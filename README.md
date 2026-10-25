# VOICEVOX コネクタ

VOICEVOX を Azure で動かし、Claude のカスタムコネクタとして登録する。登録後は、ElevenLabs と同じように Claude が直接音声を作れる。

```
GitHub (main にプッシュ) ──Actions──▶ ghcr.io にイメージ
                                          │
Claude ──(コネクタ)──▶ Azure Container Apps ┬ 中継サーバー(8080番、外に開くのはここだけ)
                                           └ VOICEVOX ENGINE(127.0.0.1:50021)
                              │ MP3を保存
                              ▼
                      Google Cloud Storage(非公開。期限付きURLでだけ取り出せる)──▶ Claude が取得
```

音声の置き場所が Google Cloud Storage なのは、Claude の作業環境から取りに行けるストレージがここだけだから。

## 構築手順

作業はすべてブラウザの中(Google Cloud Shell と Azure Cloud Shell)でできる。PC のブラウザのほうが楽。

### 0. イメージを公開にする(1回だけ)

GitHub のプロフィール → Packages → `cheerful-owl` → Package settings → Change visibility → **Public**。
コードにもイメージにも秘密の情報は入っていない。公開にすると Azure が認証なしでイメージを取れる。

### 1. Google Cloud:音声の置き場所を作る(約5分)

1. https://console.cloud.google.com でプロジェクトを1つ作り(既存でもよい)、プロジェクトIDを控える。
2. 右上の Cloud Shell(`>_`)を開いて実行する。
   ```
   git clone https://github.com/catcannotunderstand/cheerful-owl && cd cheerful-owl
   bash setup_gcs.sh <プロジェクトID>
   ```
3. 表示された**バケット名**を控え、`gcs-key.json` をダウンロードする(Cloud Shell の「︙」→「ダウンロード」→ `cheerful-owl/gcs-key.json`)。

> 「鍵の作成が組織のポリシーで禁止されている」と出た場合は、組織に属するプロジェクトの可能性がある。個人のアカウントで新しいプロジェクトを作ってやり直す。

### 2. Azure:コンテナを動かす(約10分)

1. https://portal.azure.com 上部の Cloud Shell(`>_`)を開き、**Bash** を選ぶ。
2. 「ファイルの管理」→「アップロード」で `gcs-key.json` を上げてから実行する。
   ```
   git clone https://github.com/catcannotunderstand/cheerful-owl && cd cheerful-owl
   bash deploy_azure.sh <バケット名> ~/gcs-key.json
   rm ~/gcs-key.json
   ```
3. 最後に `https://〜/mcp/〜` のURLが表示される。**これはパスワードと同じなので人に見せない。**

### 3. claude.ai に登録する

1. claude.ai(Web)→ 設定 → コネクタ → 「カスタムコネクタを追加」
2. 名前は `VOICEVOX`、URL は手順2で表示されたもの。認証の設定は空のまま。
3. チャットのツール一覧で VOICEVOX をオンにする。

## 更新するとき

1. Claude がこのリポジトリにプッシュすると、GitHub Actions が新しいイメージを作る(数分)。
2. Azure Cloud Shell で入れ替える:
   ```
   cd cheerful-owl && git pull && bash deploy_azure.sh <バケット名>
   ```
   URLの秘密の文字列は `~/.voicevox-mcp.env` に保存されていて変わらない。

## 守りの設定

- 入口のURLに **40文字のランダムな文字列** が入っていて、知らない人は呼べない。
- Azure の通信制限で、**Anthropic の IP 範囲(160.79.104.0/21)からしか届かない**。自分のブラウザで URL を開いて 403 になるのは正常。
- VOICEVOX 本体は外から届かない。辞書の書き換えなどの API も止めてある。
- 音声は非公開のバケットに置かれ、6時間で切れる URL でしか取り出せない。7日たったファイルは自動で消える。
- Google の鍵は Azure のシークレットにだけ保存される。チャットには貼らない。

## 費用の目安

- Azure Container Apps:使っていないときは0台に縮む設定。毎月の無料枠に収まる見込み。
- GitHub(Actions・ghcr.io):公開リポジトリなら無料。
- Google Cloud Storage:7日で消えるので、ほぼ0円。

使わなくなったら Azure Cloud Shell で `az group delete -n voicevox-rg` を実行すれば Azure 側は全部消える。

## ツール(Claude から見たもの)

| ツール | 内容 |
|---|---|
| `list_speakers` | 話者とスタイルIDの一覧 |
| `start_synthesis` | 段落の配列を1本のMP3にするジョブを始める。段落の間の無音、話速、抑揚、指定した段落のあとの追加の無音(クイズの考える時間)を指定できる |
| `get_synthesis` | 進み具合と結果。完了すると、MP3のURL・全体の長さ・**各段落の開始秒**・クレジット表記が返る |

段落ごとに合成してつなぐので、頭出しの位置は計算どおりに正確に決まる(文字起こしや無音の検出が要らない)。
最初の1回は、コンテナの起動と VOICEVOX のモデル読み込みで1〜2分待つことがある。

## ファイル

| ファイル | 内容 |
|---|---|
| `server.py` | 中継サーバー(MCP) |
| `Dockerfile`, `start.sh`, `requirements.txt` | VOICEVOX ENGINE のイメージに中継サーバーを同居させる |
| `.github/workflows/build.yml` | main へのプッシュでイメージを作って ghcr.io に置く |
| `setup_gcs.sh` | Google Cloud のバケットと鍵を作る |
| `deploy_azure.sh` | Azure Container Apps に配置する |
| `test/` | 偽の VOICEVOX を使った動作確認 |
