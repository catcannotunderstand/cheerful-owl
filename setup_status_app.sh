#!/bin/bash
# Azure Cloud Shell (Bash) で1回だけ実行する。
# Azure の状態とコストを閲覧するためのコネクタ(アプリ voicevox-status)を作る。
#
#   - アプリにマネージドID(パスワード不要の身分証明)を付ける
#   - そのIDに与える権限は2つだけ:
#       voicevox-rg の「閲覧者」(コンテナの状態を見るため)
#       サブスクリプションの「コスト管理の閲覧者」(コストと予算を見るため。リソースの中身は見られない)
#   - 外からの入口は Claude の IP 範囲だけ+URLの秘密の文字列(VOICEVOX と同じ)
#
# 使い方: bash setup_status_app.sh
set -euo pipefail

RG="voicevox-rg"
ENV_NAME="voicevox-env"
APP="voicevox-status"
IMAGE="ghcr.io/catcannotunderstand/cheerful-owl:status-latest"
ANTHROPIC_IPS="160.79.104.0/21"

az config set extension.use_dynamic_install=yes_without_prompt >/dev/null 2>&1 || true
echo "== 準備(拡張機能。1〜2分)"
az extension add -n containerapp --upgrade -y </dev/null

SUB=$(az account show --query id -o tsv)

# 秘密の文字列(2回目以降は今のものを使い続ける)
SECRET=$(az containerapp secret show -n "$APP" -g "$RG" --secret-name mcp-secret --query value -o tsv </dev/null 2>/dev/null || true)
SECRET="${SECRET:-$(openssl rand -hex 20)}"

echo "== アプリ"
if ! az containerapp show -n "$APP" -g "$RG" -o none 2>/dev/null; then
  az containerapp create -n "$APP" -g "$RG" --environment "$ENV_NAME" \
    --image "$IMAGE" --system-assigned \
    --secrets mcp-secret="$SECRET" \
    --env-vars MCP_SECRET=secretref:mcp-secret SUBSCRIPTION_ID="$SUB" RESOURCE_GROUP="$RG" \
               TARGET_APPS="voicevox,voicevox-status" \
    --cpu 0.25 --memory 0.5Gi --min-replicas 0 --max-replicas 1 \
    --ingress external --target-port 8080 -o none
else
  az containerapp identity assign -n "$APP" -g "$RG" --system-assigned -o none
fi
PRINCIPAL=$(az containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)

echo "== 権限(閲覧だけ)"
grant() {  # $1=ロール $2=範囲
  if [ -z "$(az role assignment list --assignee "$PRINCIPAL" --role "$1" --scope "$2" --query '[0].id' -o tsv)" ]; then
    for i in 1 2 3 4 5 6; do
      az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
        --role "$1" --scope "$2" -o none && return 0
      echo "  反映待ち…($i)"; sleep 10
    done
    return 1
  fi
}
grant "Reader" "/subscriptions/${SUB}/resourceGroups/${RG}"
grant "Cost Management Reader" "/subscriptions/${SUB}"

echo "== Claude からの通信だけを通す"
az containerapp ingress access-restriction set -n "$APP" -g "$RG" \
  --rule-name anthropic --ip-address "$ANTHROPIC_IPS" --action Allow \
  --description "Claude connectors" -o none

FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
echo
echo "できました。claude.ai の 設定 → コネクタ → カスタムコネクタを追加 に、名前「Azure状況」で次のURLを登録してください。"
echo "(このURLはパスワードと同じです。人に見せないでください)"
echo
echo "  https://${FQDN}/mcp/${SECRET}"
echo
echo "権限の反映に数分かかることがあります。最初に「権限がありません」と出たら、少し待ってからやり直してください。"
