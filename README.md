<div align="center">

# YouTubeNotice

**YouTubeの配信予定・ライブ開始・新着動画をDiscordフォーラムへ通知するBot**

[![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.6+-5865F2?logo=discord)](https://discordpy.readthedocs.io/)
[![License](https://img.shields.io/badge/License-Suiten%20LICENSE-lightgrey)](./LICENSE)

</div>

YouTubeNoticeは、YouTube WebSubによるプッシュ通知とYouTube Data API v3による定期照合を組み合わせ、対象チャンネルの更新をDiscordフォーラムへ自動投稿する独立型Discord Botです。

## 主な機能

- 配信予定枠の作成、ライブ配信開始、新着動画を検知
- WebSubによるプッシュ通知
- チャンネル数とAPIクォータ予算に応じたUploads Playlist定期照合（最短5分）
- 配信予定からライブ開始への状態更新
- SQLiteによる状態・通知履歴の永続化
- 複数Guild・複数チャンネルへの通知
- 赤色Embed・動画サムネイル・YouTubeリンクボタンを備えた通知UI
- Shorts／横動画／ライブ／プレミア・配信予定の種別タグ設定
- Guildごとの全通知ロール・通知カテゴリ別ロール設定
- `YN!youtubectrl` による統合Prefix設定パネル（操作セレクト＋ボタン式入力モード）
- YouTubeチャンネルID／@ハンドル／チャンネルURLによる監視登録・削除・テスト
- WebSubイベントのFIFOキュー処理と定期照合の個別エラー分離
- エラーログ送信先への運用エラー通知・重複通知抑制
- Ephemeralによるメンバー別通知設定（チャンネル単位・複数人同時利用）
- 通知カテゴリごとのロール自動付与・解除
- Bot Owner専用のAPI非使用UIテスト（LIVE／プレミア／横動画／Shorts）

## システム構成

| コンポーネント | 役割 |
|---|---|
| `bot.py` | Bot起動・Cogロード・Prefix管理 |
| `cogs/notification/` | WebSub受信、YouTube API照会、フォーラム通知 |
| `cogs/config/` | LayoutView設定パネル、Guild別設定 |
| `cogs/notification/member_preferences.py` | メンバー通知設定、Ephemeral UI、DM通知 |
| `cogs/notification/test_panel.py` | Bot Owner専用テスト通知UI |
| `cogs/common/` | SQLite共有ストレージ |
| `data/` | 実行時DB（Git管理対象外） |
| `data_public/` | 公開可能なデータ・仕様 |
| `.github/v1.0.x/` | v1.0.0の設計・運用ドキュメント |

## 必要環境

- Python 3.12+
- discord.py 2.6+
- aiohttp
- YouTube Data API v3のAPIキー
- 外部からHTTPSで到達できるWebSub Callback URL
- Discord BotのMessage Content Intent

## セットアップ

```bash
git clone https://github.com/official-kgp/youtubenotice.git
cd youtubenotice
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p ci data
cp .env.example ci/.env
```

`ci/.env` にDiscord Bot Token、YouTube API Key、Callback URLを設定してください。実際の秘密情報をGitへコミットしないでください。

## 起動

```bash
python bot.py
```

既存のBot Managerで起動する場合は、他のBotと同じ管理方式に登録してください。systemdのWorkingDirectoryはこのリポジトリのルートを指定します。

## 初期設定

1. Botを対象Discordサーバーへ招待し、Message Content Intentを有効にします。
2. Botにフォーラム閲覧・投稿・公開スレッド作成・スレッド管理権限を付与します。
3. `YN!youtubectrl` を実行します。
4. 設定パネルから通知先フォーラム、監視チャンネル、タグを登録します。

## メンバー通知設定・テスト

- `YN!youtubectrl` の「メンバー向け通知パネルを送信・更新」から設置・更新できます（旧 `YN!youtubepanel` も互換用に利用可能）
- `YN!youtubetest`：Bot Owner専用。YouTube APIを使わず、LIVE／プレミア公開／横動画／ShortsのUIをテスト

メンバー設定はephemeralで表示され、ユーザーごとに独立して操作できます。通知カテゴリロールは `YN!youtubectrl` の「通知カテゴリロール」から設定してください。

## WebSub署名

WebSub通知はHMAC-SHA1署名を検証します。`.env.example` の `YOUTUBE_WEBSUB_SECRET` に十分長いランダム文字列を設定してください。署名秘密が未設定の場合、Botは購読を送信せず、未署名通知も受け付けません。

## 通知レイアウトと配信日時

通知はDiscord Components V2の `LayoutView` で構成します。赤色のContainer、チャンネル名、タイトル、配信情報、MediaGalleryサムネイル、YouTubeリンクボタン、フッターを表示します。

- 通常動画・Shorts：YouTubeの公開日時を「配信日」として表示
- 配信予定：`liveStreamingDetails.scheduledStartTime` を「配信予定日」として表示
- ライブ開始後：`liveStreamingDetails.actualStartTime` を「配信日」として表示し、「配信予定日」は表示しません
- 既存の配信予定通知は、ライブ開始を検知した際にスレッドの先頭メッセージを更新します

日時はDiscordタイムスタンプ形式で表示するため、閲覧者のローカルタイムゾーンに変換されます。動画ジャンルはYouTubeのカテゴリIDから表示します。

## 動画種別の判定について

YouTube Data API v3が返す情報には、動画の縦横比やプレミア公開を通常の予約ライブから確実に区別する項目がありません。そのため、現在の分類は次の近似ルールです。

- ライブ中：APIの `liveBroadcastContent=live`
- プレミア／配信予定：APIの `liveBroadcastContent=upcoming`（予約ライブも含まれる可能性があります）
- Shorts：通常動画のうち、取得できた再生時間が180秒以内のもの
- 横動画：上記以外の通常動画

したがって、Shortsと横動画の分類は縦横比による厳密判定ではなく、プレミアと予約ライブも完全には区別されません。通知にはYouTube APIから取得したサムネイルを使用し、赤色のEmbedで表示します。

## 設定パネル

- `YN!youtubectrl` は操作セレクトを中心とした統合パネルです。操作選択後、必要な場合のみ「IDを入力する」ボタンから入力待ちモードに入ります。
- フォーラム設定、監視チャンネルの追加・削除、最新動画テスト、エラーログ送信先設定は、入力モード開始後120秒間入力を受け付けます。入力は最大3回まで再試行できます。
- YouTubeチャンネルは `UC` から始まるチャンネルIDのほか、`@handle`、`youtube.com/@handle`、`youtube.com/channel/UC...`、旧形式の `/user/...` URLを指定できます。
- エラーログ送信先には、Botがメッセージを送信できるテキストチャンネルを指定します。解除は「解除」と入力します。
- 動画種別タグは、Shorts／横動画／ライブ配信／プレミア・配信予定の4種類を設定できます。
- メンションロールはロールIDまたはロールメンションで設定でき、空欄で解除できます。Botは指定ロールのみをメンション対象として許可します。

## APIクォータ

定期照合はチャンネル数に応じて間隔を調整し、既定のAPI予算8,000ユニット/日を超えにくいようにします。最短間隔は5分です。10チャンネルの場合は約5分間隔となり、通常は約5,760ユニット/日（playlistItems.listとvideos.listを各1回と仮定）です。

- `playlistItems.list`: 約2,880回/日
- `videos.list`: 約2,880回/日
- 合計: 約5,760ユニット/日（10,000ユニット枠の57.6%）

実際の使用量は、WebSubイベント、再試行、登録チャンネル数、取得動画数により変動します。

## ドキュメント

詳細な設計、導入、運用、制約は [v1.0.0ドキュメント](./.github/v1.0.x/README.md) を参照してください。

## License

本ソフトウェアは [Suiten LICENSE](./LICENSE) に基づきます。
