#!/bin/bash
# Azure Cloud Shell (Bash) で1回だけ実行する。
# GitHub Actions が、このリポジトリの main ブランチから実行されたときだけ、
# リソースグループ voicevox-rg の中を操作できるようにする。
# パスワードや鍵は作らない(OIDC: GitHub が毎回発行する一時的な身分証明を Azure が確かめる方式)。
#
# 使い方: bash setup_github_deploy.sh
set -euo pipefail

REPO="catcannotunderstand/cheerful-owl"
RG="voicevox-rg"
NAME="cheerful-owl-deploy"

SUB=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)
az group show -n "$RG" -o none 2>/dev/null || { echo "先に deploy_azure.sh で $RG を作ってください"; exit 1; }

echo "== GitHub 用のアプリ登録(名前: $NAME)"
APP_ID=$(az ad app list --display-name "$NAME" --query "[0].appId" -o tsv)
if [ -z "$APP_ID" ]; then
  APP_ID=$(az ad app create --display-name "$NAME" --query appId -o tsv)
fi
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv 2>/dev/null || true)
if [ -z "$SP_ID" ]; then
  SP_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
fi

echo "== このリポジトリの main ブランチからだけ使えるようにする"
if [ -z "$(az ad app federated-credential list --id "$APP_ID" --query "[?name=='github-main'].name" -o tsv)" ]; then
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"github-main\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:${REPO}:ref:refs/heads/main\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" -o none
fi

echo "== 権限は $RG の中だけ(ほかのリソースには触れない)"
SCOPE="/subscriptions/${SUB}/resourceGroups/${RG}"
if [ -z "$(az role assignment list --assignee "$SP_ID" --scope "$SCOPE" --role Contributor --query "[0].id" -o tsv)" ]; then
  # 作ったばかりのアプリは反映に少し時間がかかるので、数回試す
  for i in 1 2 3 4 5 6; do
    az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
      --role Contributor --scope "$SCOPE" -o none && break
    echo "  反映待ち…($i)"; sleep 10
  done
fi

echo
echo "できました。次の3行を Claude に貼ってください(パスワードではないので貼って大丈夫です)。"
echo
echo "AZURE_CLIENT_ID=${APP_ID}"
echo "AZURE_TENANT_ID=${TENANT}"
echo "AZURE_SUBSCRIPTION_ID=${SUB}"
