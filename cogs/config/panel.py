from __future__ import annotations

import asyncio
import logging
import re

import discord
from discord.ext import commands

log = logging.getLogger("youtubenotice.config")
CLEAR_VALUES = {"解除", "off", "none", "clear", "未設定"}
ROLE_FIELDS = {
    "mention_role_id": "通知メンションロール",
    "dm_notify_role_id": "DM通知ロール",
    "live_notify_role_id": "LIVE・プレミア公開ロール",
    "video_notify_role_id": "横動画・Shortsロール",
    "all_notify_role_id": "全通知ロール",
}
TAG_FIELDS = {
    "short_tag_id": "Shortsタグ",
    "horizontal_tag_id": "横動画タグ",
    "live_tag_id": "LIVEタグ",
    "premiere_tag_id": "プレミア公開タグ",
}
ID_FIELDS = {
    "forum_id": "通知先フォーラム",
    "mention_role_id": "通知メンションロール",
    "dm_notify_role_id": "DM通知ロール",
    "live_notify_role_id": "LIVE・プレミア公開ロール",
    "video_notify_role_id": "横動画・Shortsロール",
    "all_notify_role_id": "全通知ロール",
    "error_log_channel_id": "エラーログ送信先",
    "short_tag_id": "Shortsタグ",
    "horizontal_tag_id": "横動画タグ",
    "live_tag_id": "LIVEタグ",
    "premiere_tag_id": "プレミア公開タグ",
}


def snowflake(value: str) -> int | None:
    match = re.search(r"\d{15,22}", value or "")
    return int(match.group(0)) if match else None


def is_clear(value: str) -> bool:
    return value.strip().lower() in CLEAR_VALUES


def display_id(value: int | None, kind: str) -> str:
    if not value:
        return "未設定"
    if kind == "role":
        return f"<@&{value}>"
    if kind == "channel":
        return f"<#{value}>"
    return str(value)


class ConfigInputButton(discord.ui.Button):
    def __init__(self, cog, guild_id: int, field: str):
        current = cog.get_config_value(guild_id, field)
        super().__init__(
            label=ID_FIELDS[field] + (": 変更する" if current else ": 設定する"),
            style=discord.ButtonStyle.primary,
            custom_id=f"yn:config:input:{field}",
        )
        self.cog = cog
        self.field = field

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await self.cog.run_config_input(interaction, self.field)
        except Exception:
            log.exception("Config input interaction failed for field %s.", self.field)
            await interaction.followup.send("❌ 設定処理中にエラーが発生しました。Botログを確認してください。")


class MemberPanelTargetSelect(discord.ui.ChannelSelect):
    def __init__(self, cog, guild_id: int, selected_channel_id: int | None = None):
        self.cog = cog
        self.guild_id = guild_id
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="通知設定パネルの送信先チャンネルを選択",
            min_values=1, max_values=1, custom_id="yn:config:memberpanel:target",
        )
        if selected_channel_id:
            self.default_values = [discord.SelectDefaultValue(
                id=selected_channel_id, type=discord.SelectDefaultValueType.channel
            )]

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        target = self.values[0]
        self.cog._member_panel_targets[self.guild_id] = target.id
        await interaction.response.edit_message(
            view=YouTubeConfigPanel(self.cog, self.guild_id, selected_member_panel_channel_id=target.id)
        )


class PublishMemberPanelButton(discord.ui.Button):
    def __init__(self, cog):
        super().__init__(label="選択したチャンネルに送信・更新", style=discord.ButtonStyle.success,
                         custom_id="yn:config:memberpanel:publish")
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        await interaction.response.defer()
        channel_id = self.cog._member_panel_targets.get(interaction.guild.id)
        target = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(target, (discord.TextChannel, discord.NewsChannel)):
            await interaction.followup.send("先に送信先チャンネルを選択してください。")
            return
        member_cog = self.cog.bot.get_cog("YouTubeMemberPreferences")
        if member_cog is None:
            await interaction.followup.send("メンバー通知機能が読み込まれていません。")
            return
        ok, result = await member_cog.publish_panel(interaction.guild, target)
        if ok:
            await self.cog.refresh_panel(interaction)
        await interaction.followup.send(("✅ " if ok else "❌ ") + result)


class ConfigOperationSelect(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        options = [
            discord.SelectOption(label="YouTubeチャンネルを追加", value="add", emoji="➕"),
            discord.SelectOption(label="YouTubeチャンネルを削除", value="remove", emoji="➖"),
            discord.SelectOption(label="最新動画でテスト通知", value="test", emoji="🧪"),
        ]
        super().__init__(placeholder="その他の操作を選択してください", min_values=1, max_values=1,
                         options=options, custom_id="yn:config:operation")

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        action = self.values[0]
        await interaction.response.defer()
        try:
            if action in {"add", "remove", "test"}:
                await self.cog.run_channel_operation(interaction, action)
                return
        except Exception:
            log.exception("Config operation failed: %s", action)
            await interaction.followup.send("❌ 操作中にエラーが発生しました。Botログを確認してください。")
            return


class YouTubeConfigPanel(discord.ui.LayoutView):
    def __init__(self, cog, guild_id: int, selected_member_panel_channel_id: int | None = None):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        cfg = cog.db.fetchone(
            """SELECT forum_id, mention_role_id, error_log_channel_id,
                      dm_notify_role_id, live_notify_role_id, video_notify_role_id,
                      all_notify_role_id, short_tag_id, horizontal_tag_id,
                      live_tag_id, premiere_tag_id, member_panel_channel_id,
                      member_panel_message_id
               FROM guild_config WHERE guild_id=?""", (guild_id,)
        )
        channels = cog.db.fetchall(
            "SELECT channel_id, channel_title FROM tracked_channels WHERE guild_id=? ORDER BY channel_title, channel_id",
            (guild_id,)
        )

        def value(field, kind):
            return display_id(cfg[field] if cfg else None, kind)

        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("# YouTubeNotice | 通知・サーバー設定"))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.TextDisplay(
            "## 通知先\n**フォーラム：** " + value("forum_id", "channel")
            + "\n\n## 通知ロール\n"
            + "\n".join([
                "**メンション：** " + value("mention_role_id", "role"),
                "**DM：** " + value("dm_notify_role_id", "role"),
                "**LIVE：** " + value("live_notify_role_id", "role"),
                "**横動画・Shorts：** " + value("video_notify_role_id", "role"),
                "**全通知：** " + value("all_notify_role_id", "role"),
            ])
            + "\n\n## 運用設定\n**エラーログ：** " + value("error_log_channel_id", "channel")
        ))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.TextDisplay("### 通知先・ロールの設定"))
        card.add_item(discord.ui.ActionRow(ConfigInputButton(cog, guild_id, "forum_id")))
        card.add_item(discord.ui.ActionRow(
            ConfigInputButton(cog, guild_id, "mention_role_id"),
            ConfigInputButton(cog, guild_id, "dm_notify_role_id"),
            ConfigInputButton(cog, guild_id, "live_notify_role_id"),
        ))
        card.add_item(discord.ui.ActionRow(
            ConfigInputButton(cog, guild_id, "video_notify_role_id"),
            ConfigInputButton(cog, guild_id, "all_notify_role_id"),
            ConfigInputButton(cog, guild_id, "error_log_channel_id"),
        ))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.TextDisplay(
            "### フォーラムタグ\n"
            + "\n".join(
                "**" + label + "：** " + value(field, "tag")
                for field, label in TAG_FIELDS.items()
            )
        ))
        card.add_item(discord.ui.ActionRow(
            ConfigInputButton(cog, guild_id, "short_tag_id"),
            ConfigInputButton(cog, guild_id, "horizontal_tag_id"),
            ConfigInputButton(cog, guild_id, "live_tag_id"),
            ConfigInputButton(cog, guild_id, "premiere_tag_id"),
        ))
        self.add_item(card)
        card = discord.ui.Container()
        card.add_item(discord.ui.Separator())
        channel_lines = [
            "• " + (row["channel_title"] or row["channel_id"]) + " (" + row["channel_id"] + ")"
            for row in channels[:15]
        ] or ["未登録"]
        if len(channels) > 15:
            channel_lines.append("ほか " + str(len(channels) - 15) + " チャンネル")
        member_panel = (
            f"<#{cfg['member_panel_channel_id']}>"
            if cfg and cfg["member_panel_channel_id"] and cfg["member_panel_message_id"] else "未設置"
        )
        card.add_item(discord.ui.TextDisplay(
            "### 監視チャンネル（" + str(len(channels)) + "件）\n"
            + "\n".join(channel_lines)
            + "\n\n**メンバー向け通知パネル：** " + member_panel
        ))
        selected_target = f"<#{selected_member_panel_channel_id}>" if selected_member_panel_channel_id else "未選択"
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.TextDisplay("### メンバー向け通知パネルの送信先\n**選択中：** " + selected_target))
        card.add_item(discord.ui.ActionRow(MemberPanelTargetSelect(cog, guild_id, selected_member_panel_channel_id)))
        card.add_item(discord.ui.ActionRow(PublishMemberPanelButton(cog)))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.ActionRow(ConfigOperationSelect(cog)))
        card.add_item(discord.ui.TextDisplay(
            "-# 設定ボタンを押すと、このチャンネルに入力案内が届きます。変更後はこのパネル自体が更新されます。"
        ))
        self.add_item(card)


class YouTubeConfig(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._input_sessions: set[tuple[int, int, int]] = set()
        self._member_panel_targets: dict[int, int] = {}

    @property
    def db(self):
        db = getattr(self.bot, "youtube_db", None)
        if db is None:
            raise RuntimeError("Notification database is not ready.")
        return db

    def get_config_value(self, guild_id: int, field: str):
        if field not in ID_FIELDS:
            return None
        row = self.db.fetchone(f"SELECT {field} FROM guild_config WHERE guild_id=?", (guild_id,))
        return row[field] if row else None

    async def cog_load(self):
        # The message itself is created by YN!youtubectrl; persistent IDs are registered here.
        self.bot.add_view(YouTubeConfigPanel(self, 0))

    async def refresh_panel(self, interaction: discord.Interaction):
        if interaction.message and interaction.guild:
            selected_id = self._member_panel_targets.get(interaction.guild.id)
            await interaction.message.edit(
                view=YouTubeConfigPanel(
                    self, interaction.guild.id,
                    selected_member_panel_channel_id=selected_id,
                )
            )

    async def read_channel_message(self, interaction: discord.Interaction, title: str) -> str | None:
        guild, channel = interaction.guild, interaction.channel
        if guild is None or channel is None:
            return None
        key = (guild.id, interaction.user.id, channel.id)
        if key in self._input_sessions:
            await interaction.followup.send("このチャンネルでは既に入力待ちです。")
            return None
        self._input_sessions.add(key)
        prompt = None
        try:
            prompt = await channel.send(
                "**" + title + "を送信してください▶︎**\n"
                "入力内容をこのチャンネルに送信してください。解除する場合は「解除」と入力します。（120秒以内）"
            )
            def check(message: discord.Message):
                return message.author.id == interaction.user.id and message.channel.id == channel.id and message.guild is not None and message.guild.id == guild.id
            try:
                message = await self.bot.wait_for("message", check=check, timeout=120)
            except asyncio.TimeoutError:
                await channel.send("⌛ 入力がタイムアウトしました。設定は変更されていません。")
                return None
            raw = message.content.strip()
            try:
                await message.delete()
            except discord.Forbidden:
                await channel.send("⚠️ 入力メッセージを削除できません。Botにメッセージ管理権限を付与してください。")
            except discord.HTTPException:
                log.warning("Could not delete config input message %s", message.id)
            return raw
        finally:
            self._input_sessions.discard(key)
            if prompt:
                try:
                    await prompt.delete()
                except discord.HTTPException:
                    pass

    async def run_config_input(self, interaction: discord.Interaction, field: str):
        guild = interaction.guild
        if guild is None or field not in ID_FIELDS:
            return
        if field != "forum_id" and not self.db.fetchone("SELECT 1 FROM guild_config WHERE guild_id=?", (guild.id,)):
            await interaction.followup.send("最初に通知先フォーラムを設定してください。")
            return
        raw = await self.read_channel_message(interaction, ID_FIELDS[field])
        if raw is None:
            return
        if is_clear(raw):
            if field == "forum_id":
                await interaction.followup.send("通知先フォーラムは解除できません。")
                return
            self.db.execute(
                f"UPDATE guild_config SET {field}=NULL, updated_at=CURRENT_TIMESTAMP WHERE guild_id=?",
                (guild.id,)
            )
            await self.refresh_panel(interaction)
            await interaction.followup.send("✅ " + ID_FIELDS[field] + "を解除しました。")
            return

        if field == "forum_id":
            target_id = snowflake(raw)
            target = guild.get_channel(target_id) if target_id else None
            if target is None and target_id:
                try:
                    target = await guild.fetch_channel(target_id)
                except discord.HTTPException:
                    target = None
            if not isinstance(target, discord.ForumChannel):
                await interaction.followup.send("❌ このサーバーのフォーラムチャンネルIDまたはメンションを入力してください。")
                return
            self.db.execute(
                """INSERT INTO guild_config(guild_id, forum_id) VALUES(?,?)
                   ON CONFLICT(guild_id) DO UPDATE SET forum_id=excluded.forum_id, updated_at=CURRENT_TIMESTAMP""",
                (guild.id, target.id)
            )
        elif field in ROLE_FIELDS:
            role_id = snowflake(raw)
            role = guild.get_role(role_id) if role_id else None
            if role is None:
                await interaction.followup.send("❌ このサーバーに存在するロールIDまたはロールメンションを入力してください。")
                return
            self.db.execute(
                f"UPDATE guild_config SET {field}=?, updated_at=CURRENT_TIMESTAMP WHERE guild_id=?",
                (role.id, guild.id)
            )
        elif field == "error_log_channel_id":
            channel_id = snowflake(raw)
            target = guild.get_channel(channel_id) if channel_id else None
            if target is None and channel_id:
                try:
                    target = await guild.fetch_channel(channel_id)
                except discord.HTTPException:
                    target = None
            if not isinstance(target, discord.TextChannel):
                await interaction.followup.send("❌ このサーバーのテキストチャンネルIDまたはメンションを入力してください。")
                return
            self.db.execute(
                "UPDATE guild_config SET error_log_channel_id=?, updated_at=CURRENT_TIMESTAMP WHERE guild_id=?",
                (target.id, guild.id)
            )
        elif field in TAG_FIELDS:
            cfg = self.db.fetchone("SELECT forum_id FROM guild_config WHERE guild_id=?", (guild.id,))
            forum = guild.get_channel(cfg["forum_id"]) if cfg else None
            if not isinstance(forum, discord.ForumChannel):
                await interaction.followup.send("❌ 先に通知先フォーラムを設定してください。")
                return
            tag = next((item for item in forum.available_tags if item.name == raw), None)
            if tag is None:
                tag_id = snowflake(raw)
                tag = next((item for item in forum.available_tags if item.id == tag_id), None) if tag_id else None
            if tag is None:
                await interaction.followup.send("❌ フォーラムに存在するタグ名またはタグIDを入力してください。")
                return
            self.db.execute(
                f"UPDATE guild_config SET {field}=?, updated_at=CURRENT_TIMESTAMP WHERE guild_id=?",
                (tag.id, guild.id)
            )
        await self.refresh_panel(interaction)
        await interaction.followup.send("✅ " + ID_FIELDS[field] + "を更新しました。")

    async def run_channel_operation(self, interaction: discord.Interaction, action: str):
        guild = interaction.guild
        if guild is None:
            return
        if not self.db.fetchone("SELECT 1 FROM guild_config WHERE guild_id=?", (guild.id,)):
            await interaction.followup.send("最初に通知先フォーラムを設定してください。")
            return
        core = self.bot.get_cog("YouTubeNotification")
        if core is None:
            await interaction.followup.send("通知コアが起動していません。")
            return
        labels = {
            "add": "追加するYouTubeチャンネルのID・@ハンドル・チャンネルURL",
            "remove": "削除するYouTubeチャンネルのID・@ハンドル・チャンネルURL",
            "test": "テスト通知するYouTubeチャンネルのID・@ハンドル・チャンネルURL",
        }
        raw = await self.read_channel_message(interaction, labels[action])
        if raw is None:
            return
        channel_id = await core.resolve_channel_id(raw)
        if not channel_id:
            await interaction.followup.send("❌ UCから始まるID、@ハンドル、またはYouTubeチャンネルURLを確認してください。")
            return
        if action == "add":
            ok, result = await core.add_channel(guild.id, channel_id)
        elif action == "remove":
            exists = self.db.fetchone("SELECT 1 FROM tracked_channels WHERE guild_id=? AND channel_id=?", (guild.id, channel_id))
            if not exists:
                ok, result = False, "このサーバーでは監視されていないチャンネルです。"
            else:
                await core.remove_channel(guild.id, channel_id)
                ok, result = True, "監視対象から削除しました。"
        else:
            ok, result = await core.test_latest_video(guild.id, channel_id)
        await self.refresh_panel(interaction)
        await interaction.followup.send(("✅ " if ok else "❌ ") + result)

    @commands.command(name="youtubectrl", aliases=["ynconfig"])
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def youtubectrl(self, ctx: commands.Context):
        await ctx.send(view=YouTubeConfigPanel(self, ctx.guild.id))


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTubeConfig(bot))
