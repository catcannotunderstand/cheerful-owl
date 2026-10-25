#!/bin/bash
# VOICEVOX ENGINE と中継サーバーを起動する。どちらかが止まったらコンテナごと終了させ、Azure に再起動してもらう。
set -u

gosu user /opt/voicevox_engine/run --host "${VV_HOST}" --port "${VV_PORT}" &
ENGINE_PID=$!

/app/venv/bin/python /app/server.py &
MCP_PID=$!

trap 'kill -TERM $ENGINE_PID $MCP_PID 2>/dev/null' TERM INT
wait -n $ENGINE_PID $MCP_PID
STATUS=$?
kill -TERM $ENGINE_PID $MCP_PID 2>/dev/null
wait
exit $STATUS
