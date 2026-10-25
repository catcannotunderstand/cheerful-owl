# VOICEVOX ENGINE (CPU版) に、Claude のカスタムコネクタ用の中継サーバーを同居させたイメージ。
# 外に開くのは 8080 番(中継サーバー)だけ。VOICEVOX 自体は 127.0.0.1:50021 で動き、外からは届かない。
ARG BASE_IMAGE=voicevox/voicevox_engine:cpu-latest
FROM ${BASE_IMAGE}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /app/venv \
 && /app/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY server.py start.sh ./
RUN chmod 755 start.sh

ENV VV_HOST=127.0.0.1 \
    VV_PORT=50021 \
    VV_DISABLE_MUTABLE_API=1 \
    PORT=8080
EXPOSE 8080

ENTRYPOINT ["/app/start.sh"]
