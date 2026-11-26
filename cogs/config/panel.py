from __future__ import annotations

import re
import logging

import discord
from discord.ext import commands

CHANNEL_RE = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")


def snowflake(value: str) -> int | None:
    match = re.search(r"\d{15,22}", value or "")
    return int(match.group(0)) if match else None


class TagsModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="動画種別ごとのフォーラムタグ", timeout=300)
        self.cog = cog
        self.short = discord.ui.TextInput(label="Shortsタグ（名前またはID）", required=False, max_length=100)
        self.horizontal = discord.ui.TextInput(label="横動画タグ（名前またはID）", required=False, max_length=100)
        self.live = discord.ui.TextInput(label="ライブ配信タグ（名前またはID）", required=False, max_length=100)
        self.premiere = discord.ui.TextInput(label="プレミア／配信予定タグ（名前またはID）", required=False, max_length=100)
        for item in (self.short, self.horizontal, self.live, self.premiere):
            self.add_item(item)

    @staticmethod
    def resolve(forum: discord.ForumChannel, raw: str):
        value = str(raw).strip()
        if not value:
            return None
        tag = next((t for t in forum.available_tags if t.name == value), None)
        if tag:
            return tag.id
        try:
            tag_id = int(value)
        except ValueError:
            return False
        return tag_id if any(t.id == tag_id for t in forum.available_tags) else False

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        config = self.cog.db.fetchone("SELECT forum_id FROM guild_config WHERE guild_id=?", (interaction.guild.id,))
        forum = interaction.guild.get_channel(config["forum_id"]) if config else None
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message("先に通知先フォーラムを設定してください。", ephemeral=True)
            return
        values = [self.resolve(forum, x.value) for x in (self.short, self.horizontal, self.live, self.premiere)]
        if any(x is False for x in values):
            await interaction.response.send_message("フォーラムに存在するタグ名またはIDを入力してください。", ephemeral=True)
            return
        self.cog.db.execute(
            """UPDATE guild_config SET short_tag_id=?, horizontal_tag_id=?,
               live_tag_id=?, premiere_tag_id=?, updated_at=CURRENT_TIMESTAMP
               WHERE guild_id=?""",
            (*values, interaction.guild.id),
        )
        await interaction.response.send_message("動画種別ごとのフォーラムタグを更新しました。", ephemeral=True)


class MentionRoleModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="通知メンションロールを設定", timeout=300)
        self.cog = cog
        self.value = discord.ui.TextInput(
            label="ロールID・ロールメンション（空欄で解除）",
            placeholder="ロールID またはロールメンション",
            required=False,
            max_length=40,
        )
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        if not self.cog.db.fetchone("SELECT 1 FROM guild_config WHERE guild_id=?", (interaction.guild.id,)):
            await interaction.response.send_message("先に通知先フォーラムを設定してください。", ephemeral=True)
            return
        raw = str(self.value.value).strip()
        role_id = snowflake(raw) if raw else None
        role = interaction.guild.get_role(role_id) if role_id else None
        if raw and role is None:
            await interaction.response.send_message("このサーバーに存在するロールIDまたはロールメンションを指定してください。", ephemeral=True)
            return
        self.cog.db.execute(
            """UPDATE guild_config SET mention_role_id=?, updated_at=CURRENT_TIMESTAMP
               WHERE guild_id=?""",
            (role.id if role else None, interaction.guild.id),
        )
        message = f"通知メンションを {role.mention} に設定しました。" if role else "通知メンションを解除しました。"
        await interaction.response.send_message(message, ephemeral=True)


class NotificationRolesModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="通知カテゴリロール設定", timeout=300)
        self.cog = cog
        self.dm = discord.ui.TextInput(label="DM通知ロールID（空欄で解除）", required=False, max_length=40)
        self.live = discord.ui.TextInput(label="LIVE・プレミア公開ロールID", required=False, max_length=40)
        self.video = discord.ui.TextInput(label="横動画・ShortsロールID", required=False, max_length=40)
        self.all = discord.ui.TextInput(label="すべての通知ロールID", required=False, max_length=40)
        for item in (self.dm, self.live, self.video, self.all):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        if not self.cog.db.fetchone(
            "SELECT 1 FROM guild_config WHERE guild_id=?", (interaction.guild.id,)
        ):
            await interaction.response.send_message("先に通知先フォーラムを設定してください。", ephemeral=True)
            return
        values = []
        for field in (self.dm, self.live, self.video, self.all):
            raw = str(field.value).strip()
            role_id = snowflake(raw) if raw else None
            role = interaction.guild.get_role(role_id) if role_id else None
            if raw and role is None:
                await interaction.response.send_message(
                    f"指定されたロールが見つかりません：{raw}", ephemeral=True
                )
                return
            values.append(role.id if role else None)
        self.cog.db.execute(
            """UPDATE guild_config SET dm_notify_role_id=?, live_notify_role_id=?,
               video_notify_role_id=?, all_notify_role_id=?, updated_at=CURRENT_TIMESTAMP
               WHERE guild_id=?""",
            (*values, interaction.guild.id),
        )
        await interaction.response.send_message("通知カテゴリロールを更新しました。", ephemeral=True)


class InputActionButton(discord.ui.Button):
    def __init__(self, cog, action: str):
        super().__init__(
            label="IDを入力する",
            style=discord.ButtonStyle.primary,
            custom_id=f"yn:panel:input:{action}",
        )
        self.cog = cog
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        await self.cog.prompt_channel_id(interaction, self.action)


class InputActionBackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="閉じる", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            view=discord.ui.LayoutView(timeout=60)
        )


class InputActionView(discord.ui.LayoutView):
    def __init__(self, cog, action: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.action = action
        instructions = {
            "forum": "通知先にするDiscordフォーラムのチャンネルIDまたはメンションを入力します。",
            "add": "監視対象に追加するYouTubeチャンネルのID・@ハンドル・チャンネルURLを入力します。",
            "remove": "監視対象から削除するYouTubeチャンネルのID・@ハンドル・チャンネルURLを入力します。",
            "test": "最新動画を取得してテスト通知するYouTubeチャンネルのID・@ハンドル・チャンネルURLを入力します。",
            "errorlog": "エラーログ送信先のテキストチャンネルIDまたはメンションを入力します。解除する場合は「解除」と入力します。",
        }
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("## 入力モード"))
        card.add_item(discord.ui.TextDisplay(
            instructions.get(action, "入力する値を確認してください。")
            + "\n「IDを入力する」を押すと、120秒間の入力待ち状態になります。"
        ))
        card.add_item(discord.ui.ActionRow(InputActionButton(cog, action), InputActionBackButton()))
        self.add_item(card)


class PanelActionSelect(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        options = [
            discord.SelectOption(label="通知先フォーラムを設定", value="forum", emoji="📌"),
            discord.SelectOption(label="YouTubeチャンネルを追加", value="add", emoji="➕"),
            discord.SelectOption(label="YouTubeチャンネルを削除", value="remove", emoji="➖"),
            discord.SelectOption(label="最新動画でテスト通知", value="test", emoji="🧪"),
            discord.SelectOption(label="動画種別タグを設定", value="tags", emoji="🏷️"),
            discord.SelectOption(label="通知メンションロールを設定", value="role", emoji="🔔"),
            discord.SelectOption(label="通知カテゴリロールを設定", value="notifyroles", emoji="👥"),
            discord.SelectOption(label="メンバー向け通知パネルを送信・更新", value="memberpanel", emoji="📨"),
            discord.SelectOption(label="エラーログ送信先を設定", value="errorlog", emoji="🧾"),
            discord.SelectOption(label="現在の設定を確認", value="status", emoji="📋"),
        ]
        super().__init__(
            placeholder="実行する操作を選択してください",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="yn:panel:operation",
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("このパネルはサーバー内で使用してください。", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return

        action = self.values[0]
        if action in {"forum", "add", "remove", "test", "errorlog"}:
            await interaction.response.send_message(
                view=InputActionView(self.cog, action),
                ephemeral=True,
            )
        elif action == "tags":
            await interaction.response.send_modal(TagsModal(self.cog))
        elif action == "role":
            await interaction.response.send_modal(MentionRoleModal(self.cog))
        elif action == "notifyroles":
            await interaction.response.send_modal(NotificationRolesModal(self.cog))
        elif action == "memberpanel":
            member_cog = self.cog.bot.get_cog("YouTubeMemberPreferences")
            if member_cog is None:
                await interaction.response.send_message(
                    "メンバー通知機能が読み込まれていません。", ephemeral=True
                )
                return
            ok, result = await member_cog.publish_panel(interaction.guild, interaction.channel)
            await interaction.response.send_message(
                ("✅ " if ok else "❌ ") + result,
                ephemeral=True,
            )
        elif action == "status":
            await self.cog.show_status(interaction)


class YouTubeConfigPanel(discord.ui.LayoutView):
    def __init__(self, cog, guild_id: int | None = None):
        super().__init__(timeout=None)
        self.cog = cog
        if guild_id is None:
            summary = "サーバーの保存済み設定を読み込みます。"
        else:
            cfg = cog.db.fetchone(
                """SELECT forum_id, mention_role_id, error_log_channel_id
                   FROM guild_config WHERE guild_id=?""",
                (guild_id,),
            )
            channels = cog.db.fetchall(
                "SELECT channel_id FROM tracked_channels WHERE guild_id=?",
                (guild_id,),
            )
            forum = f"<#{cfg['forum_id']}>" if cfg else "未設定"
            role = f"<@&{cfg['mention_role_id']}>" if cfg and cfg["mention_role_id"] else "未設定"
            error_channel = (
                f"<#{cfg['error_log_channel_id']}>"
                if cfg and cfg["error_log_channel_id"] else "未設定"
            )
            summary = (
                f"**通知先フォーラム：** {forum}\n"
                f"**監視チャンネル：** {len(channels)}件\n"
                f"**メンションロール：** {role}\n"
                f"**エラーログ送信先：** {error_channel}"
            )

        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("# YouTubeNotice | Config"))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.TextDisplay(
            f"{summary}\n\n下のメニューから操作を選択してください。"
            "入力が必要な操作は、次の画面で「IDを入力する」を押すと120秒間の入力待ちになります。"
            "\n通知カテゴリロールはメンバーの選択状況を示すロールです。チャンネル別の通知はDMで配信します。"
        ))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.ActionRow(PanelActionSelect(cog)))
        card.add_item(discord.ui.TextDisplay("-# YouTubeNotice Config System · YN!youtubectrl"))
        self.add_item(card)


class YouTubeConfig(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._input_sessions: set[tuple[int, int, int]] = set()

    async def cog_load(self):
        self.bot.add_view(YouTubeConfigPanel(self))

    @property
    def db(self):
        db = getattr(self.bot, "youtube_db", None)
        if db is None:
            raise RuntimeError("Notification database is not ready.")
        return db

    async def prompt_channel_id(self, interaction: discord.Interaction, action: str):
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or channel is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        session_key = (guild.id, interaction.user.id, channel.id)
        if session_key in self._input_sessions:
            await interaction.response.send_message("このチャンネルでは既に入力待ちです。", ephemeral=True)
            return

        if action in {"add", "remove", "test"} and not self.db.fetchone(
            "SELECT 1 FROM guild_config WHERE guild_id=?", (guild.id,)
        ):
            await interaction.response.send_message("先に通知先フォーラムを設定してください。", ephemeral=True)
            return

        core = self.bot.get_cog("YouTubeNotification")
        if action in {"add", "remove", "test"} and core is None:
            await interaction.response.send_message("通知コアが起動していません。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        self._input_sessions.add(session_key)
        prompt = None
        try:
            prompt_texts = {
                "forum": "通知先フォーラムのチャンネルIDまたはメンションを送信してください。",
                "add": "追加するYouTubeチャンネルのID・@ハンドル・チャンネルURLを送信してください。",
                "remove": "削除するYouTubeチャンネルのID・@ハンドル・チャンネルURLを送信してください。",
                "test": "テスト通知するYouTubeチャンネルのID・@ハンドル・チャンネルURLを送信してください。",
                "errorlog": "エラーログ送信先のテキストチャンネルIDまたはメンションを送信してください。解除する場合は「解除」と入力してください。",
            }
            prompt = await channel.send(
                prompt_texts[action] + "\n入力待ち時間は120秒です。入力内容は処理後に削除します。"
            )
            check = lambda message: (
                message.author.id == interaction.user.id
                and message.channel.id == channel.id
                and message.guild is not None
                and message.guild.id == guild.id
            )
            resolved = None
            error_message = "入力内容を確認できませんでした。"
            for attempt in range(3):
                try:
                    message = await self.bot.wait_for("message", check=check, timeout=120)
                except TimeoutError:
                    await interaction.followup.send(
                        "入力がタイムアウトしました。もう一度パネルから操作してください。",
                        ephemeral=True,
                    )
                    return
                candidate = message.content.strip()
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass

                if action == "forum":
                    discord_channel_id = snowflake(candidate)
                    target = guild.get_channel(discord_channel_id) if discord_channel_id else None
                    if target is None and discord_channel_id:
                        try:
                            target = await guild.fetch_channel(discord_channel_id)
                        except discord.HTTPException:
                            target = None
                    if isinstance(target, discord.ForumChannel):
                        resolved = str(target.id)
                        break
                    error_message = "フォーラムチャンネルのIDまたはメンションを送信してください。"
                elif action == "errorlog":
                    if candidate.lower() in {"解除", "off", "none", "clear"}:
                        self.db.execute(
                            "UPDATE guild_config SET error_log_channel_id=NULL, updated_at=CURRENT_TIMESTAMP WHERE guild_id=?",
                            (guild.id,),
                        )
                        await interaction.followup.send("エラーログ送信先を解除しました。", ephemeral=True)
                        return
                    text_channel_id = snowflake(candidate)
                    target = guild.get_channel(text_channel_id) if text_channel_id else None
                    if target is None and text_channel_id:
                        try:
                            target = await guild.fetch_channel(text_channel_id)
                        except discord.HTTPException:
                            target = None
                    if isinstance(target, discord.TextChannel):
                        self.db.execute(
                            "UPDATE guild_config SET error_log_channel_id=?, updated_at=CURRENT_TIMESTAMP WHERE guild_id=?",
                            (target.id, guild.id),
                        )
                        await interaction.followup.send(
                            f"エラーログ送信先を {target.mention} に設定しました。",
                            ephemeral=True,
                        )
                        return
                    error_message = "このサーバーのテキストチャンネルIDまたはメンションを送信してください。"
                else:
                    resolved = await core.resolve_channel_id(candidate)
                    if resolved:
                        break
                    error_message = (
                        "チャンネルを特定できませんでした。UCから始まるID、@ハンドル、"
                        "またはYouTubeチャンネルURLを確認してください。"
                    )
                if attempt < 2:
                    await interaction.followup.send(error_message, ephemeral=True)

            if resolved is None:
                await interaction.followup.send(
                    "入力内容を確認できなかったため、操作を中止しました。",
                    ephemeral=True,
                )
                return

            if action == "forum":
                self.db.execute(
                    """INSERT INTO guild_config(guild_id, forum_id) VALUES(?,?)
                       ON CONFLICT(guild_id) DO UPDATE SET forum_id=excluded.forum_id,
                       updated_at=CURRENT_TIMESTAMP""",
                    (guild.id, int(resolved)),
                )
                ok, result = True, f"通知先フォーラムを <#{resolved}> に設定しました。"
            elif action == "add":
                ok, result = await core.add_channel(guild.id, resolved)
            elif action == "remove":
                tracked = self.db.fetchone(
                    "SELECT 1 FROM tracked_channels WHERE guild_id=? AND channel_id=?",
                    (guild.id, resolved),
                )
                if not tracked:
                    ok, result = False, "このサーバーでは監視されていないチャンネルです。"
                else:
                    await core.remove_channel(guild.id, resolved)
                    ok, result = True, "監視対象から削除しました。"
            else:
                ok, result = await core.test_latest_video(guild.id, resolved)
            await interaction.followup.send(("✅ " if ok else "❌ ") + result, ephemeral=True)
        except Exception:
            logging.getLogger("youtubenotice.config").exception("Input mode failed.")
            await interaction.followup.send(
                "処理中にエラーが発生しました。Botログを確認してください。",
                ephemeral=True,
            )
        finally:
            self._input_sessions.discard(session_key)
            if prompt:
                try:
                    await prompt.delete()
                except discord.HTTPException:
                    pass

    @commands.command(name="youtubectrl", aliases=["ynconfig"])
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def youtubectrl(self, ctx):
        await ctx.send(view=YouTubeConfigPanel(self, ctx.guild.id))

    async def show_status(self, interaction: discord.Interaction):
        cfg = self.db.fetchone(
            """SELECT forum_id, short_tag_id, horizontal_tag_id, live_tag_id,
                      premiere_tag_id, mention_role_id, error_log_channel_id,
                      member_panel_channel_id, member_panel_message_id
               FROM guild_config WHERE guild_id=?""",
            (interaction.guild.id,),
        )
        rows = self.db.fetchall(
            "SELECT channel_id, channel_title FROM tracked_channels WHERE guild_id=? ORDER BY channel_id",
            (interaction.guild.id,),
        )
        forum = f"<#{cfg['forum_id']}>" if cfg else "未設定"
        role = f"<@&{cfg['mention_role_id']}>" if cfg and cfg["mention_role_id"] else "未設定"
        error_channel = (
            f"<#{cfg['error_log_channel_id']}>"
            if cfg and cfg["error_log_channel_id"] else "未設定"
        )
        panel = (
            f"<#{cfg['member_panel_channel_id']}>"
            if cfg and cfg["member_panel_channel_id"] and cfg["member_panel_message_id"]
            else "未設置"
        )
        lines = [
            f"**通知先：** {forum}",
            f"**監視チャンネル：** {len(rows)}件",
            f"**メンションロール：** {role}",
            f"**エラーログ送信先：** {error_channel}",
            f"**メンバー向け通知パネル：** {panel}",
        ]
        if cfg:
            role_rows = self.db.fetchone(
                """SELECT dm_notify_role_id, live_notify_role_id, video_notify_role_id,
                          all_notify_role_id FROM guild_config WHERE guild_id=?""",
                (interaction.guild.id,),
            )
            lines.append(
                "**通知カテゴリロール：** "
                + " / ".join(
                    f"{label} <@&{role_rows[column]}>" if role_rows[column] else f"{label} 未設定"
                    for column, label in (
                        ("dm_notify_role_id", "DM"),
                        ("live_notify_role_id", "LIVE"),
                        ("video_notify_role_id", "動画"),
                        ("all_notify_role_id", "全通知"),
                    )
                )
            )
        lines.extend(
            f"• {r['channel_title'] or r['channel_id']} ({r['channel_id']})"
            for r in rows
        )
        if cfg:
            lines.append(
                f"**タグID：** Shorts {cfg['short_tag_id'] or '未設定'} / "
                f"横動画 {cfg['horizontal_tag_id'] or '未設定'} / "
                f"ライブ {cfg['live_tag_id'] or '未設定'} / "
                f"プレミア {cfg['premiere_tag_id'] or '未設定'}"
            )
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTubeConfig(bot))
