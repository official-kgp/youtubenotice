from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands

log = logging.getLogger("youtubenotice.preferences")

CATEGORIES = {
    "dm": ("DM通知", "DMで通知を受け取ります。"),
    "live": ("LIVE・プレミア公開", "配信開始時のみ通知します。"),
    "video": ("横動画・Shorts", "通常公開された横動画・Shortsを通知します。"),
    "all": ("すべての通知", "配信枠の作成・配信開始・通常動画公開を通知します。"),
}
ROLE_COLUMNS = {
    "dm": "dm_notify_role_id",
    "live": "live_notify_role_id",
    "video": "video_notify_role_id",
    "all": "all_notify_role_id",
}


class PublicPanelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="通知をカスタマイズする",
            style=discord.ButtonStyle.primary,
            custom_id="yn:member-preferences:open",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("YouTubeMemberPreferences")
        if cog is None or interaction.guild is None:
            await interaction.response.send_message(
                "このパネルはサーバー内で使用してください。", ephemeral=True
            )
            return
        channels = cog.db.fetchall(
            """SELECT channel_id, channel_title FROM tracked_channels
               WHERE guild_id=? ORDER BY channel_title, channel_id""",
            (interaction.guild.id,),
        )
        if not channels:
            await interaction.response.send_message(
                "現在、通知設定が可能なYouTubeチャンネルはありません。", ephemeral=True
            )
            return
        view = ChannelSelectView(cog, interaction.guild.id, channels)
        await interaction.response.send_message(
            "## YouTube通知設定\n設定を変更するチャンネルを選択してください。",
            view=view,
            ephemeral=True,
        )
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            pass


class PublicNotificationPanel(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("# YouTubeNotice | 通知設定"))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.TextDisplay(
            "登録されているYouTubeチャンネルごとに、受け取りたい通知を設定できます。\n"
            "設定画面は本人にのみ表示されます。他のメンバーの操作と干渉せず、同時に利用できます。"
        ))
        card.add_item(discord.ui.ActionRow(PublicPanelButton()))
        card.add_item(discord.ui.TextDisplay("-# YouTubeNotice · Member Notification Preferences"))
        self.add_item(card)


class PreferenceCategorySelect(discord.ui.Select):
    def __init__(self, view: "CategorySelectView"):
        self.parent_view = view
        options = [
            discord.SelectOption(label=label, value=key, description=description)
            for key, (label, description) in CATEGORIES.items()
        ]
        super().__init__(
            placeholder="変更する通知カテゴリを選択",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="yn:member-preferences:category",
        )

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        await interaction.response.edit_message(
            content=f"## {CATEGORIES[key][0]}\nこのチャンネルの通知設定を変更します。",
            view=PreferenceEditorView(
                self.parent_view.cog, self.parent_view.guild_id,
                interaction.user.id, self.parent_view.channel_id, key,
            ),
        )


class ChannelSelect(discord.ui.Select):
    def __init__(self, view: "ChannelSelectView", channels: list[Any], offset: int = 0):
        self.parent_view = view
        self.offset = offset
        page = channels[offset:offset + 25]
        options = [
            discord.SelectOption(
                label=(row["channel_title"] or row["channel_id"])[:100],
                value=row["channel_id"],
                description=row["channel_id"][:100],
            )
            for row in page
        ]
        super().__init__(
            placeholder="YouTubeチャンネルを選択",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"yn:member-preferences:channel:{offset}",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="## 通知カテゴリ\n設定したい通知カテゴリを選択してください。",
            view=CategorySelectView(
                self.parent_view.cog, self.parent_view.guild_id,
                interaction.user.id, self.values[0],
            ),
        )


class ChannelPageButton(discord.ui.Button):
    def __init__(self, parent: "ChannelSelectView", offset: int, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.parent = parent
        self.offset = offset

    async def callback(self, interaction: discord.Interaction):
        channels = self.parent.channels
        await interaction.response.edit_message(
            content="## YouTube通知設定\n設定を変更するチャンネルを選択してください。",
            view=ChannelSelectView(
                self.parent.cog, self.parent.guild_id, channels, self.offset
            ),
        )


class ChannelSelectView(discord.ui.LayoutView):
    def __init__(self, cog, guild_id: int, channels: list[Any], offset: int = 0):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.channels = channels
        self.message = None
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("### チャンネル選択"))
        card.add_item(discord.ui.ActionRow(ChannelSelect(self, channels, offset)))
        if len(channels) > 25:
            buttons = []
            if offset > 0:
                buttons.append(ChannelPageButton(self, max(0, offset - 25), "前へ"))
            if offset + 25 < len(channels):
                buttons.append(ChannelPageButton(self, offset + 25, "次へ"))
            if buttons:
                card.add_item(discord.ui.ActionRow(*buttons))
            card.add_item(discord.ui.TextDisplay(
                f"-# {offset + 1}〜{min(offset + 25, len(channels))} / {len(channels)}チャンネル"
            ))
        self.add_item(card)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(
                    content="設定画面の有効期限が切れました。公開パネルから再度開いてください。",
                    view=None,
                )
            except discord.HTTPException:
                pass


class CategorySelectView(discord.ui.LayoutView):
    def __init__(self, cog, guild_id: int, user_id: int, channel_id: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.channel_id = channel_id
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("### 通知カテゴリ"))
        card.add_item(discord.ui.TextDisplay(
            "変更するカテゴリを選択してください。各カテゴリは個別に有効・無効を設定できます。"
        ))
        card.add_item(discord.ui.ActionRow(PreferenceCategorySelect(self)))
        card.add_item(discord.ui.ActionRow(
            discord.ui.Button(
                label="チャンネル選択に戻る", style=discord.ButtonStyle.secondary,
                custom_id="yn:member-preferences:back-channels",
            )
        ))
        self.add_item(card)
        self.children[-1].children[0].callback = self.back_to_channels

    async def back_to_channels(self, interaction: discord.Interaction):
        rows = self.cog.db.fetchall(
            """SELECT channel_id, channel_title FROM tracked_channels
               WHERE guild_id=? ORDER BY channel_title, channel_id""",
            (self.guild_id,),
        )
        await interaction.response.edit_message(
            content="## YouTube通知設定\n設定を変更するチャンネルを選択してください。",
            view=ChannelSelectView(self.cog, self.guild_id, rows),
        )


class PreferenceToggleButton(discord.ui.Button):
    def __init__(self, parent: "PreferenceEditorView", enabled: bool):
        super().__init__(
            label="通知をOFFにする" if enabled else "通知をONにする",
            style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
        )
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent.toggle(interaction)


class PreferenceEditorView(discord.ui.LayoutView):
    def __init__(self, cog, guild_id: int, user_id: int, channel_id: str, category: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.channel_id = channel_id
        self.category = category
        row = self._get_row()
        enabled = bool(row[category]) if row else False
        channel = cog.db.fetchone(
            "SELECT channel_title FROM tracked_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        channel_title = channel["channel_title"] if channel else channel_id
        label, description = CATEGORIES[category]
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay(f"### {label}"))
        card.add_item(discord.ui.TextDisplay(
            f"**対象チャンネル：** {channel_title}\n{description}\n\n"
            f"**現在の設定：** {'有効' if enabled else '無効'}"
        ))
        card.add_item(discord.ui.ActionRow(PreferenceToggleButton(self, enabled)))
        card.add_item(discord.ui.ActionRow(
            discord.ui.Button(
                label="チャンネル選択に戻る", style=discord.ButtonStyle.secondary,
                custom_id="yn:member-preferences:back",
            ),
            discord.ui.Button(
                label="終了する", style=discord.ButtonStyle.primary,
                custom_id="yn:member-preferences:finish",
            ),
        ))
        self.add_item(card)
        buttons = self.children[-1].children
        buttons[0].callback = self.back_to_channels
        buttons[1].callback = self.finish

    def _get_row(self):
        return self.cog.db.fetchone(
            """SELECT dm, live, video, all_notifications
               FROM user_notification_preferences
               WHERE guild_id=? AND user_id=? AND channel_id=?""",
            (self.guild_id, self.user_id, self.channel_id),
        )

    async def toggle(self, interaction: discord.Interaction):
        column = "all_notifications" if self.category == "all" else self.category
        row = self._get_row()
        current = bool(row[column]) if row else False
        new_value = 0 if current else 1
        self.cog.db.execute(
            f"""INSERT INTO user_notification_preferences
                (guild_id,user_id,channel_id,{column},updated_at)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id,user_id,channel_id) DO UPDATE SET
                {column}=excluded.{column}, updated_at=CURRENT_TIMESTAMP""",
            (self.guild_id, self.user_id, self.channel_id, new_value),
        )
        role_ok, role_message = await self.cog.sync_category_role(
            interaction.guild, interaction.user, self.category, bool(new_value)
        )
        updated = PreferenceEditorView(
            self.cog, self.guild_id, self.user_id, self.channel_id, self.category
        )
        content = f"設定を{'有効' if new_value else '無効'}にしました。"
        if not role_ok:
            content += f"\n{role_message}"
        await interaction.response.edit_message(content=content, view=updated)

    async def back_to_channels(self, interaction: discord.Interaction):
        rows = self.cog.db.fetchall(
            """SELECT channel_id, channel_title FROM tracked_channels
               WHERE guild_id=? ORDER BY channel_title, channel_id""",
            (self.guild_id,),
        )
        await interaction.response.edit_message(
            content="## YouTube通知設定\n設定を変更するチャンネルを選択してください。",
            view=ChannelSelectView(self.cog, self.guild_id, rows),
        )

    async def finish(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="通知設定を終了しました。設定内容は保存されています。",
            view=None,
        )


class YouTubeMemberPreferences(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        db = getattr(self.bot, "youtube_db", None)
        if db is None:
            raise RuntimeError("Notification database is not ready.")
        return db

    async def cog_load(self):
        self.bot.add_view(PublicNotificationPanel())

    async def sync_category_role(
        self, guild: discord.Guild | None, user: discord.abc.User,
        category: str, enabled: bool,
    ) -> tuple[bool, str]:
        if guild is None:
            return False, "サーバー情報を取得できませんでした。"
        config = self.db.fetchone(
            f"SELECT {ROLE_COLUMNS[category]} AS role_id FROM guild_config WHERE guild_id=?",
            (guild.id,),
        )
        role_id = config["role_id"] if config else None
        if not role_id:
            return False, "対応する通知ロールが未設定のため、ロール同期は行われていません。"
        role = guild.get_role(role_id)
        if role is None:
            return False, "設定された通知ロールが見つかりません。"
        member = user if isinstance(user, discord.Member) else guild.get_member(user.id)
        if member is None:
            try:
                member = await guild.fetch_member(user.id)
            except discord.HTTPException:
                return False, "メンバー情報を取得できませんでした。"
        try:
            if enabled and role not in member.roles:
                await member.add_roles(role, reason="YouTubeNotice notification preference enabled")
            elif not enabled and role in member.roles:
                await member.remove_roles(role, reason="YouTubeNotice notification preference disabled")
            return True, "通知ロールを同期しました。"
        except discord.Forbidden:
            return False, "Botのロール管理権限、またはBotのロール位置を確認してください。"
        except discord.HTTPException:
            log.exception("Could not sync notification role.")
            return False, "通知ロールの同期に失敗しました。"

    async def dispatch_dm(self, guild_id: int, item: dict[str, Any], event_kind: str):
        """Send one DM per user/video/event, without touching Forum delivery history."""
        if event_kind not in {"upcoming", "live", "video"}:
            return
        rows = self.db.fetchall(
            """SELECT user_id, dm, live, video, all_notifications
               FROM user_notification_preferences WHERE guild_id=? AND channel_id=?""",
            (guild_id, item["channel_id"]),
        )
        for row in rows:
            enabled = (
                bool(row["dm"]) or bool(row["all_notifications"])
                or (event_kind == "live" and bool(row["live"]))
                or (event_kind == "video" and bool(row["video"]))
            )
            if not enabled:
                continue
            exists = self.db.fetchone(
                """SELECT 1 FROM user_notification_deliveries
                   WHERE guild_id=? AND user_id=? AND video_id=? AND event_kind=?""",
                (guild_id, row["user_id"], item["video_id"], event_kind),
            )
            if exists:
                continue
            try:
                user = self.bot.get_user(row["user_id"]) or await self.bot.fetch_user(row["user_id"])
                await user.send(view=__import__(
                    "cogs.notification.core", fromlist=["notification_layout"]
                ).notification_layout(item))
                self.db.execute(
                    """INSERT OR IGNORE INTO user_notification_deliveries
                       (guild_id,user_id,video_id,event_kind,created_at)
                       VALUES(?,?,?,?,CURRENT_TIMESTAMP)""",
                    (guild_id, row["user_id"], item["video_id"], event_kind),
                )
            except discord.Forbidden:
                log.info("DM is closed for user %s.", row["user_id"])
            except discord.HTTPException:
                log.exception("Could not send notification DM to user %s.", row["user_id"])

    @commands.command(name="youtubepanel")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def youtubepanel(self, ctx: commands.Context):
        existing = self.db.fetchone(
            """SELECT member_panel_channel_id, member_panel_message_id
               FROM guild_config WHERE guild_id=?""",
            (ctx.guild.id,),
        )
        if existing and existing["member_panel_channel_id"] and existing["member_panel_message_id"]:
            channel = ctx.guild.get_channel(existing["member_panel_channel_id"])
            if channel:
                try:
                    message = await channel.fetch_message(existing["member_panel_message_id"])
                    await message.edit(view=PublicNotificationPanel())
                    await ctx.reply("通知設定パネルを更新しました。", mention_author=False)
                    return
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    log.exception("Could not update existing member panel.")
        message = await ctx.send(view=PublicNotificationPanel())
        self.db.execute(
            """UPDATE guild_config SET member_panel_channel_id=?, member_panel_message_id=?,
               updated_at=CURRENT_TIMESTAMP WHERE guild_id=?""",
            (message.channel.id, message.id, ctx.guild.id),
        )
        await ctx.reply("通知設定パネルを設置しました。", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTubeMemberPreferences(bot))
