from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Match the existing Suiten bot convention: secrets are stored in ci/.env.
load_dotenv(dotenv_path="ci/.env")

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
PREFIX = "YN!"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("youtubenotice")


class YouTubeNoticeBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True

        super().__init__(
            command_prefix=PREFIX,
            case_insensitive=True,
            strip_after_prefix=True,
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self) -> None:
        # The notification Cog creates the shared database and HTTP listener.
        # Load it before the config Cog, which accesses that shared service.
        extensions = (
            "cogs.notification.core",
            "cogs.config.panel",
        )
        for extension in extensions:
            try:
                await self.load_extension(extension)
                log.info("Loaded extension: %s", extension)
            except Exception:
                log.exception("Failed to load extension: %s", extension)
                raise

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "unknown")
        log.info("Prefix: %s", PREFIX)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("このコマンドを実行する権限がありません。", mention_author=False)
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply("このコマンドはサーバー内で実行してください。", mention_author=False)
            return
        log.error("Command error", exc_info=(type(error), error, error.__traceback__))
        await ctx.reply("コマンドの実行中にエラーが発生しました。Botログを確認してください。", mention_author=False)


async def main() -> None:
    if not TOKEN:
        raise RuntimeError("ci/.env に DISCORD_BOT_TOKEN を設定してください。")

    async with YouTubeNoticeBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by keyboard interrupt.")
