"""MCPクライアントとして中継サーバーを呼び、頭出し位置・無音・長さが計算どおりか確かめる。"""
import asyncio
import json
import re
import subprocess
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1]
sys.path.insert(0, sys.argv[2])  # server.py の split_sentences を使って期待値を計算する
import importlib.util  # noqa: E402

PARAS = [
    "支援士ラジオ、午後対策、基礎固めコーナー、第24回、多要素認証とパスキー。こんにちは、支援士ラジオの時間です。",
    "**今日のテーマ**",
    "知識、所持、生体の三つの要素のうち、異なる二つ以上を組み合わせるのが多要素認証です。" * 3,
    "第1問。ワンタイムパスワードはどの要素でしょうか。考えてみてください。",
    "答えは所持です。それでは、また次回。",
]


def expected(paras, pause, extra, split):
    t, starts = 0.0, []
    for i, p in enumerate(paras):
        starts.append(round(t, 3))
        for s in split(p):
            t += int(len(s) * 0.1 * 24000) / 24000 + 0.2
        if i < len(paras) - 1 or str(i) in extra:
            t += pause + extra.get(str(i), 0)
    return starts, t


async def main():
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", tools)
            sp = await s.call_tool("list_speakers", {})
            print("speakers:", sp.content[0].text[:120])
            bad = await s.call_tool("start_synthesis", {"paragraphs": ["あ"], "speaker": 999})
            print("bad speaker -> isError:", bad.isError, bad.content[0].text[:80])
            res = await s.call_tool("start_synthesis", {"paragraphs": PARAS, "speaker": 3, "title": "第24回",
                                                        "extra_pause_after": {"3": 5}})
            job = json.loads(res.content[0].text)
            print("start:", job)
            while True:
                await asyncio.sleep(0.5)
                st = json.loads((await s.call_tool("get_synthesis", {"job_id": job["job_id"]})).content[0].text)
                if st["status"] not in ("queued", "running", "encoding"):
                    break
            print("result:", json.dumps({k: st[k] for k in st if k != "audio_url"}, ensure_ascii=False))
            return st


st = asyncio.run(main())
spec = importlib.util.spec_from_file_location("srv", sys.argv[2] + "/server.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
exp_starts, exp_dur = expected([p.strip() for p in PARAS], 0.6, {"3": 5.0}, mod.split_sentences)
path = st["audio_url"].removeprefix("file://")
probe = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                       "-of", "csv=p=0", path]).decode())
print("expected starts:", exp_starts)
print("got starts:     ", st["paragraph_starts"])
print(f"expected dur {exp_dur:.3f} / reported {st['duration']} / mp3 {probe:.3f}")
assert all(abs(a - b) < 0.01 for a, b in zip(exp_starts, st["paragraph_starts"]))
assert abs(exp_dur - st["duration"]) < 0.01 and abs(probe - st["duration"]) < 0.1
# 5秒の無音が第4段落の後にあるか
sil = subprocess.run(["ffmpeg", "-i", path, "-af", "silencedetect=noise=-40dB:d=3", "-f", "null", "-"],
                     capture_output=True, text=True).stderr
print("silences >=3s:", re.findall(r"silence_duration: ([\d.]+)", sil))
print("ALL OK, credit =", st["credit"])
