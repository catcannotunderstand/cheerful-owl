"""VOICEVOX を Claude のカスタムコネクタ(リモートMCPサーバー)として使うための中継サーバー。

同じコンテナの中で動く VOICEVOX ENGINE (127.0.0.1:50021) に段落ごとに合成を頼み、
1本のMP3にまとめて Google Cloud Storage に置き、期限付きのダウンロードURLを返す。
各段落の開始秒(頭出し位置)も一緒に返す。

ツール:
  list_speakers    話者(キャラクター)とスタイルIDの一覧
  start_synthesis  合成ジョブを始める(すぐ job_id を返す)
  get_synthesis    ジョブの進み具合と結果(URL・段落ごとの開始秒・長さ)

環境変数:
  MCP_SECRET         必須。MCPの入口のURLに入れる秘密の文字列(/mcp/<MCP_SECRET>)
  STORAGE            gcs(既定) または local(動作確認用)
  GCS_BUCKET         保存先のバケット名(STORAGE=gcs のとき必須)
  GCS_SA_KEY_JSON    サービスアカウントの鍵(JSONの中身そのもの)
  GCS_PREFIX         保存先の接頭辞(既定 voicevox/)
  URL_HOURS          ダウンロードURLの有効時間(既定 6)
  LOCAL_DIR          STORAGE=local のときの保存先(既定 /tmp/voicevox-out)
  VOICEVOX_URL       既定 http://127.0.0.1:50021
  PORT               既定 8080
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from typing import Optional

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

log = logging.getLogger("voicevox-mcp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021").rstrip("/")
MCP_SECRET = os.getenv("MCP_SECRET", "")
STORAGE = os.getenv("STORAGE", "gcs")
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
GCS_PREFIX = os.getenv("GCS_PREFIX", "voicevox/")
URL_HOURS = float(os.getenv("URL_HOURS", "6"))
LOCAL_DIR = os.getenv("LOCAL_DIR", "/tmp/voicevox-out")
PORT = int(os.getenv("PORT", "8080"))

MAX_TOTAL_CHARS = 30000      # 1ジョブの文字数の上限(午後対策の回は約4,000字)
MAX_PARAGRAPHS = 400
SENTENCE_MAX = 120           # これより長い文は読点で分けて合成する

if not re.fullmatch(r"[A-Za-z0-9_-]{24,}", MCP_SECRET):
    raise SystemExit("MCP_SECRET に24文字以上の英数字(と - _)を設定してください")


# ---------------------------------------------------------------- 保存先

class Storage:
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> Optional[bytes]: ...
    def url(self, key: str) -> str: ...


class GCSStorage(Storage):
    def __init__(self) -> None:
        from google.cloud import storage as gcs
        from google.oauth2 import service_account

        if not GCS_BUCKET:
            raise SystemExit("GCS_BUCKET を設定してください")
        key_json = os.getenv("GCS_SA_KEY_JSON", "")
        if key_json:
            creds = service_account.Credentials.from_service_account_info(json.loads(key_json))
            client = gcs.Client(project=creds.project_id, credentials=creds)
        else:  # 鍵の指定がなければ GOOGLE_APPLICATION_CREDENTIALS などの既定の認証
            client = gcs.Client()
        self.bucket = client.bucket(GCS_BUCKET)

    def put(self, key, data, content_type):
        self.bucket.blob(GCS_PREFIX + key).upload_from_string(data, content_type=content_type)

    def get(self, key):
        blob = self.bucket.blob(GCS_PREFIX + key)
        return blob.download_as_bytes() if blob.exists() else None

    def url(self, key):
        return self.bucket.blob(GCS_PREFIX + key).generate_signed_url(
            version="v4", expiration=dt.timedelta(hours=URL_HOURS), method="GET")


class LocalStorage(Storage):
    def __init__(self) -> None:
        os.makedirs(LOCAL_DIR, exist_ok=True)

    def _p(self, key):
        return os.path.join(LOCAL_DIR, key.replace("/", "_"))

    def put(self, key, data, content_type):
        with open(self._p(key), "wb") as f:
            f.write(data)

    def get(self, key):
        p = self._p(key)
        return open(p, "rb").read() if os.path.exists(p) else None

    def url(self, key):
        return "file://" + self._p(key)


store: Storage = GCSStorage() if STORAGE == "gcs" else LocalStorage()


# ---------------------------------------------------------------- VOICEVOX

http = httpx.Client(base_url=VOICEVOX_URL, timeout=httpx.Timeout(300, connect=10))
_speakers_cache: Optional[list] = None


def wait_engine(timeout: float = 180) -> None:
    """コンテナ起動直後は VOICEVOX がモデルを読み込み中なので、応答するまで待つ。"""
    end = time.time() + timeout
    while True:
        try:
            if http.get("/version", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.time() > end:
            raise RuntimeError("VOICEVOX ENGINE が起動しません。しばらくしてからやり直してください")
        time.sleep(2)


def speakers() -> list:
    global _speakers_cache
    if _speakers_cache is None:
        wait_engine(90)
        r = http.get("/speakers")
        r.raise_for_status()
        _speakers_cache = r.json()
    return _speakers_cache


def style_owner(style_id: int) -> Optional[str]:
    for sp in speakers():
        for st in sp.get("styles", []):
            if st.get("id") == style_id:
                return f'{sp["name"]}({st["name"]})'
    return None


def split_sentences(par: str) -> list[str]:
    """段落を文に分ける。長すぎる文は読点で分ける。合成の単位を小さくして失敗と待ち時間を減らす。"""
    parts = [s for s in re.split(r"(?<=[。！？!?])", par) if s.strip()]
    out: list[str] = []
    for s in parts:
        if len(s) <= SENTENCE_MAX:
            out.append(s)
            continue
        buf = ""
        for piece in re.split(r"(?<=[、，,])", s):
            if buf and len(buf) + len(piece) > SENTENCE_MAX:
                out.append(buf)
                buf = ""
            buf += piece
        if buf:
            out.append(buf)
    return out or [par]


def synth_pcm(text: str, style_id: int, params: dict) -> tuple[bytes, int]:
    """1文を合成して (16bitモノラルPCM, サンプリング周波数) を返す。"""
    for attempt in range(3):
        try:
            q = http.post("/audio_query", params={"text": text, "speaker": style_id})
            q.raise_for_status()
            query = q.json()
            query.update(params)
            w = http.post("/synthesis", params={"speaker": style_id}, json=query)
            w.raise_for_status()
            with wave.open(io.BytesIO(w.content)) as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                    raise RuntimeError("想定外のWAV形式です")
                return wf.readframes(wf.getnframes()), wf.getframerate()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            if attempt == 2:
                raise RuntimeError(f"VOICEVOXでの合成に失敗しました: {e}") from e
            time.sleep(2 * (attempt + 1))
    raise AssertionError


def to_mp3(pcm: bytes, rate: int, bitrate: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "a.wav"), os.path.join(d, "a.mp3")
        with wave.open(src, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-codec:a", "libmp3lame",
                        "-b:a", bitrate, dst], check=True)
        return open(dst, "rb").read()


# ---------------------------------------------------------------- ジョブ

jobs: dict[str, dict] = {}
work_lock = threading.Lock()   # CPUを取り合わないよう、合成は1ジョブずつ


def run_job(job_id: str, req: dict) -> None:
    job = jobs[job_id]
    try:
        with work_lock:
            job["status"] = "running"
            wait_engine()
            style_id = req["speaker"]
            params = {k: v for k, v in {
                "speedScale": req["speed_scale"], "pitchScale": req["pitch_scale"],
                "intonationScale": req["intonation_scale"], "volumeScale": req["volume_scale"],
                "prePhonemeLength": 0.1, "postPhonemeLength": 0.1,
            }.items()}
            units = [split_sentences(p) for p in req["paragraphs"]]
            job["total"] = sum(len(u) for u in units)
            pcm = bytearray()
            rate = None
            offsets: list[float] = []
            for i, sentences in enumerate(units):
                if rate is not None:
                    offsets.append(round(len(pcm) / 2 / rate, 3))
                for s in sentences:
                    chunk, r = synth_pcm(s, style_id, params)
                    if rate is None:
                        rate = r
                        offsets.append(0.0)
                    elif r != rate:
                        raise RuntimeError("サンプリング周波数が途中で変わりました")
                    pcm += chunk
                    job["done"] += 1
                gap = req["pause_seconds"] + float(req["extra_pause_after"].get(str(i), 0))
                if i < len(units) - 1 or str(i) in req["extra_pause_after"]:
                    pcm += b"\x00\x00" * int(rate * gap)
            duration = round(len(pcm) / 2 / rate, 3)
            job["status"] = "encoding"
            mp3 = to_mp3(bytes(pcm), rate, req["bitrate"])
            key = f"{job_id}.mp3"
            store.put(key, mp3, "audio/mpeg")
            owner = style_owner(style_id)
            result = {
                "status": "done", "job_id": job_id, "title": req["title"],
                "audio_key": key, "bytes": len(mp3), "duration": duration,
                "paragraph_starts": offsets, "speaker": style_id,
                "credit": f"VOICEVOX:{owner.split('(')[0]}" if owner else "VOICEVOX",
                "voice": owner,
            }
            store.put(f"{job_id}.json", json.dumps(result, ensure_ascii=False).encode(), "application/json")
            job.update(result)
            log.info("job %s done: %.1fs, %d bytes", job_id, duration, len(mp3))
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        job.update(status="failed", error=str(e))


# ---------------------------------------------------------------- MCP

mcp = FastMCP(
    "VOICEVOX",
    instructions=(
        "日本語の音声合成(VOICEVOX)。長い文章は start_synthesis で段落の配列を渡してジョブを始め、"
        "get_synthesis を数十秒おきに呼んで完了を待つ。完了すると MP3 のダウンロードURL(期限付き)と、"
        "各段落の開始秒(paragraph_starts)、全体の長さ(duration)が返る。"
        "音声を公開するときは credit の表記(例: VOICEVOX:ずんだもん)を添えること。"
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path=f"/mcp/{MCP_SECRET}",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
async def list_speakers() -> list[dict]:
    """使える話者(キャラクター)とスタイルの一覧。start_synthesis の speaker にはスタイルの id を渡す。"""
    return await asyncio.to_thread(_list_speakers)


def _list_speakers() -> list[dict]:
    """使える話者(キャラクター)とスタイルの一覧。start_synthesis の speaker にはスタイルの id を渡す。"""
    return [{"name": sp["name"],
             "styles": [{"name": st["name"], "id": st["id"]} for st in sp.get("styles", [])
                        if st.get("type", "talk") == "talk"]}
            for sp in speakers()]


@mcp.tool()
async def start_synthesis(
    paragraphs: list[str],
    speaker: int,
    title: str = "",
    speed_scale: float = 1.0,
    pitch_scale: float = 0.0,
    intonation_scale: float = 1.0,
    volume_scale: float = 1.0,
    pause_seconds: float = 0.6,
    extra_pause_after: Optional[dict[str, float]] = None,
    bitrate: str = "128k",
) -> dict:
    """段落の配列を1本のMP3に合成するジョブを始め、すぐに job_id を返す。

    paragraphs: 読み上げる段落(順番どおり)。段落ごとの開始秒が結果の paragraph_starts になる。
    speaker: list_speakers のスタイルid。
    speed_scale: 話速(1.0が標準、1.1〜1.2で少し速め)。pitch_scale: 音高(0が標準、±0.15程度)。
    intonation_scale: 抑揚(1.0が標準)。volume_scale: 音量。
    pause_seconds: 段落と段落の間の無音(秒)。
    extra_pause_after: 段落の番号(0始まり、文字列)→追加の無音(秒)。
        例 {"3": 5} で4番目の段落のあとに5秒足す(クイズの考える時間など)。
    bitrate: MP3のビットレート(64k〜192k)。
    """
    paragraphs = [p.strip() for p in paragraphs if p and p.strip()]
    if not paragraphs:
        raise ValueError("paragraphs が空です")
    if len(paragraphs) > MAX_PARAGRAPHS or sum(map(len, paragraphs)) > MAX_TOTAL_CHARS:
        raise ValueError(f"長すぎます(段落{MAX_PARAGRAPHS}個・{MAX_TOTAL_CHARS}文字まで)")
    if await asyncio.to_thread(style_owner, speaker) is None:
        raise ValueError(f"speaker={speaker} は見つかりません。list_speakers で確かめてください")
    if not re.fullmatch(r"(64|96|128|160|192)k", bitrate):
        raise ValueError("bitrate は 64k, 96k, 128k, 160k, 192k のどれか")
    extra = {str(int(k)): max(0.0, min(30.0, float(v))) for k, v in (extra_pause_after or {}).items()}
    job_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    jobs[job_id] = {"status": "queued", "done": 0, "total": None, "created": time.time()}
    req = dict(paragraphs=paragraphs, speaker=speaker, title=title,
               speed_scale=speed_scale, pitch_scale=pitch_scale,
               intonation_scale=intonation_scale, volume_scale=volume_scale,
               pause_seconds=max(0.0, min(5.0, pause_seconds)), extra_pause_after=extra,
               bitrate=bitrate)
    asyncio.get_running_loop().run_in_executor(None, run_job, job_id, req)
    chars = sum(map(len, paragraphs))
    return {"job_id": job_id, "status": "queued", "paragraphs": len(paragraphs), "chars": chars,
            "poll_after_seconds": 20,
            "hint": "get_synthesis(job_id) を poll_after_seconds おきに呼んで完了を待つ"}


@mcp.tool()
def get_synthesis(job_id: str) -> dict:
    """ジョブの状態。完了(status=done)なら audio_url(期限付き)、duration、paragraph_starts、credit を返す。"""
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}", job_id):
        raise ValueError("job_id の形式が違います")
    job = jobs.get(job_id)
    if job is None:  # サーバーが再起動していても、完了済みなら保存先から結果を読む
        raw = store.get(f"{job_id}.json")
        if raw is None:
            return {"job_id": job_id, "status": "not_found",
                    "hint": "サーバーが合成中に再起動した可能性があります。start_synthesis からやり直してください"}
        job = json.loads(raw)
    out = {k: v for k, v in job.items() if k not in ("created",)}
    out["job_id"] = job_id
    if job.get("status") == "done":
        out["audio_url"] = store.url(job["audio_key"])
        out["audio_url_expires_in_hours"] = URL_HOURS
    elif job.get("status") in ("queued", "running", "encoding"):
        out["poll_after_seconds"] = 20
    return out


# ---------------------------------------------------------------- 起動

app = mcp.streamable_http_app()


async def health(_: Request):
    try:
        r = await asyncio.to_thread(http.get, "/version")
        ok = r.status_code == 200
    except httpx.HTTPError:
        ok = False
    return JSONResponse({"mcp": "ok", "voicevox": "ok" if ok else "starting"}, status_code=200 if ok else 503)


async def root(_: Request):
    return PlainTextResponse("ok")


app.add_route("/healthz", health)
app.add_route("/", root)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info", access_log=False)
