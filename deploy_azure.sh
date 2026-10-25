#!/bin/bash
# Azure Cloud Shell (Bash) で、このフォルダの中から実行する。
# 使い方: bash deploy_azure.sh <バケット名> <鍵ファイルのパス>
# 例:     bash deploy_azure.sh voicevox-radio-my-project-123 ~/gcs-key.json
# 2回目以降(プログラムの更新だけ)は鍵ファイルを省略できる: bash deploy_azure.sh <バケット名>
set -euo pipefail

BUCKET="${1:?バケット名を指定してください}"
KEYFILE="${2:-}"
if [ -n "$KEYFILE" ]; then
  jq -e .private_key "$KEYFILE" >/dev/null || { echo "鍵ファイルが読めません: $KEYFILE"; exit 1; }
fi
RG="voicevox-rg"
LOC="japaneast"
ENV_NAME="voicevox-env"
APP="voicevox"
ANTHROPIC_IPS="160.79.104.0/21"   # Claude のコネクタからの通信元(Anthropic公開のIP範囲)

# 2回目以降の実行でも同じレジストリ名と秘密の文字列を使う
STATE="$HOME/.voicevox-mcp.env"
[ -f "$STATE" ] && . "$STATE"
ACR="${ACR:-voicevox$(tr -dc a-z0-9 </dev/urandom | head -c 8)}"
MCP_SECRET="${MCP_SECRET:-$(tr -dc A-Za-z0-9 </dev/urandom | head -c 40)}"
printf 'ACR=%s\nMCP_SECRET=%s\n' "$ACR" "$MCP_SECRET" > "$STATE"; chmod 600 "$STATE"
TAG="v$(date +%Y%m%d%H%M%S)"

echo "== 準備"
az extension add -n containerapp --upgrade -y >/dev/null
for p in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry; do
  az provider register -n "$p" --wait >/dev/null
done
az group create -n "$RG" -l "$LOC" -o none

echo "== イメージを作る(Azure上でビルド。10分ほどかかります)"
az acr show -n "$ACR" -g "$RG" -o none 2>/dev/null || \
  az acr create -n "$ACR" -g "$RG" --sku Basic --admin-enabled true -o none
az acr build -r "$ACR" -t "voicevox-mcp:${TAG}" .
ACR_USER=$(az acr credential show -n "$ACR" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR" --query "passwords[0].value" -o tsv)
IMAGE="${ACR}.azurecr.io/voicevox-mcp:${TAG}"

echo "== コンテナを動かす"
az containerapp env show -n "$ENV_NAME" -g "$RG" -o none 2>/dev/null || \
  az containerapp env create -n "$ENV_NAME" -g "$RG" -l "$LOC" -o none

SECRETS=(mcp-secret="$MCP_SECRET")
[ -n "$KEYFILE" ] && SECRETS+=(gcs-key="$(jq -c . "$KEYFILE")")
ENVS=(MCP_SECRET=secretref:mcp-secret GCS_SA_KEY_JSON=secretref:gcs-key GCS_BUCKET="$BUCKET"
      STORAGE=gcs URL_HOURS=6 VV_CPU_NUM_THREADS=2)

if az containerapp show -n "$APP" -g "$RG" -o none 2>/dev/null; then
  az containerapp secret set -n "$APP" -g "$RG" --secrets "${SECRETS[@]}" -o none
  az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" --set-env-vars "${ENVS[@]}" -o none
else
  [ -n "$KEYFILE" ] || { echo "初回は鍵ファイル(gcs-key.json)のパスも指定してください"; exit 1; }
  az containerapp create -n "$APP" -g "$RG" --environment "$ENV_NAME" \
    --image "$IMAGE" --registry-server "${ACR}.azurecr.io" \
    --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --secrets "${SECRETS[@]}" --env-vars "${ENVS[@]}" \
    --cpu 2 --memory 4Gi --min-replicas 0 --max-replicas 1 \
    --ingress external --target-port 8080 -o none
fi

echo "== Claude からの通信だけを通す"
az containerapp ingress access-restriction set -n "$APP" -g "$RG" \
  --rule-name anthropic --ip-address "$ANTHROPIC_IPS" --action Allow \
  --description "Claude connectors" -o none

FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
echo
echo "できました。claude.ai の 設定 → コネクタ → カスタムコネクタを追加 に、次のURLを登録してください。"
echo "(このURLはパスワードと同じです。人に見せないでください)"
echo
echo "  https://${FQDN}/mcp/${MCP_SECRET}"
echo
[ -n "$KEYFILE" ] && echo "鍵ファイルはもう不要です: rm ${KEYFILE}"
true
