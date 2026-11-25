from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands, tasks
from dotenv import load_dotenv

from cogs.common.database import Database

load_dotenv(dotenv_path="ci/.env")

log = logging.getLogger("youtubenotice.notification")
WEBSUB_HUB = "https://pubsubhubbub.appspot.com/subscribe"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}
CHANNEL_ID_RE = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")
VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
DB_PATH = Path(os.getenv("YOUTUBE_DB_PATH", "data/youtube_notice.sqlite3"))
API_KEY = os.getenv("YOUTUBE_API_KEY", "")
WEBSUB_SECRET = os.getenv("YOUTUBE_WEBSUB_SECRET", "")
CALLBACK_URL = os.getenv("YOUTUBE_CALLBACK_URL", "").rstrip("/")
WEB_HOST = os.getenv("YOUTUBE_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("YOUTUBE_WEB_PORT", "8765"))
SUBSCRIPTION_LEASE = 864000
SCAN_BATCH_SIZE = 50
LATEST_UPLOADS_PER_CHANNEL = 10


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def youtube_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return f"<t:{int(parsed.timestamp())}:F>"
    except (TypeError, ValueError, OverflowError):
        return None


VIDEO_CATEGORIES = {
    "1": "映画とアニメ", "2": "自動車と乗り物", "10": "音楽",
    "15": "ペットと動物", "17": "スポーツ", "19": "旅行とイベント",
    "20": "ゲーム", "22": "ブログ", "23": "コメディ",
    "24": "エンターテイメント", "25": "ニュースと政治",
    "26": "ハウツーとスタイル", "27": "教育", "28": "科学と技術",
    "29": "非営利団体と社会活動",
}


def notification_layout(item: dict[str, Any], mention_role_id: int | None = None) -> discord.ui.LayoutView:
    """Build the YouTube notification using Discord Components V2."""
    content_type = item.get("content_type", "horizontal")
    status = item.get("status", "video")

    type_labels = {
        "short": "SHORTS",
        "horizontal": "VIDEO",
        "live": "LIVE",
        "premiere": "PREMIERE",
    }
    status_label = "LIVE" if status == "live" else "配信予定" if status == "upcoming" else "アーカイブ" if status == "archive" else "公開"
    title_icon = (
        "<:Video_Schedule:1574818195288760410>" if status == "upcoming"
        else "<:Live_Streaming:1574813059036221445>" if status == "live"
        else "<:Archive:1574816935131217990>" if status == "archive"
        else "<:Youtube_Shorts:1574814556369330288>" if content_type == "short"
        else "<:Youtube_Video:1574814296158900284>"
    )
    stream_date = (
        youtube_timestamp(item.get("actual_start_time"))
        if status == "live"
        else youtube_timestamp(item.get("published_at"))
        if status == "video"
        else None
    )
    scheduled_date = (
        youtube_timestamp(item.get("scheduled_start_time"))
        if status == "upcoming"
        else None
    )
    category = VIDEO_CATEGORIES.get(str(item.get("category_id") or ""), "未分類")
    title = discord.utils.escape_markdown(item.get("title") or "タイトル未取得")[:240]
    channel = discord.utils.escape_markdown(item.get("channel_title") or "YouTube")[:100]
    thumbnail = item.get("thumbnail_url") or f"https://i.ytimg.com/vi/{item['video_id']}/hqdefault.jpg"

    info = [f"ステータス：{status_label}"]
    if stream_date:
        info.append(f"配信日：{stream_date}")
    if scheduled_date:
        info.append(f"配信予定日：{scheduled_date}")
    info.append(f"ジャンル：{category}")

    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=discord.Colour.red())


    container.add_item(discord.ui.TextDisplay(f"-# {channel}"))
    container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
    container.add_item(discord.ui.TextDisplay(f"## {title_icon} ｜ {title}"))
    container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large))
    container.add_item(discord.ui.TextDisplay("\n".join(info)))
    container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large))
    container.add_item(discord.ui.MediaGallery(
        discord.MediaGalleryItem(thumbnail, description="YouTube thumbnail")
    ))
    container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large))
    container.add_item(discord.ui.ActionRow(
        discord.ui.Button(
            label="YouTubeを開く",
            style=discord.ButtonStyle.link,
            url=f"https://www.youtube.com/watch?v={item['video_id']}",
        )
    ))
    container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
    footer_date = stream_date or scheduled_date
    footer = f"-# YouTubeNotice ｜ {footer_date}" if footer_date else "-# YouTubeNotice"
    container.add_item(discord.ui.TextDisplay(footer))

    if mention_role_id:
        container.add_item(discord.ui.TextDisplay(f"<@&{mention_role_id}>"))
    view.add_item(container)
    return view


class YouTubeNotification(commands.Cog):
    """WebSub receiver, periodic reconciliation, and Forum delivery core."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database | None = None
        self.session: aiohttp.ClientSession | None = None
        self.web_runner: web.AppRunner | None = None
        self.process_lock = asyncio.Lock()
        self.pending_subscriptions: dict[str, datetime] = {}
        self.pending_unsubscribes: dict[str, datetime] = {}
        self._startup_renewal = True

    async def cog_load(self) -> None:
        if not API_KEY:
            log.warning("YOUTUBE_API_KEY is not configured; API polling is disabled.")
        self.db = Database(DB_PATH)
        self.bot.youtube_db = self.db

        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"User-Agent": "YouTubeNotice/1.0"},
        )
        app = web.Application(client_max_size=256 * 1024)
        app.router.add_get("/websub", self.websub_verify)
        app.router.add_post("/websub", self.websub_receive)

        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, WEB_HOST, WEB_PORT)
        await site.start()
        log.info("WebSub callback listener started at %s:%s", WEB_HOST, WEB_PORT)

        self.renew_subscriptions.start()
        self.reconcile_channels.start()

    async def cog_unload(self) -> None:
        self.renew_subscriptions.cancel()
        self.reconcile_channels.cancel()
        if self.web_runner:
            await self.web_runner.cleanup()
        if self.session and not self.session.closed:
            await self.session.close()
        if self.db:
            self.db.close()
        self.bot.youtube_db = None

    def database(self) -> Database:
        if self.db is None:
            raise RuntimeError("YouTube database has not been initialized.")
        return self.db

    @staticmethod
    def channel_id_from_topic(topic: str) -> str | None:
        match = re.search(r"(?:\?|&)channel_id=(UC[a-zA-Z0-9_-]{22})(?:&|$)", topic)
        return match.group(1) if match else None

    async def websub_verify(self, request: web.Request) -> web.Response:
        mode = request.query.get("hub.mode", "")
        topic = request.query.get("hub.topic", "")
        challenge = request.query.get("hub.challenge")
        channel_id = self.channel_id_from_topic(topic)

        if mode not in {"subscribe", "unsubscribe"} or not challenge or not channel_id:
            raise web.HTTPBadRequest(text="Invalid WebSub verification request.")
        expected_topic = f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"
        if topic != expected_topic:
            raise web.HTTPForbidden(text="Unexpected WebSub topic.")

        if mode == "subscribe":
            tracked = self.database().fetchone(
                "SELECT 1 FROM tracked_channels WHERE channel_id=? LIMIT 1",
                (channel_id,),
            )
            pending_until = self.pending_subscriptions.get(channel_id)
            if (
                not tracked
                or pending_until is None
                or pending_until < datetime.now(timezone.utc)
            ):
                self.pending_subscriptions.pop(channel_id, None)
                raise web.HTTPForbidden(text="No matching pending subscription.")
            self.pending_subscriptions.pop(channel_id, None)
            try:
                granted_lease = max(0, int(request.query.get("hub.lease_seconds", "0")))
            except ValueError:
                granted_lease = 0
            self.database().execute(
                """INSERT OR REPLACE INTO subscriptions(
                       channel_id, requested_at, lease_seconds, last_result
                   ) VALUES(?,?,?,?)""",
                (
                    channel_id, utc_now(), granted_lease,
                    "verified" if granted_lease > 0 else "verified_unknown_lease",
                ),
            )
        else:
            pending_until = self.pending_unsubscribes.get(channel_id)
            if pending_until is None or pending_until < datetime.now(timezone.utc):
                self.pending_unsubscribes.pop(channel_id, None)
                raise web.HTTPForbidden(text="No matching pending unsubscription.")
            self.pending_unsubscribes.pop(channel_id, None)
            self.database().execute(
                "DELETE FROM subscriptions WHERE channel_id=?",
                (channel_id,),
            )

        return web.Response(text=challenge, content_type="text/plain")

    async def websub_receive(self, request: web.Request) -> web.Response:
        try:
            raw = await request.read()
            if not WEBSUB_SECRET:
                raise web.HTTPServiceUnavailable(text="WebSub signature verification is not configured.")
            signature = request.headers.get("X-Hub-Signature", "")
            algorithm, separator, supplied_digest = signature.partition("=")
            expected_digest = hmac.new(
                WEBSUB_SECRET.encode("utf-8"), raw, hashlib.sha1,
            ).hexdigest()
            if (
                not separator
                or algorithm.lower() != "sha1"
                or not hmac.compare_digest(supplied_digest, expected_digest)
            ):
                raise web.HTTPForbidden(text="Invalid WebSub signature.")
            root = ET.fromstring(raw)
        except web.HTTPException:
            raise
        except ET.ParseError:
            raise web.HTTPBadRequest(text="Invalid Atom XML.")
        except Exception:
            log.exception("Failed to read WebSub payload.")
            raise web.HTTPBadRequest(text="Invalid request body.")

        entry = root.find("atom:entry", ATOM_NS)
        if entry is None:
            return web.Response(status=204)

        video_id = (entry.findtext("yt:videoId", default="", namespaces=ATOM_NS) or "").strip()
        channel_id = (entry.findtext("yt:channelId", default="", namespaces=ATOM_NS) or "").strip()

        if not VIDEO_ID_RE.fullmatch(video_id) or not CHANNEL_ID_RE.fullmatch(channel_id):
            raise web.HTTPBadRequest(text="Invalid video or channel ID.")

        tracked = self.database().fetchone(
            "SELECT 1 FROM tracked_channels WHERE channel_id=? LIMIT 1",
            (channel_id,),
        )
        if tracked:
            asyncio.create_task(self.process_websub_video(video_id, channel_id))

        return web.Response(status=204)

    async def request_websub(self, channel_id: str, mode: str = "subscribe") -> bool:
        if not CALLBACK_URL or not self.session:
            log.warning("YOUTUBE_CALLBACK_URL is missing; WebSub request skipped.")
            return False
        if not WEBSUB_SECRET:
            log.warning("YOUTUBE_WEBSUB_SECRET is missing; refusing unsigned WebSub subscription.")
            return False

        pending_map = self.pending_subscriptions if mode == "subscribe" else self.pending_unsubscribes
        pending_map[channel_id] = datetime.now(timezone.utc) + timedelta(minutes=30)

        form = {
            "hub.callback": f"{CALLBACK_URL}/websub",
            "hub.topic": f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "hub.verify": "async",
            "hub.mode": mode,
        }
        if mode == "subscribe":
            form["hub.lease_seconds"] = str(SUBSCRIPTION_LEASE)
            form["hub.secret"] = WEBSUB_SECRET

        try:
            async with self.session.post(WEBSUB_HUB, data=form) as response:
                body = await response.text()
                if response.status not in (202, 204):
                    log.warning(
                        "WebSub %s request failed for %s: HTTP %s %s",
                        mode, channel_id, response.status, body[:300],
                    )
                    pending_map.pop(channel_id, None)
                    self.database().execute(
                        "INSERT OR REPLACE INTO subscriptions(channel_id, requested_at, lease_seconds, last_result) VALUES(?,?,?,?)",
                        (channel_id, utc_now(), SUBSCRIPTION_LEASE if mode == "subscribe" else 0, f"HTTP {response.status}"),
                    )
                    return False

            if mode == "subscribe":
                self.database().execute(
                    """INSERT INTO subscriptions(channel_id, requested_at, lease_seconds, last_result)
                       VALUES(?,?,?,?)
                       ON CONFLICT(channel_id) DO UPDATE SET
                       requested_at=CASE WHEN subscriptions.last_result='verified'
                                         THEN subscriptions.requested_at ELSE excluded.requested_at END,
                       lease_seconds=CASE WHEN subscriptions.last_result='verified'
                                          THEN subscriptions.lease_seconds ELSE excluded.lease_seconds END,
                       last_result=CASE WHEN subscriptions.last_result='verified'
                                        THEN subscriptions.last_result ELSE excluded.last_result END""",
                    (channel_id, utc_now(), SUBSCRIPTION_LEASE, "accepted"),
                )
            return True
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pending_map.pop(channel_id, None)
            log.exception("WebSub request failed for %s", channel_id)
            return False

    async def process_websub_video(self, video_id: str, channel_id: str) -> None:
        item = await self.fetch_video(video_id)
        if item and item["channel_id"] == channel_id:
            await self.process_item(item, allow_new_video=True, force_new_video=True)

    async def api_get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if not API_KEY or not self.session:
            return None
        request_params = {**params, "key": API_KEY}
        try:
            async with self.session.get(f"{YOUTUBE_API}/{endpoint}", params=request_params) as response:
                payload = await response.json(content_type=None)
                if response.status != 200:
                    log.warning(
                        "YouTube API %s returned HTTP %s: %s",
                        endpoint, response.status, str(payload)[:500],
                    )
                    return None
                return payload
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            log.exception("YouTube API request failed: %s", endpoint)
            return None

    async def fetch_video(self, video_id: str) -> dict[str, Any] | None:
        payload = await self.api_get("videos", {"part": "snippet,contentDetails,liveStreamingDetails", "id": video_id})
        items = (payload or {}).get("items") or []
        if not items:
            return None
        return self.normalize_video(items[0])

    @staticmethod
    def normalize_video(raw: dict[str, Any]) -> dict[str, Any]:
        snippet = raw.get("snippet", {})
        live_state = snippet.get("liveBroadcastContent", "none")
        live_details = raw.get("liveStreamingDetails", {})
        actual_start_time = live_details.get("actualStartTime")
        actual_end_time = live_details.get("actualEndTime")
        # The API's actualStartTime/actualEndTime also appear for Premieres.
        # A completed upload is therefore classified using each guild's delivery history.
        status = live_state if live_state in {"upcoming", "live"} else "video"

        duration = raw.get("contentDetails", {}).get("duration", "")
        duration_match = re.fullmatch(
            r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration
        )
        duration_seconds = 0
        if duration_match:
            hours, minutes, seconds = (int(value or 0) for value in duration_match.groups())
            duration_seconds = hours * 3600 + minutes * 60 + seconds

        if status == "live":
            content_type = "live"
        elif status == "upcoming":
            # Data API does not reliably distinguish a Premiere from a scheduled live stream.
            content_type = "premiere"
        else:
            # Aspect ratio is not exposed by the Data API; duration is a best-effort heuristic.
            content_type = "short" if 0 < duration_seconds <= 180 else "horizontal"

        scheduled_start_time = live_details.get("scheduledStartTime")

        thumbnails = snippet.get("thumbnails", {})
        thumbnail = next(
            (thumbnails[key]["url"] for key in ("maxres", "standard", "high", "medium", "default")
             if key in thumbnails and thumbnails[key].get("url")),
            None,
        )
        return {
            "video_id": raw.get("id"),
            "channel_id": snippet.get("channelId"),
            "title": snippet.get("title") or "タイトル未取得",
            "channel_title": snippet.get("channelTitle") or "YouTube",
            "status": status,
            "content_type": content_type,
            "duration_seconds": duration_seconds,
            "thumbnail_url": thumbnail,
            "category_id": snippet.get("categoryId"),
            "published_at": snippet.get("publishedAt"),
            "actual_start_time": actual_start_time,
            "scheduled_start_time": scheduled_start_time,
        }

    async def get_uploads_playlist(self, channel_id: str) -> str | None:
        cached = self.database().fetchone(
            "SELECT uploads_playlist_id FROM channel_cache WHERE channel_id=?",
            (channel_id,),
        )
        if cached:
            return cached["uploads_playlist_id"]

        payload = await self.api_get(
            "channels",
            {"part": "contentDetails,snippet", "id": channel_id},
        )
        items = (payload or {}).get("items") or []
        if not items:
            log.warning("No public channel metadata found for %s", channel_id)
            return None

        channel = items[0]
        playlist_id = (
            channel.get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        if not playlist_id:
            return None

        channel_title = channel.get("snippet", {}).get("title")
        self.database().execute(
            """INSERT INTO channel_cache(channel_id, uploads_playlist_id, channel_title, initialized)
               VALUES(?,?,?,0)
               ON CONFLICT(channel_id) DO UPDATE SET
               uploads_playlist_id=excluded.uploads_playlist_id,
               channel_title=excluded.channel_title""",
            (channel_id, playlist_id, channel_title),
        )
        self.database().execute(
            "UPDATE tracked_channels SET channel_title=? WHERE channel_id=?",
            (channel_title, channel_id),
        )
        return playlist_id

    async def fetch_video_batch(self, video_ids: list[str]) -> list[dict[str, Any]] | None:
        normalized: list[dict[str, Any]] = []
        unique_ids = list(dict.fromkeys(video_ids))
        for start in range(0, len(unique_ids), SCAN_BATCH_SIZE):
            batch = unique_ids[start:start + SCAN_BATCH_SIZE]
            if not batch:
                continue
            payload = await self.api_get(
                "videos",
                {"part": "snippet,contentDetails,liveStreamingDetails", "id": ",".join(batch)},
            )
            if payload is None:
                return None
            normalized.extend(self.normalize_video(raw) for raw in payload.get("items", []))
        return normalized

    async def scan_channel(self, channel_id: str) -> None:
        playlist_id = await self.get_uploads_playlist(channel_id)
        if not playlist_id:
            return

        playlist = await self.api_get(
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": LATEST_UPLOADS_PER_CHANNEL,
            },
        )
        if playlist is None:
            return

        upload_ids = [
            item.get("contentDetails", {}).get("videoId")
            for item in playlist.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]

        cache = self.database().fetchone(
            "SELECT initialized FROM channel_cache WHERE channel_id=?",
            (channel_id,),
        )
        first_scan = not cache or not bool(cache["initialized"])

        # Keep scheduled broadcasts in the watch set even after they leave the
        # latest ten uploads window, so upcoming -> live transitions are found.
        upcoming_rows = self.database().fetchall(
            "SELECT video_id FROM videos WHERE channel_id=? AND status='upcoming'",
            (channel_id,),
        )
        video_ids = list(dict.fromkeys([
            *upload_ids,
            *(row["video_id"] for row in upcoming_rows),
        ]))

        if not video_ids:
            self.database().execute(
                "UPDATE channel_cache SET initialized=1, last_scanned_at=? WHERE channel_id=?",
                (utc_now(), channel_id),
            )
            return

        videos = await self.fetch_video_batch(video_ids)
        if videos is None:
            # Do not mark the initial baseline complete after a transient API
            # error; otherwise old uploads could be misclassified as new later.
            return
        for item in videos:
            if item.get("channel_id") != channel_id:
                continue
            # During the first baseline scan, do not create threads for old
            # ordinary uploads. WebSub handles fresh feed events; upcoming/live
            # broadcasts are still important and are posted immediately.
            allow_new_video = not first_scan
            await self.process_item(item, allow_new_video=allow_new_video)

        self.database().execute(
            "UPDATE channel_cache SET initialized=1, last_scanned_at=? WHERE channel_id=?",
            (utc_now(), channel_id),
        )

    async def process_item(
        self,
        item: dict[str, Any],
        allow_new_video: bool,
        force_new_video: bool = False,
        target_guild_id: int | None = None,
    ) -> None:
        video_id = item.get("video_id")
        channel_id = item.get("channel_id")
        if not video_id or not channel_id:
            return

        async with self.process_lock:
            previous = self.database().fetchone(
                "SELECT status, notification_eligible FROM videos WHERE video_id=?",
                (video_id,),
            )
            eligible = (
                True if force_new_video
                else bool(previous["notification_eligible"]) if previous
                else bool(allow_new_video)
            )

            self.database().execute(
                """INSERT INTO videos(
                       video_id, channel_id, title, channel_title, status,
                       published_at, notification_eligible, updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(video_id) DO UPDATE SET
                   title=excluded.title,
                   channel_title=excluded.channel_title,
                   status=excluded.status,
                   published_at=excluded.published_at,
                   notification_eligible=MAX(videos.notification_eligible, excluded.notification_eligible),
                   updated_at=excluded.updated_at""",
                (
                    video_id, channel_id, item["title"], item["channel_title"],
                    item["status"], item.get("published_at"), int(eligible), utc_now(),
                ),
            )

            configs = self.database().fetchall(
                """SELECT tc.guild_id, gc.forum_id, gc.short_tag_id, gc.horizontal_tag_id,
                          gc.live_tag_id, gc.premiere_tag_id, gc.mention_role_id
                   FROM tracked_channels tc
                   JOIN guild_config gc ON gc.guild_id=tc.guild_id
                   WHERE tc.channel_id=? AND (? IS NULL OR tc.guild_id=?)""",
                (channel_id, target_guild_id, target_guild_id),
            )

            for config in configs:
                delivery = self.database().fetchone(
                    "SELECT thread_id, last_status FROM deliveries WHERE guild_id=? AND video_id=?",
                    (config["guild_id"], video_id),
                )
                if delivery:
                    previous_status = delivery["last_status"]
                    # A completed actual LIVE is an archive. A completed Premiere is a normal video.
                    if previous_status == "live" and item["status"] == "video":
                        item["status"] = "archive"
                        item["content_type"] = "archive"
                    elif previous_status == "upcoming" and item["status"] == "video":
                        item["status"] = "video"
                        item["content_type"] = "horizontal"
                        item["published_at"] = item.get("actual_start_time") or item.get("published_at")

                    announce_start = previous_status == "upcoming" and item["status"] == "live"
                    updated = await self.update_live_thread(
                        config, delivery["thread_id"], item, announce_start=announce_start
                    )
                    if updated:
                        self.database().execute(
                            "UPDATE deliveries SET last_status=? WHERE guild_id=? AND video_id=?",
                            (item["status"], config["guild_id"], video_id),
                        )
                    continue

                should_create = item["status"] in {"upcoming", "live"}
                if item["status"] in {"video", "archive"}:
                    should_create = eligible
                if should_create:
                    await self.create_forum_thread(config, item)

    async def ensure_forum_tags(
        self, forum: discord.ForumChannel, guild_id: int, channel_id: str,
        channel_title: str, content_type: str,
    ) -> list[discord.ForumTag]:
        """Create/reuse channel and video-type tags, respecting Discord's 20-tag limit."""
        db = self.database()
        type_names = {
            "short": "Shorts",
            "horizontal": "横動画",
            "live": "LIVE",
            "premiere": "プレミア公開",
            "archive": "アーカイブ",
        }
        type_column = {
            "short": "short_tag_id",
            "horizontal": "horizontal_tag_id",
            "live": "live_tag_id",
            "premiere": "premiere_tag_id",
            "archive": "horizontal_tag_id",
        }.get(content_type, "horizontal_tag_id")
        config = db.fetchone(
            f"SELECT {type_column} AS type_tag_id FROM guild_config WHERE guild_id=?",
            (guild_id,),
        )
        available = list(forum.available_tags)
        changed = False

        type_tag = next(
            (tag for tag in available if config and config["type_tag_id"] and tag.id == config["type_tag_id"]),
            None,
        )
        if type_tag is None:
            type_tag = next((tag for tag in available if tag.name == type_names.get(content_type)), None)
        if type_tag is None and len(available) < 20:
            type_tag = discord.ForumTag(name=type_names.get(content_type, "横動画"))
            available.append(type_tag)
            changed = True

        channel_row = db.fetchone(
            "SELECT channel_tag_id FROM tracked_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        channel_tag = next(
            (tag for tag in available if channel_row and channel_row["channel_tag_id"] and tag.id == channel_row["channel_tag_id"]),
            None,
        )
        safe_channel_name = re.sub(r"[\\n\\r]", " ", channel_title or channel_id).strip()[:20] or channel_id
        if channel_tag is None:
            channel_tag = next((tag for tag in available if tag.name == safe_channel_name), None)
        if channel_tag is None and len(available) < 20:
            channel_tag = discord.ForumTag(name=safe_channel_name)
            available.append(channel_tag)
            changed = True

        if changed:
            try:
                await forum.edit(available_tags=available, reason="YouTubeNotice automatic tag provisioning")
            except discord.HTTPException:
                log.exception("Could not create automatic Forum tags for guild %s", guild_id)
                return []

        # Re-read tags after edit so IDs assigned by Discord are used.
        available = list(forum.available_tags)
        if changed:
            try:
                forum = await forum.guild.fetch_channel(forum.id)
                available = list(forum.available_tags)
            except discord.HTTPException:
                pass

        type_tag = next((tag for tag in available if tag.name == type_names.get(content_type)), type_tag)
        channel_tag = next((tag for tag in available if tag.name == safe_channel_name), channel_tag)
        if channel_tag and channel_tag.id:
            db.execute(
                "UPDATE tracked_channels SET channel_tag_id=? WHERE guild_id=? AND channel_id=?",
                (channel_tag.id, guild_id, channel_id),
            )
        if type_tag and type_tag.id:
            db.execute(
                f"UPDATE guild_config SET {type_column}=?, updated_at=CURRENT_TIMESTAMP WHERE guild_id=?",
                (type_tag.id, guild_id),
            )
        return [tag for tag in (channel_tag, type_tag) if tag is not None and tag.id]

    async def create_forum_thread(self, config: Any, item: dict[str, Any]) -> None:
        guild = self.bot.get_guild(config["guild_id"])
        if guild is None:
            return
        forum = guild.get_channel(config["forum_id"])
        if not isinstance(forum, discord.ForumChannel):
            log.warning("Configured destination is not a ForumChannel: guild=%s", guild.id)
            return

        applied_tags = await self.ensure_forum_tags(
            forum, guild.id, item["channel_id"], item.get("channel_title", "YouTube"),
            item.get("content_type", "horizontal"),
        )
        thread_name = discord.utils.escape_markdown(item["title"])[:90].strip()
        if not thread_name:
            thread_name = f"YouTube {item['video_id']}"
        role_id = config["mention_role_id"]

        try:
            thread, _starter = await forum.create_thread(
                name=thread_name,
                view=notification_layout(item, role_id),
                applied_tags=applied_tags,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
                reason="YouTubeNotice automatic notification",
            )
        except discord.HTTPException:
            log.exception("Failed to create Forum thread in guild %s", guild.id)
            return

        self.database().execute(
            """INSERT OR IGNORE INTO deliveries(guild_id, video_id, thread_id, last_status)
               VALUES(?,?,?,?)""",
            (guild.id, item["video_id"], thread.id, item["status"]),
        )

    async def update_live_thread(
        self, config: Any, thread_id: int, item: dict[str, Any], announce_start: bool = False
    ) -> bool:
        guild = self.bot.get_guild(config["guild_id"])
        if guild is None:
            return False

        thread = guild.get_thread(thread_id)
        if thread is None:
            try:
                fetched = await guild.fetch_channel(thread_id)
                thread = fetched if isinstance(fetched, discord.Thread) else None
            except discord.HTTPException:
                log.warning("Could not fetch existing YouTube thread %s", thread_id)
                return False
        if thread is None:
            return False

        role_id = config["mention_role_id"]
        forum = guild.get_channel(config["forum_id"])
        try:
            new_name = discord.utils.escape_markdown(item.get("title") or "YouTube")[:90].strip()
            if new_name and thread.name != new_name:
                try:
                    await thread.edit(name=new_name, reason="YouTubeNotice video title update")
                except discord.HTTPException:
                    log.exception("Could not rename YouTube thread %s", thread_id)

            starter = await thread.fetch_message(thread.id)
            await starter.edit(
                content=None, embed=None, embeds=[], view=notification_layout(item, role_id),
                allowed_mentions=discord.AllowedMentions(roles=False, users=False, everyone=False),
            )

            if isinstance(forum, discord.ForumChannel):
                target_tags = await self.ensure_forum_tags(
                    forum, guild.id, item["channel_id"], item.get("channel_title", "YouTube"),
                    item.get("content_type", "horizontal"),
                )
                old_type_names = {"Shorts", "横動画", "LIVE", "プレミア公開", "アーカイブ"}
                tags = [tag for tag in thread.applied_tags if tag.name not in old_type_names and tag.name != (item.get("channel_title") or "YouTube")[:20]]
                merged = list({tag.id: tag for tag in [*tags, *target_tags]}.values())
                try:
                    await thread.edit(applied_tags=merged[:5])
                except discord.HTTPException:
                    log.exception("Status/channel tag update failed for thread %s", thread_id)

            if announce_start:
                announcement = "🔴 **LIVE／プレミア公開が開始されました！**"
                if role_id:
                    announcement = f"<@&{role_id}>\n{announcement}"
                await thread.send(
                    announcement,
                    allowed_mentions=discord.AllowedMentions(
                        roles=True, users=False, everyone=False
                    ),
                )
            return True
        except discord.HTTPException:
            log.exception("Failed to update YouTube thread %s", thread_id)
            return False

    async def test_latest_video(self, guild_id: int, channel_id: str) -> tuple[bool, str]:
        """Fetch one latest upload through YouTube API and run the normal delivery path."""
        if not API_KEY:
            return False, "YOUTUBE_API_KEY が設定されていません。"
        tracked = self.database().fetchone(
            "SELECT 1 FROM tracked_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        if not tracked:
            return False, "このサーバーで監視しているチャンネルではありません。"

        config = self.database().fetchone(
            "SELECT forum_id FROM guild_config WHERE guild_id=?",
            (guild_id,),
        )
        if not config:
            return False, "通知先フォーラムが設定されていません。"

        playlist_id = await self.get_uploads_playlist(channel_id)
        if not playlist_id:
            return False, "チャンネル情報を取得できませんでした。APIキー・チャンネルID・API制限を確認してください。"

        playlist = await self.api_get(
            "playlistItems",
            {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 1},
        )
        if playlist is None:
            return False, "最新動画一覧の取得に失敗しました。BotログのYouTube APIエラーを確認してください。"
        items = playlist.get("items") or []
        if not items:
            return False, "このチャンネルの公開動画を取得できませんでした。"

        video_id = (items[0].get("contentDetails") or {}).get("videoId")
        if not video_id:
            return False, "最新動画のIDがレスポンスにありませんでした。"

        video = await self.fetch_video(video_id)
        if not video or video.get("channel_id") != channel_id:
            return False, "最新動画の詳細取得に失敗したか、チャンネルIDが一致しません。"

        existing = self.database().fetchone(
            "SELECT thread_id FROM deliveries WHERE guild_id=? AND video_id=?",
            (guild_id, video_id),
        )
        if existing:
            return True, (
                f"API取得成功：**{video['title']}**\n"
                f"この動画は既に通知済みです（スレッドID: `{existing['thread_id']}`）。重複投稿は行いません。"
            )

        await self.process_item(
            video, allow_new_video=True, force_new_video=True, target_guild_id=guild_id
        )
        delivered = self.database().fetchone(
            "SELECT thread_id FROM deliveries WHERE guild_id=? AND video_id=?",
            (guild_id, video_id),
        )
        if delivered:
            return True, (
                f"テスト成功：**{video['title']}**\n"
                "YouTube API取得からDiscordフォーラムへのスレッド作成まで完了しました。"
            )
        return False, (
            f"API取得成功：**{video['title']}**\n"
            "Discordへのスレッド作成に失敗しました。Botログ、フォーラム権限、タグ設定を確認してください。"
        )
    async def add_channel(self, guild_id: int, channel_id: str) -> tuple[bool, str]:
        if not CHANNEL_ID_RE.fullmatch(channel_id):
            return False, "チャンネルIDは UC から始まる24文字で指定してください。"

        self.database().execute(
            "INSERT OR IGNORE INTO tracked_channels(guild_id, channel_id) VALUES(?,?)",
            (guild_id, channel_id),
        )
        # Subscribe immediately after adding; renewal follows the lease granted by the hub.
        subscribed = await self.request_websub(channel_id)
        # Prime channel metadata so the first reconciliation does not need to
        # wait for another cycle to resolve the uploads playlist.
        await self.get_uploads_playlist(channel_id)

        if subscribed:
            return True, "監視対象に追加し、WebSub購読リクエストを送信しました。"
        return True, "監視対象に追加しました。WebSub購読は未完了のため、Callback URLと接続状態を確認してください。"

    async def remove_channel(self, guild_id: int, channel_id: str) -> bool:
        self.database().execute(
            "DELETE FROM tracked_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        still_used = self.database().fetchone(
            "SELECT 1 FROM tracked_channels WHERE channel_id=? LIMIT 1",
            (channel_id,),
        )
        if not still_used:
            await self.request_websub(channel_id, mode="unsubscribe")
            self.database().execute(
                "DELETE FROM subscriptions WHERE channel_id=?",
                (channel_id,),
            )
        return True

    @tasks.loop(hours=6)
    async def renew_subscriptions(self) -> None:
        if not CALLBACK_URL or not WEBSUB_SECRET:
            log.warning("Skipping WebSub renewal: callback URL or signature secret is missing.")
            return
        rows = self.database().fetchall(
            "SELECT DISTINCT channel_id FROM tracked_channels"
        )
        now = datetime.now(timezone.utc)
        force_all = self._startup_renewal
        self._startup_renewal = False
        for row in rows:
            channel_id = row["channel_id"]
            if force_all:
                await self.request_websub(channel_id)
                await asyncio.sleep(0.25)
                continue
            subscription = self.database().fetchone(
                "SELECT requested_at, lease_seconds, last_result FROM subscriptions WHERE channel_id=?",
                (channel_id,),
            )
            if subscription and subscription["last_result"] == "verified" and subscription["lease_seconds"]:
                try:
                    granted_at = datetime.fromisoformat(subscription["requested_at"])
                    if granted_at.tzinfo is None:
                        granted_at = granted_at.replace(tzinfo=timezone.utc)
                    lease_seconds = int(subscription["lease_seconds"])
                    expires_at = granted_at + timedelta(seconds=lease_seconds)
                    renew_window = timedelta(
                        seconds=min(48 * 3600, max(6 * 3600, int(lease_seconds * 0.2)))
                    )
                    if expires_at - now > renew_window:
                        continue
                except (TypeError, ValueError):
                    log.warning("Invalid stored WebSub lease for %s; renewing.", channel_id)
            await self.request_websub(channel_id)
            await asyncio.sleep(0.25)

    @renew_subscriptions.before_loop
    async def before_renew_subscriptions(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=20)
    async def reconcile_channels(self) -> None:
        if not API_KEY:
            return
        rows = self.database().fetchall(
            "SELECT DISTINCT channel_id FROM tracked_channels ORDER BY channel_id"
        )
        log.info("Starting 20-minute reconciliation for %d channel(s).", len(rows))
        for row in rows:
            try:
                await self.scan_channel(row["channel_id"])
            except Exception:
                log.exception("Reconciliation failed for channel %s", row["channel_id"])
            await asyncio.sleep(0.3)

    @reconcile_channels.before_loop
    async def before_reconcile_channels(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(YouTubeNotification(bot))
