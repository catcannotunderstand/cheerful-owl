#!/bin/bash
# Google Cloud Shell で実行する。音声の置き場所(バケット)と、Azure から書き込むための鍵を作る。
# 使い方: bash setup_gcs.sh <GoogleCloudのプロジェクトID>
set -euo pipefail

PROJECT="${1:?プロジェクトIDを指定してください(例: bash setup_gcs.sh my-project-123)}"
BUCKET="voicevox-radio-${PROJECT}"     # バケット名は世界で一意。重なったら変えてください
SA="voicevox-mcp"
LOCATION="us-central1"               # 無料枠(5GB)の対象はアメリカの3リージョンだけ。音声は小さいので遠くても問題ない

gcloud config set project "$PROJECT"
gcloud services enable storage.googleapis.com iam.googleapis.com

# 途中で失敗しても、もう一度実行すれば続きから進む
# 非公開のバケット(公開アクセスは禁止、7日たったファイルは自動で削除)
gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${BUCKET}" --location="$LOCATION" \
    --uniform-bucket-level-access --public-access-prevention
cat > /tmp/lifecycle.json <<'EOF'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 7}}]}
EOF
gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file=/tmp/lifecycle.json

# このバケットにだけ読み書きできるサービスアカウント
gcloud iam service-accounts describe "${SA}@${PROJECT}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA" --display-name="VOICEVOX MCP"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}@${PROJECT}.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# 鍵(期限付きURLの署名に使う)。このファイルは Azure の設定に使ったら消すこと
[ -f gcs-key.json ] && { echo "gcs-key.json がすでにあります。使い終わった古い鍵なら消してから実行してください"; exit 1; }
gcloud iam service-accounts keys create gcs-key.json \
  --iam-account="${SA}@${PROJECT}.iam.gserviceaccount.com"

echo
echo "できました。"
echo "  バケット名: ${BUCKET}"
echo "  鍵ファイル: $(pwd)/gcs-key.json  ← ダウンロードして、Azure Cloud Shell にアップロードしてください"
