from __future__ import annotations

import re
import logging

import discord
from discord.ext import commands

CHANNEL_RE = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")


def snowflake(value: str) -> int | None:
    match = re.search(r"\d{15,22}", value or "")
    return int(match.group(0)) if match else None


class ForumModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="通知先フォーラムを設定", timeout=300)
        self.cog = cog
        self.value = discord.ui.TextInput(label="フォーラムチャンネルID", placeholder="ID またはチャンネルメンション", max_length=30)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        channel_id = snowflake(str(self.value.value))
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.ForumChannel):
            await interaction.response.send_message("指定されたチャンネルはフォーラムではありません。", ephemeral=True)
            return
        self.cog.db.execute(
            """INSERT INTO guild_config(guild_id, forum_id) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET forum_id=excluded.forum_id,
               updated_at=CURRENT_TIMESTAMP""",
            (interaction.guild.id, channel.id),
        )
        await interaction.response.send_message(f"通知先を {channel.mention} に設定しました。", ephemeral=True)


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


class PanelButton(discord.ui.Button):
    def __init__(self, panel, label, action, style):
        super().__init__(label=label, style=style, custom_id=f"yn:panel:{action}")
        self.panel = panel
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("このパネルはサーバー内で使用してください。", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        if self.action == "forum":
            await interaction.response.send_modal(ForumModal(self.panel.cog))
        elif self.action in {"add", "remove", "test"}:
            await self.panel.cog.prompt_channel_id(interaction, self.action)
        elif self.action == "tags":
            await interaction.response.send_modal(TagsModal(self.panel.cog))
        elif self.action == "role":
            await interaction.response.send_modal(MentionRoleModal(self.panel.cog))
        elif self.action == "status":
            await self.panel.cog.show_status(interaction)


class YouTubeConfigPanel(discord.ui.LayoutView):
    def __init__(self, cog, guild_id: int | None = None):
        super().__init__(timeout=None)
        self.cog = cog
        if guild_id is None:
            summary = "各サーバーの保存済み設定を読み込みます。"
        else:
            cfg = cog.db.fetchone(
                "SELECT forum_id, mention_role_id FROM guild_config WHERE guild_id=?",
                (guild_id,),
            )
            channels = cog.db.fetchall("SELECT channel_id FROM tracked_channels WHERE guild_id=?", (guild_id,))
            forum = f"<#{cfg['forum_id']}>" if cfg else "未設定"
            role = f"<@&{cfg['mention_role_id']}>" if cfg and cfg["mention_role_id"] else "未設定"
            summary = f"**通知先フォーラム：** {forum}\n**監視チャンネル：** {len(channels)}件\n**メンションロール：** {role}"

        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("# YouTubeNotice | Config"))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.TextDisplay(
            f"{summary}\n\nボタンから各種設定を変更できます。設定変更にはサーバー管理権限が必要です。"
        ))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.ActionRow(
            PanelButton(self, "フォーラム設定", "forum", discord.ButtonStyle.primary),
            PanelButton(self, "チャンネル追加", "add", discord.ButtonStyle.success),
            PanelButton(self, "チャンネル削除", "remove", discord.ButtonStyle.danger),
        ))
        card.add_item(discord.ui.ActionRow(
            PanelButton(self, "動画タグ設定", "tags", discord.ButtonStyle.secondary),
            PanelButton(self, "メンション設定", "role", discord.ButtonStyle.secondary),
            PanelButton(self, "設定確認", "status", discord.ButtonStyle.secondary),
        ))
        card.add_item(discord.ui.ActionRow(
            PanelButton(self, "最新動画でテスト通知", "test", discord.ButtonStyle.primary),
        ))
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

        if action == "add" and not self.db.fetchone(
            "SELECT 1 FROM guild_config WHERE guild_id=?", (guild.id,)
        ):
            await interaction.response.send_message("先に通知先フォーラムを設定してください。", ephemeral=True)
            return

        core = self.bot.get_cog("YouTubeNotification")
        if core is None:
            await interaction.response.send_message("通知コアが起動していません。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        self._input_sessions.add(session_key)
        prompt = None
        try:
            prompt = await channel.send(
                "YouTubeチャンネルIDを送信してください。\n"
                "例：UCから始まる24文字\n"
                "入力待ち時間は120秒です。"
            )
            check = lambda message: (
                message.author.id == interaction.user.id
                and message.channel.id == channel.id
                and message.guild is not None
                and message.guild.id == guild.id
            )
            channel_id = None
            for attempt in range(3):
                try:
                    message = await self.bot.wait_for("message", check=check, timeout=120)
                except TimeoutError:
                    await interaction.followup.send("入力がタイムアウトしました。もう一度パネルから操作してください。", ephemeral=True)
                    return
                candidate = message.content.strip()
                if CHANNEL_RE.fullmatch(candidate):
                    channel_id = candidate
                    break
                if attempt < 2:
                    await interaction.followup.send(
                        "チャンネルIDの形式が正しくありません。UCから始まる24文字を送信してください。",
                        ephemeral=True,
                    )
            if channel_id is None:
                await interaction.followup.send("形式が確認できなかったため、操作を中止しました。", ephemeral=True)
                return

            if action == "add":
                ok, result = await core.add_channel(guild.id, channel_id)
            elif action == "remove":
                tracked = self.db.fetchone(
                    "SELECT 1 FROM tracked_channels WHERE guild_id=? AND channel_id=?",
                    (guild.id, channel_id),
                )
                if not tracked:
                    ok, result = False, "このサーバーでは監視されていないチャンネルです。"
                else:
                    await core.remove_channel(guild.id, channel_id)
                    ok, result = True, "監視対象から削除しました。"
            else:
                ok, result = await core.test_latest_video(guild.id, channel_id)
            await interaction.followup.send(("✅ " if ok else "❌ ") + result, ephemeral=True)
        except Exception:
            logging.getLogger("youtubenotice.config").exception("Channel ID input flow failed.")
            await interaction.followup.send("処理中にエラーが発生しました。Botログを確認してください。", ephemeral=True)
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
                      premiere_tag_id, mention_role_id
               FROM guild_config WHERE guild_id=?""",
            (interaction.guild.id,),
        )
        rows = self.db.fetchall(
            "SELECT channel_id, channel_title FROM tracked_channels WHERE guild_id=? ORDER BY channel_id",
            (interaction.guild.id,),
        )
        forum = f"<#{cfg['forum_id']}>" if cfg else "未設定"
        role = f"<@&{cfg['mention_role_id']}>" if cfg and cfg["mention_role_id"] else "未設定"
        lines = [f"**通知先：** {forum}", f"**監視チャンネル：** {len(rows)}件", f"**メンションロール：** {role}"]
        lines.extend(f"• {r['channel_title'] or r['channel_id']} ({r['channel_id']})" for r in rows)
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
