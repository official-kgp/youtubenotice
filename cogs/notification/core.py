from __future__ import annotations

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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
WEBSub_HUB = "https://pubsubhubbub.appspot.com/subscribe"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}
CHANNEL_ID_RE = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")
VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
DB_PATH = Path(os.getenv("YOUTUBE_DB_PATH", "data/youtube_notice.sqlite3"))
API_KEY = os.getenv("YOUTUBE_API_KEY", "")
CALLBACK_URL = os.getenv("YOUTUBE_CALLBACK_URL", "").rstrip("/")
WEB_HOST = os.getenv("YOUTUBE_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("YOUTUBE_WEB_PORT", "8765"))
SUBSCRIPTION_LEASE = 864000
SCAN_BATCH_SIZE = 50
LATEST_UPLOADS_PER_CHANNEL = 10


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class YouTubeNoticeView(discord.ui.LayoutView):
    """Discord Components V2 notice card."""

    def __init__(self, video_id: str, title: str, channel_title: str, status: str):
        super().__init__(timeout=None)
        url = f"https://www.youtube.com/watch?v={video_id}"
        heading = {
            "upcoming": "🔔 配信予定",
            "live": "🔴 ライブ配信中",
            "video": "🎬 新着動画",
        }.get(status, "YouTube更新")

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(f"## {heading}"))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"**{discord.utils.escape_markdown(title)[:240]}**"))
        container.add_item(discord.ui.TextDisplay(
            f"チャンネル：{discord.utils.escape_markdown(channel_title)[:100]}"
        ))
        container.add_item(discord.ui.TextDisplay(f"[YouTubeで開く]({url})"))
        container.add_item(discord.ui.ActionRow(
            discord.ui.Button(
                label="YouTubeを開く",
                style=discord.ButtonStyle.link,
                url=url,
            )
        ))
        container.add_item(discord.ui.TextDisplay("-# YouTubeNotice · かざま隊の集いの場"))
        self.add_item(container)


class YouTubeNotification(commands.Cog):
    """WebSub receiver, periodic reconciliation, and Forum delivery core."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database | None = None
        self.session: aiohttp.ClientSession | None = None
        self.web_runner: web.AppRunner | None = None
        self.process_lock = asyncio.Lock()

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

        if mode == "subscribe":
            tracked = self.database().fetchone(
                "SELECT 1 FROM tracked_channels WHERE channel_id=? LIMIT 1",
                (channel_id,),
            )
            if not tracked:
                raise web.HTTPForbidden(text="Channel is not configured.")

        return web.Response(text=challenge, content_type="text/plain")

    async def websub_receive(self, request: web.Request) -> web.Response:
        try:
            raw = await request.read()
            root = ET.fromstring(raw)
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

        form = {
            "hub.callback": f"{CALLBACK_URL}/websub",
            "hub.topic": f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "hub.verify": "async",
            "hub.mode": mode,
        }
        if mode == "subscribe":
            form["hub.lease_seconds"] = str(SUBSCRIPTION_LEASE)

        try:
            async with self.session.post(WEBSub_HUB, data=form) as response:
                body = await response.text()
                if response.status not in (202, 204):
                    log.warning(
                        "WebSub %s request failed for %s: HTTP %s %s",
                        mode, channel_id, response.status, body[:300],
                    )
                    self.database().execute(
                        "INSERT OR REPLACE INTO subscriptions(channel_id, requested_at, lease_seconds, last_result) VALUES(?,?,?,?)",
                        (channel_id, utc_now(), SUBSCRIPTION_LEASE if mode == "subscribe" else 0, f"HTTP {response.status}"),
                    )
                    return False

            self.database().execute(
                "INSERT OR REPLACE INTO subscriptions(channel_id, requested_at, lease_seconds, last_result) VALUES(?,?,?,?)",
                (channel_id, utc_now(), SUBSCRIPTION_LEASE if mode == "subscribe" else 0, "accepted"),
            )
            return True
        except (aiohttp.ClientError, asyncio.TimeoutError):
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
        payload = await self.api_get("videos", {"part": "snippet", "id": video_id})
        items = (payload or {}).get("items") or []
        if not items:
            return None
        return self.normalize_video(items[0])

    @staticmethod
    def normalize_video(raw: dict[str, Any]) -> dict[str, Any]:
        snippet = raw.get("snippet", {})
        live_state = snippet.get("liveBroadcastContent", "none")
        status = live_state if live_state in {"upcoming", "live"} else "video"
        return {
            "video_id": raw.get("id"),
            "channel_id": snippet.get("channelId"),
            "title": snippet.get("title") or "タイトル未取得",
            "channel_title": snippet.get("channelTitle") or "YouTube",
            "status": status,
            "published_at": snippet.get("publishedAt"),
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
                {"part": "snippet", "id": ",".join(batch)},
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
            old_status = previous["status"] if previous else None
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
                """SELECT tc.guild_id, gc.forum_id, gc.upcoming_tag_id,
                          gc.live_tag_id, gc.video_tag_id
                   FROM tracked_channels tc
                   JOIN guild_config gc ON gc.guild_id=tc.guild_id
                   WHERE tc.channel_id=?""",
                (channel_id,),
            )

            for config in configs:
                delivery = self.database().fetchone(
                    "SELECT thread_id, last_status FROM deliveries WHERE guild_id=? AND video_id=?",
                    (config["guild_id"], video_id),
                )
                if delivery:
                    if delivery["last_status"] == "upcoming" and item["status"] == "live":
                        updated = await self.update_live_thread(config, delivery["thread_id"], item)
                        if updated:
                            self.database().execute(
                                "UPDATE deliveries SET last_status='live' WHERE guild_id=? AND video_id=?",
                                (config["guild_id"], video_id),
                            )
                    continue

                should_create = item["status"] in {"upcoming", "live"}
                if item["status"] == "video":
                    should_create = eligible
                if should_create:
                    await self.create_forum_thread(config, item)

    async def create_forum_thread(self, config: Any, item: dict[str, Any]) -> None:
        guild = self.bot.get_guild(config["guild_id"])
        if guild is None:
            return
        forum = guild.get_channel(config["forum_id"])
        if not isinstance(forum, discord.ForumChannel):
            log.warning("Configured destination is not a ForumChannel: guild=%s", guild.id)
            return

        status = item["status"]
        tag_id = (
            config["upcoming_tag_id"] if status == "upcoming"
            else config["live_tag_id"] if status == "live"
            else config["video_tag_id"]
        )
        applied_tags = [
            tag for tag in forum.available_tags
            if tag_id is not None and tag.id == tag_id
        ]
        thread_name = discord.utils.escape_markdown(item["title"])[:90].strip()
        if not thread_name:
            thread_name = f"YouTube {item['video_id']}"

        try:
            thread, _starter = await forum.create_thread(
                name=thread_name,
                view=YouTubeNoticeView(
                    item["video_id"], item["title"], item["channel_title"], status,
                ),
                applied_tags=applied_tags,
                reason="YouTubeNotice automatic notification",
            )
        except discord.HTTPException:
            log.exception("Failed to create Forum thread in guild %s", guild.id)
            return

        self.database().execute(
            """INSERT OR IGNORE INTO deliveries(guild_id, video_id, thread_id, last_status)
               VALUES(?,?,?,?)""",
            (guild.id, item["video_id"], thread.id, status),
        )

    async def update_live_thread(self, config: Any, thread_id: int, item: dict[str, Any]) -> bool:
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

        try:
            await thread.send(
                view=YouTubeNoticeView(
                    item["video_id"], item["title"], item["channel_title"], "live",
                )
            )
            forum = guild.get_channel(config["forum_id"])
            live_tag_id = config["live_tag_id"]
            if isinstance(forum, discord.ForumChannel) and live_tag_id:
                live_tag = next(
                    (tag for tag in forum.available_tags if tag.id == live_tag_id),
                    None,
                )
                if live_tag:
                    tags = [
                        tag for tag in thread.applied_tags
                        if tag.id != config["upcoming_tag_id"]
                    ]
                    try:
                        await thread.edit(applied_tags=[*tags, live_tag])
                    except discord.HTTPException:
                        log.exception("Live tag update failed for thread %s", thread_id)
            return True
        except discord.HTTPException:
            log.exception("Failed to update live status for thread %s", thread_id)
            return False

    async def add_channel(self, guild_id: int, channel_id: str) -> tuple[bool, str]:
        if not CHANNEL_ID_RE.fullmatch(channel_id):
            return False, "チャンネルIDは UC から始まる24文字で指定してください。"

        self.database().execute(
            "INSERT OR IGNORE INTO tracked_channels(guild_id, channel_id) VALUES(?,?)",
            (guild_id, channel_id),
        )
        # Subscribe immediately after adding. The 72-hour task is only renewal.
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

    @tasks.loop(hours=72)
    async def renew_subscriptions(self) -> None:
        if not CALLBACK_URL:
            log.warning("Skipping subscription renewal: YOUTUBE_CALLBACK_URL is missing.")
            return
        rows = self.database().fetchall(
            "SELECT DISTINCT channel_id FROM tracked_channels"
        )
        for row in rows:
            await self.request_websub(row["channel_id"])
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
