# VOICEVOX コネクタ 構築手順

VOICEVOX を Azure で動かし、Claude のカスタムコネクタとして登録する。登録後は、ElevenLabs と同じように Claude が直接音声を作れる。

```
Claude ──(コネクタ)──▶ Azure Container Apps ┬ 中継サーバー(8080番、外に開くのはここだけ)
                                            └ VOICEVOX ENGINE(127.0.0.1:50021)
                              │ MP3を保存
                              ▼
                      Google Cloud Storage(非公開。期限付きURLでだけ取り出せる)──▶ Claude が取得
```

音声の置き場所が Google Cloud Storage なのは、Claude の作業環境から取りに行けるストレージがここだけだから。

## 必要なもの

- Google Cloud のアカウント(無料枠で足りる)
- Azure のアカウント
- claude.ai(カスタムコネクタは Web 版からしか追加できない。スマホアプリでは追加済みのものを使える)

作業はすべてブラウザの中(Google Cloud Shell と Azure Cloud Shell)でできる。PC のブラウザのほうが楽。

## 1. Google Cloud:音声の置き場所を作る(約5分)

1. https://console.cloud.google.com を開き、プロジェクトを1つ作る(既存のものでもよい)。プロジェクトIDを控える。
2. 右上の「Cloud Shell をアクティブにする」(`>_` のアイコン)を押す。
3. このフォルダの `setup_gcs.sh` をアップロード(Cloud Shell の「︙」→「アップロード」)して実行する。
   ```
   bash setup_gcs.sh <プロジェクトID>
   ```
4. 最後に表示される**バケット名**を控え、`gcs-key.json` をダウンロードする(「︙」→「ダウンロード」)。

> 「鍵の作成が組織のポリシーで禁止されている」と出た場合は、会社などの組織に属するプロジェクトの可能性がある。個人のアカウントで新しいプロジェクトを作って、やり直してください。

## 2. Azure:コンテナを動かす(約15分)

1. https://portal.azure.com を開き、上部の Cloud Shell(`>_`)を押して **Bash** を選ぶ。
2. このフォルダ一式(zip)と `gcs-key.json` をアップロードする(「ファイルの管理」→「アップロード」)。
   ```
   unzip voicevox-mcp.zip && cd voicevox-mcp
   bash deploy_azure.sh <バケット名> ~/gcs-key.json
   ```
3. 最後に `https://〜/mcp/〜` のURLが表示される。**これはパスワードと同じなので人に見せない。**
4. 鍵ファイルを消す:`rm ~/gcs-key.json`

`az acr build` で「ACR Tasks がこのサブスクリプションでは使えない」というエラーが出たら、無料試用のサブスクリプションの制限の可能性がある。エラー文をそのまま Claude に貼ってください。別の作り方(GitHub でイメージを作る方法)に切り替える。

## 3. claude.ai に登録する

1. claude.ai(Web)→ 設定 → コネクタ → 「カスタムコネクタを追加」
2. 名前は `VOICEVOX`、URL は手順2で表示されたもの。認証の設定は空のままでよい。
3. 追加したら、チャットのツール一覧で VOICEVOX をオンにする。

## 守りの設定

- **入口のURLに40文字のランダムな文字列**が入っていて、知らない人は呼べない。
- **Azure の通信制限で、Anthropic の IP 範囲(160.79.104.0/21)からしか届かない。** 自分のブラウザで URL を開いても 403 になるのは正常。
- VOICEVOX 本体は外から届かない。辞書の書き換えなどの API も止めてある。
- 音声は非公開のバケットに置かれ、6時間で切れる URL でしか取り出せない。7日たったファイルは自動で消える。
- Google の鍵は Azure のシークレットにだけ保存される。チャットには貼らない。

## 費用の目安

- Azure Container Apps:使っていないときは0台に縮む設定。毎月の無料枠の範囲に収まる見込み。
- Azure Container Registry(Basic):イメージの保管で月5ドル前後かかる。
- Google Cloud Storage:7日で消えるので、ほぼ0円。

使わなくなったら Azure Cloud Shell で `az group delete -n voicevox-rg` を実行すれば全部消える。

## 使い方(Claude から見たツール)

| ツール | 内容 |
|---|---|
| `list_speakers` | 話者とスタイルIDの一覧 |
| `start_synthesis` | 段落の配列を1本のMP3にするジョブを始める。段落の間の無音、話速、抑揚、指定した段落のあとの追加の無音(クイズの考える時間)を指定できる |
| `get_synthesis` | 進み具合と結果。完了すると、MP3のURL・全体の長さ・**各段落の開始秒**・クレジット表記が返る |

段落ごとに合成してつなぐので、頭出しの位置は計算どおりに正確に決まる(文字起こしや無音の検出が要らない)。

最初の1回は、コンテナの起動と VOICEVOX のモデル読み込みで1〜2分待つことがある。

## 更新するとき

ファイルを直したら、Azure Cloud Shell で `bash deploy_azure.sh <バケット名>` をもう一度実行する(鍵ファイルは不要)。URLの秘密の文字列は `~/.voicevox-mcp.env` に保存されていて変わらない。
