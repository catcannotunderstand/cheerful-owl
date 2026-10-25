#!/bin/bash
# Azure Cloud Shell (Bash) で実行する。GitHub Actions が ghcr.io に作ったイメージを Azure Container Apps で動かす。
#
# 初回:   bash deploy_azure.sh <バケット名> <鍵ファイルのパス>
#         例) bash deploy_azure.sh voicevox-radio-my-project-123 ~/gcs-key.json
# 更新:   bash deploy_azure.sh <バケット名>      (GitHub の最新のイメージに入れ替える。鍵ファイルは不要)
set -euo pipefail

BUCKET="${1:?バケット名を指定してください}"
KEYFILE="${2:-}"
if [ -n "$KEYFILE" ]; then
  jq -e .private_key "$KEYFILE" >/dev/null || { echo "鍵ファイルが読めません: $KEYFILE"; exit 1; }
fi
IMAGE="${IMAGE:-ghcr.io/catcannotunderstand/cheerful-owl:latest}"
RG="voicevox-rg"
LOC="japaneast"
ENV_NAME="voicevox-env"
APP="voicevox"
ANTHROPIC_IPS="160.79.104.0/21"   # Claude のコネクタからの通信元(Anthropic公開のIP範囲)

# 秘密の文字列は初回に作って保存し、2回目以降も同じものを使う(URLが変わらないように)
STATE="$HOME/.voicevox-mcp.env"
# shellcheck disable=SC1090
[ -f "$STATE" ] && . "$STATE"
# Cloud Shell のファイルが消えていても(ストレージなしのセッションなど)、Azure に登録済みの値を取り戻す
if [ -z "${MCP_SECRET:-}" ]; then
  MCP_SECRET=$(az containerapp secret show -n "$APP" -g "$RG" --secret-name mcp-secret \
                 --query value -o tsv 2>/dev/null || true)
fi
MCP_SECRET="${MCP_SECRET:-$(tr -dc A-Za-z0-9 </dev/urandom | head -c 40)}"
printf 'MCP_SECRET=%s\n' "$MCP_SECRET" > "$STATE"; chmod 600 "$STATE"

echo "== 準備"
az extension add -n containerapp --upgrade -y >/dev/null 2>&1
for p in Microsoft.App Microsoft.OperationalInsights; do
  az provider register -n "$p" --wait >/dev/null
done
az group create -n "$RG" -l "$LOC" -o none

# ログの保存先(Log Analytics)は作らない。保存に料金がかかるため。画面の「ログストリーム」でリアルタイムの出力は見られる
echo "== 実行環境(初回は数分かかります)"
az containerapp env show -n "$ENV_NAME" -g "$RG" -o none 2>/dev/null || \
  az containerapp env create -n "$ENV_NAME" -g "$RG" -l "$LOC" --logs-destination none -o none

SECRETS=(mcp-secret="$MCP_SECRET")
[ -n "$KEYFILE" ] && SECRETS+=(gcs-key="$(jq -c . "$KEYFILE")")
ENVS=(MCP_SECRET=secretref:mcp-secret GCS_SA_KEY_JSON=secretref:gcs-key GCS_BUCKET="$BUCKET"
      STORAGE=gcs URL_HOURS=6 VV_CPU_NUM_THREADS=2)

echo "== コンテナ"
if az containerapp show -n "$APP" -g "$RG" -o none 2>/dev/null; then
  az containerapp secret set -n "$APP" -g "$RG" --secrets "${SECRETS[@]}" -o none
  # revision-suffix を毎回変えて、:latest でも新しいイメージを取り直させる
  az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" --set-env-vars "${ENVS[@]}" \
    --revision-suffix "r$(date +%m%d%H%M%S)" -o none
else
  [ -n "$KEYFILE" ] || { echo "初回は鍵ファイル(gcs-key.json)のパスも指定してください"; exit 1; }
  az containerapp create -n "$APP" -g "$RG" --environment "$ENV_NAME" \
    --image "$IMAGE" \
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
