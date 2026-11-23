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
- 20分間隔のUploads Playlist照合による通知補完
- 配信予定からライブ開始への状態更新
- SQLiteによる状態・通知履歴の永続化
- 複数Guild・複数チャンネルへの通知
- LayoutViewを使用した通知UIと設定パネル
- `YN!youtubectrl` によるPrefix設定パネル

## システム構成

| コンポーネント | 役割 |
|---|---|
| `bot.py` | Bot起動・Cogロード・Prefix管理 |
| `cogs/notification/` | WebSub受信、YouTube API照会、フォーラム通知 |
| `cogs/config/` | LayoutView設定パネル、Guild別設定 |
| `cogs/common/` | SQLite共有ストレージ |
| `data/` | 実行時DB（Git管理対象外） |
| `data_public/` | 公開可能なデータ・仕様 |
| `.github/v1.0.0/` | v1.0.0の設計・運用ドキュメント |

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

## WebSub署名

WebSub通知はHMAC-SHA1署名を検証します。`.env.example` の `YOUTUBE_WEBSUB_SECRET` に十分長いランダム文字列を設定してください。署名秘密が未設定の場合、Botは購読を送信せず、未署名通知も受け付けません。

## APIクォータ

10チャンネルを20分間隔で照合する場合、標準的な1日あたりの使用量は約1,440ユニットです。

- `playlistItems.list`: 720回/日
- `videos.list`: 720回/日
- 合計: 1,440ユニット/日（10,000ユニット枠の14.4%）

実際の使用量は、WebSubイベント、再試行、登録チャンネル数、取得動画数により変動します。

## ドキュメント

詳細な設計、導入、運用、制約は [v1.0.0ドキュメント](./.github/v1.0.0/README.md) を参照してください。

## License

本ソフトウェアは [Suiten LICENSE](./LICENSE) に基づきます。
