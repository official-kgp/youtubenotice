from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER PRIMARY KEY,
    forum_id INTEGER NOT NULL,
    upcoming_tag_id INTEGER,
    live_tag_id INTEGER,
    video_tag_id INTEGER,
    short_tag_id INTEGER,
    horizontal_tag_id INTEGER,
    premiere_tag_id INTEGER,
    mention_role_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracked_channels (
    guild_id INTEGER NOT NULL,
    channel_id TEXT NOT NULL,
    channel_title TEXT,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS channel_cache (
    channel_id TEXT PRIMARY KEY,
    uploads_playlist_id TEXT NOT NULL,
    channel_title TEXT,
    initialized INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT NOT NULL,
    channel_title TEXT,
    status TEXT NOT NULL,
    published_at TEXT,
    notification_eligible INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    guild_id INTEGER NOT NULL,
    video_id TEXT NOT NULL,
    thread_id INTEGER NOT NULL,
    last_status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, video_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    channel_id TEXT PRIMARY KEY,
    requested_at TEXT,
    lease_seconds INTEGER,
    last_result TEXT
);

CREATE INDEX IF NOT EXISTS idx_tracked_channels_channel
    ON tracked_channels(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_channel_status
    ON videos(channel_id, status);
"""


class Database:
    """Small synchronous SQLite wrapper; all queries are short local operations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)
        # Lightweight forward migration for databases created by early builds.
        video_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(videos)").fetchall()
        }
        if "notification_eligible" not in video_columns:
            self.connection.execute(
                "ALTER TABLE videos ADD COLUMN notification_eligible INTEGER NOT NULL DEFAULT 1"
            )
        config_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(guild_config)").fetchall()
        }
        for column in ("short_tag_id", "horizontal_tag_id", "premiere_tag_id", "mention_role_id"):
            if column not in config_columns:
                self.connection.execute(f"ALTER TABLE guild_config ADD COLUMN {column} INTEGER")
        self.connection.commit()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        cursor = self.connection.execute(sql, params)
        self.connection.commit()
        return cursor

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def close(self) -> None:
        self.connection.close()
