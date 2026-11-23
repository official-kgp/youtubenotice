# YouTubeNotice v1.0.0 | 設計・導入・運用ガイド

## 1. 概要

YouTubeNoticeは、登録したYouTubeチャンネルの配信予定・ライブ配信開始・新着動画を検知し、Discordサーバーのフォーラムへ自動通知する独立型Botです。

通知経路は次の2系統です。

1. **WebSub Push**：YouTubeからのAtom通知をaiohttpで受信し、対象動画を即時照会します。
2. **定期Reconciliation**：20分ごとに各チャンネルのUploads Playlistを照合し、Push通知の遅延・欠落を補います。

WebSubは即時性を担い、定期照合は復旧性を担います。どちらか一方に依存しない構成です。

## 2. アーキテクチャ

```text
YouTube
  ├─ WebSub Push ──> Cloudflare Tunnel ──> aiohttp /websub
  │                                      │
  │                                      v
  │                               videos.list
  │                                      │
  └──────────────────────────────────────┤
                                         v
                               Notification Core
                                         │
                         SQLite state / delivery records
                                         │
                                         v
                              Discord Forum Threads
                                         ^
                                         │
                          Config System (LayoutView)
                                         ^
                                         │
                              YN!youtubectrl
```

### モジュール責務

| モジュール | 責務 |
|---|---|
| `bot.py` | Bot起動、Prefix `YN!`、Cogロード |
| `cogs/notification/core.py` | WebSub、YouTube API、20分照合、スレッド作成・更新 |
| `cogs/config/panel.py` | Prefixコマンド、LayoutView、設定Modal |
| `cogs/common/database.py` | SQLiteスキーマと共有DB操作 |
| `data/` | 実行時DB（Git管理外） |
| `data_public/` | 公開可能な仕様・サンプルデータ |

Notification CoreとConfig SystemはCogとして分離されています。Config Systemは共有DBとNotification Coreの公開メソッドを利用し、通知処理そのものを持ちません。

## 3. 通知状態

YouTube Data APIの `snippet.liveBroadcastContent` を基準に分類します。

| API値 | 内部状態 | 動作 |
|---|---|---|
| `upcoming` | 配信予定 | フォーラムスレッド作成 |
| `live` | ライブ中 | 新規ならスレッド作成。予定枠があれば既存スレッド更新 |
| `none` | 通常動画 | 新着動画としてスレッド作成 |

API応答が空、非公開、削除済み、照会失敗の場合は通常動画と誤認せず、処理を保留します。

### 初回同期

初回の定期照合では、過去の通常動画を一括通知しないようにします。初回に見つかった通常動画は既知データとして保存し、配信予定・ライブ中の動画は通知対象とします。以降に新しく追加された通常動画は通知します。

WebSubで受信した新規動画は初回同期中でも通知対象です。

## 4. 20分間隔の補完照合

照合は `discord.ext.tasks` で20分間隔に実行します。

各チャンネルについて次の処理を行います。

1. `channels.list` でUploads Playlist IDを取得（初回のみ、DBへキャッシュ）。
2. `playlistItems.list` で最新10件の動画IDを取得。
3. DBに保存された配信予定動画も照合対象へ追加。
4. `videos.list` で動画の状態とメタデータを取得。
5. SQLiteの状態と比較し、必要な通知・スレッド更新を実行。

動画IDは最大50件ずつ `videos.list` にまとめます。既知の配信予定動画が多い場合は、1チャンネルの照合で複数リクエストになることがあります。

## 5. APIクォータ試算

YouTube Data APIの標準クォータは、通常のData APIメソッド合計で10,000ユニット/日です。今回使用する `channels.list`、`playlistItems.list`、`videos.list` は各1ユニットです。最新の公式表はGoogle for DevelopersのQuota Calculatorを参照してください。

### 10チャンネル・20分間隔

- 1日あたりの照合回数：24時間 × 3回/時 = 72回/チャンネル
- `playlistItems.list`：10チャンネル × 72回 = 720回/日
- `videos.list`：10チャンネル × 72回 = 720回/日（各チャンネルの取得IDが50件以内の場合）
- 合計：1,440ユニット/日
- 標準10,000ユニットに対する割合：14.4%
- 残り：8,560ユニット/日

初回のUploads Playlist ID取得は `channels.list` がチャンネルごとに1回なので、10チャンネルで最大10ユニットの初期費用です。

### 試算に含まれないもの

- WebSub Pushを受けた際の追加 `videos.list`
- 50件を超える動画IDの追加バッチ
- APIエラーやネットワーク障害による再試行
- チャンネル情報キャッシュを失った場合の再取得
- 将来追加する機能のAPI呼び出し

`search.list` は使用しません。定期照合ではUploads Playlistを使うため、検索用の別枠も消費しません。

## 6. Discord設定

Botには対象フォーラムに対する次の権限が必要です。

- View Channel
- Send Messages
- Embed Links
- Send Messages in Threads
- Manage Threads

BotのMessage Content IntentをDeveloper Portalとコードの両方で有効にしてください。

設定コマンドはPrefixコマンドです。

```text
YN!youtubectrl
```

LayoutViewパネルから次を設定できます。

| 操作 | 説明 |
|---|---|
| フォーラム設定 | 通知先Forum Channelを指定 |
| チャンネル追加 | YouTube Channel IDを登録し、WebSub購読を即時リクエスト |
| チャンネル削除 | Guildの監視対象から削除。全Guildで未使用なら購読解除 |
| タグ設定 | 配信予定・ライブ中・新着動画タグを名前またはIDで指定 |
| 設定確認 | 現在のフォーラム、監視チャンネル、タグIDを表示 |

設定変更は「サーバー管理」権限を持つメンバーに限定されます。設定はGuild単位で保存されます。

## 7. 環境構築

### Python環境

```bash
git clone https://github.com/official-kgp/youtubenotice.git
cd youtubenotice
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p ci data
cp .env.example ci/.env
```

### 環境変数

`ci/.env` に設定します。`YOUTUBE_WEBSUB_SECRET` はWebSubのHMAC-SHA1署名検証に使用する秘密文字列です。十分長いランダム値を設定し、Gitへコミットしないでください。

```dotenv
DISCORD_BOT_TOKEN=your_discord_bot_token
YOUTUBE_API_KEY=your_youtube_data_api_key
YOUTUBE_WEBSUB_SECRET=your_random_websub_secret
YOUTUBE_CALLBACK_URL=https://y-api.suiten.site
YOUTUBE_WEB_HOST=127.0.0.1
YOUTUBE_WEB_PORT=8765
YOUTUBE_DB_PATH=data/youtube_notice.sqlite3
```

秘密情報を含む `ci/.env` はGitへコミットしないでください。

### WebSub購読・署名

購読リクエストには `hub.secret` を付与し、受信POSTでは `X-Hub-Signature` をHMAC-SHA1で検証します。署名秘密が未設定の場合は購読を送信せず、POST通知も受け付けません。購読認証GETではHubから返された実際の `hub.lease_seconds` を保存します。Botは6時間ごとに期限を確認し、残り48時間以下になった購読だけを更新します。Hubが認証GETで期限を返さない場合は、次回の確認で再購読を試みます。

### Cloudflare Tunnel

既存TunnelのIngressへ次のルールを追加します。既存の `suiten.site` 等のルールは残してください。

```yaml
- hostname: y-api.suiten.site
  service: http://127.0.0.1:8765
```

YouTubeのPush通知トピックは、現行の配信挙動との互換性を考慮し `https://www.youtube.com/xml/feeds/videos.xml?channel_id=...` を使用します。購読POSTのHTTP 202は受付を示すだけで、実際の購読成立は後続のGET認証で確定します。

Callback URLは次のURLになります。

```text
https://y-api.suiten.site/websub
```

### 起動

```bash
source .venv/bin/activate
python bot.py
```

既存のBot Managerを利用する場合は、他のBotと同じ管理方式に登録します。Bot Managerの管理リストへこのリポジトリの配置ディレクトリ名を追加し、WorkingDirectoryをリポジトリルートに設定してください。既存のsystemdサービスや他Botの設定を上書きしないでください。

## 8. 永続化と重複防止

SQLiteは `data/youtube_notice.sqlite3` に保存します。

- `guild_config`：Guild別フォーラム・タグ設定
- `tracked_channels`：Guild別監視チャンネル
- `channel_cache`：Uploads Playlist IDと初回同期状態
- `videos`：動画ごとの最新状態
- `deliveries`：Guild・動画ごとの作成済みスレッド
- `subscriptions`：WebSub購読リクエスト履歴

`deliveries` の複合主キーで同一Guild・同一動画の二重スレッド作成を抑止します。DBファイルはバックアップ対象ですが、Gitへコミットしないでください。

## 9. 信頼性と制約

WebSubはPush通知であり、配送遅延・重複・一時的な欠落を完全には排除できません。20分間隔の補完照合によって、Pushが届かなかった新着動画や配信状態の変化を次回照合で検出する設計です。

ただし、次の理由により「すり抜けゼロ」「即時検知100%」は保証できません。

- YouTube側のFeed/API反映遅延
- WebSub Hubやネットワークの障害
- Bot・自宅サーバーの停止
- APIクォータ上限、APIキー設定不備
- 最新10件より古い通常動画の取りこぼし（長期停止時など）

定期照合は最新10件を確認し、DB上の配信予定動画も追跡します。Botが長期間停止し、その間に10件を超える通常動画が公開された場合、通常動画の全件回収は保証しません。停止時間が長い場合はAPI使用量と通知対象を確認してください。

同一SQLite DBに対して複数Botプロセスを同時起動しないでください。現状は単一プロセス運用を前提としています。

## 10. 運用チェックリスト

- [ ] Discord Bot Tokenが設定されている
- [ ] YouTube API Keyが設定されている
- [ ] Callback URLが外部HTTPSから到達できる
- [ ] TunnelのIngressが8765番ポートへ向いている
- [ ] BotのMessage Content Intentが有効
- [ ] フォーラム権限が付与されている
- [ ] `YN!youtubectrl` でフォーラムを登録
- [ ] 監視チャンネルを登録
- [ ] WebSub認証GETが成功
- [ ] Push通知でスレッドが作成される
- [ ] 20分照合が実行される
- [ ] upcomingからliveへの更新が確認できる
- [ ] 再起動後もSQLite設定が維持される

---

©2026 YouTubeNotice · Developed by Suiten
