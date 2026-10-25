"""動作確認用の偽VOICEVOX。文字数に比例した長さのサイン波を返す(1文字=0.1秒、前後0.1秒ずつ無音)。"""
import io
import math
import struct
import wave

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

RATE = 24000


async def version(_):
    return JSONResponse("0.0.0-fake")


async def speakers(_):
    return JSONResponse([
        {"name": "ずんだもん", "speaker_uuid": "x", "styles": [{"name": "ノーマル", "id": 3, "type": "talk"}]},
        {"name": "青山龍星", "speaker_uuid": "y", "styles": [{"name": "ノーマル", "id": 13, "type": "talk"}]},
    ])


async def audio_query(req: Request):
    text = req.query_params["text"]
    return JSONResponse({"text": text, "speedScale": 1, "pitchScale": 0, "intonationScale": 1,
                         "volumeScale": 1, "prePhonemeLength": 0.1, "postPhonemeLength": 0.1,
                         "outputSamplingRate": RATE, "outputStereo": False, "accent_phrases": []})


async def synthesis(req: Request):
    q = await req.json()
    n_voice = int(len(q["text"]) * 0.1 / q["speedScale"] * RATE)
    pre, post = int(q["prePhonemeLength"] * RATE), int(q["postPhonemeLength"] * RATE)
    frames = [0] * pre + [int(8000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(n_voice)] + [0] * post
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(frames)}h", *frames))
    return Response(buf.getvalue(), media_type="audio/wav")


app = Starlette(routes=[Route("/version", version), Route("/speakers", speakers),
                        Route("/audio_query", audio_query, methods=["POST"]),
                        Route("/synthesis", synthesis, methods=["POST"])])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=50021, log_level="warning")
