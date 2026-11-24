from __future__ import annotations

import re

import discord
from discord.ext import commands

CHANNEL_RE = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")


def snowflake(value: str) -> int | None:
    match = re.search(r"\d{15,22}", value or "")
    return int(match.group(0)) if match else None


class ConfigModal(discord.ui.Modal):
    def __init__(self, cog, title: str):
        super().__init__(title=title, timeout=300)
        self.cog = cog

    async def allowed(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return False
        return True


class ForumModal(ConfigModal):
    def __init__(self, cog):
        super().__init__(cog, "通知先フォーラムを設定")
        self.value = discord.ui.TextInput(label="フォーラムチャンネルID", placeholder="ID または <#ID>", max_length=30)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        if not await self.allowed(interaction):
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


class AddChannelModal(ConfigModal):
    def __init__(self, cog):
        super().__init__(cog, "YouTubeチャンネルを追加")
        self.value = discord.ui.TextInput(label="YouTubeチャンネルID", placeholder="UCから始まる24文字", max_length=24)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        if not await self.allowed(interaction):
            return
        cid = str(self.value.value).strip()
        if not CHANNEL_RE.fullmatch(cid):
            await interaction.response.send_message("チャンネルIDの形式が正しくありません。", ephemeral=True)
            return
        if not self.cog.db.fetchone("SELECT 1 FROM guild_config WHERE guild_id=?", (interaction.guild.id,)):
            await interaction.response.send_message("先に通知先フォーラムを設定してください。", ephemeral=True)
            return
        core = self.cog.bot.get_cog("YouTubeNotification")
        if core is None:
            await interaction.response.send_message("通知コアが起動していません。", ephemeral=True)
            return
        ok, message = await core.add_channel(interaction.guild.id, cid)
        await interaction.response.send_message(("✅ " if ok else "❌ ") + message, ephemeral=True)


class RemoveChannelModal(ConfigModal):
    def __init__(self, cog):
        super().__init__(cog, "監視チャンネルを削除")
        self.value = discord.ui.TextInput(label="削除するチャンネルID", placeholder="UCから始まる24文字", max_length=24)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        if not await self.allowed(interaction):
            return
        cid = str(self.value.value).strip()
        if not CHANNEL_RE.fullmatch(cid):
            await interaction.response.send_message("チャンネルIDの形式が正しくありません。", ephemeral=True)
            return
        if not self.cog.db.fetchone("SELECT 1 FROM tracked_channels WHERE guild_id=? AND channel_id=?", (interaction.guild.id, cid)):
            await interaction.response.send_message("このサーバーでは監視されていません。", ephemeral=True)
            return
        core = self.cog.bot.get_cog("YouTubeNotification")
        if core is None:
            await interaction.response.send_message("通知コアが起動していません。", ephemeral=True)
            return
        await core.remove_channel(interaction.guild.id, cid)
        await interaction.response.send_message("監視対象から削除しました。", ephemeral=True)


class TagsModal(ConfigModal):
    def __init__(self, cog):
        super().__init__(cog, "フォーラムタグを設定")
        self.upcoming = discord.ui.TextInput(label="配信予定タグ（名前またはID）", required=False, max_length=100)
        self.live = discord.ui.TextInput(label="ライブ中タグ（名前またはID）", required=False, max_length=100)
        self.video = discord.ui.TextInput(label="新着動画タグ（名前またはID）", required=False, max_length=100)
        self.add_item(self.upcoming)
        self.add_item(self.live)
        self.add_item(self.video)

    @staticmethod
    def resolve(forum, raw):
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

    async def on_submit(self, interaction):
        if not await self.allowed(interaction):
            return
        config = self.cog.db.fetchone("SELECT forum_id FROM guild_config WHERE guild_id=?", (interaction.guild.id,))
        forum = interaction.guild.get_channel(config["forum_id"]) if config else None
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message("先に通知先フォーラムを設定してください。", ephemeral=True)
            return
        values = [self.resolve(forum, x.value) for x in (self.upcoming, self.live, self.video)]
        if any(x is False for x in values):
            await interaction.response.send_message("フォーラムに存在するタグ名またはIDを入力してください。", ephemeral=True)
            return
        self.cog.db.execute(
            """UPDATE guild_config SET upcoming_tag_id=?, live_tag_id=?, video_tag_id=?,
               updated_at=CURRENT_TIMESTAMP WHERE guild_id=?""",
            (*values, interaction.guild.id),
        )
        await interaction.response.send_message("フォーラムタグを更新しました。", ephemeral=True)


class TestLatestModal(ConfigModal):
    def __init__(self, cog):
        super().__init__(cog, "最新動画でテスト通知")
        self.value = discord.ui.TextInput(
            label="監視中のYouTubeチャンネルID",
            placeholder="UCから始まる24文字",
            max_length=24,
        )
        self.add_item(self.value)

    async def on_submit(self, interaction):
        if not await self.allowed(interaction):
            return
        channel_id = str(self.value.value).strip()
        if not CHANNEL_RE.fullmatch(channel_id):
            await interaction.response.send_message(
                "チャンネルIDの形式が正しくありません。", ephemeral=True
            )
            return
        core = self.cog.bot.get_cog("YouTubeNotification")
        if core is None:
            await interaction.response.send_message(
                "通知コアが起動していません。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            ok, message = await core.test_latest_video(interaction.guild.id, channel_id)
        except Exception:
            import logging
            logging.getLogger("youtubenotice.config").exception(
                "Latest-video test failed."
            )
            ok, message = False, "テスト中に予期しないエラーが発生しました。Botログを確認してください。"
        await interaction.followup.send(("✅ " if ok else "❌ ") + message, ephemeral=True)

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
        elif self.action == "add":
            await interaction.response.send_modal(AddChannelModal(self.panel.cog))
        elif self.action == "remove":
            await interaction.response.send_modal(RemoveChannelModal(self.panel.cog))
        elif self.action == "tags":
            await interaction.response.send_modal(TagsModal(self.panel.cog))
        elif self.action == "status":
            await self.panel.cog.show_status(interaction)
        elif self.action == "test":
            await interaction.response.send_modal(TestLatestModal(self.panel.cog))


class YouTubeConfigPanel(discord.ui.LayoutView):
    def __init__(self, cog, guild_id: int | None = None):
        # timeout=None and stable custom_ids make this a persistent Discord view.
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

        if guild_id is None:
            summary = "各サーバーの保存済み設定を読み込みます。"
        else:
            cfg = cog.db.fetchone("SELECT forum_id FROM guild_config WHERE guild_id=?", (guild_id,))
            channels = cog.db.fetchall("SELECT channel_id FROM tracked_channels WHERE guild_id=?", (guild_id,))
            forum = f"<#{cfg['forum_id']}>" if cfg else "未設定"
            summary = f"**通知先フォーラム：** {forum}\n**監視チャンネル：** {len(channels)}件"

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
            PanelButton(self, "タグ設定", "tags", discord.ButtonStyle.secondary),
            PanelButton(self, "設定確認", "status", discord.ButtonStyle.secondary),
            PanelButton(self, "最新動画でテスト通知", "test", discord.ButtonStyle.primary),
        ))
        card.add_item(discord.ui.TextDisplay("-# YouTubeNotice Config System · YN!youtubectrl"))
        self.add_item(card)


class YouTubeConfig(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Register one global persistent view so old panel messages keep working
        # after a process restart. Guild context is taken from each interaction.
        self.bot.add_view(YouTubeConfigPanel(self))

    @property
    def db(self):
        db = getattr(self.bot, "youtube_db", None)
        if db is None:
            raise RuntimeError("Notification database is not ready.")
        return db

    @commands.command(name="youtubectrl", aliases=["ynconfig"])
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def youtubectrl(self, ctx):
        """Open the persistent LayoutView configuration panel."""
        await ctx.send(view=YouTubeConfigPanel(self, ctx.guild.id))

    async def show_status(self, interaction):
        cfg = self.db.fetchone(
            "SELECT forum_id, upcoming_tag_id, live_tag_id, video_tag_id FROM guild_config WHERE guild_id=?",
            (interaction.guild.id,),
        )
        rows = self.db.fetchall(
            "SELECT channel_id, channel_title FROM tracked_channels WHERE guild_id=? ORDER BY channel_id",
            (interaction.guild.id,),
        )
        forum = f"<#{cfg['forum_id']}>" if cfg else "未設定"
        lines = [f"**通知先：** {forum}", f"**監視チャンネル：** {len(rows)}件"]
        lines.extend(f"• {r['channel_title'] or r['channel_id']} ({r['channel_id']})" for r in rows)
        if cfg:
            lines.append(
                f"**タグID：** 予定 {cfg['upcoming_tag_id'] or '未設定'} / "
                f"ライブ {cfg['live_tag_id'] or '未設定'} / "
                f"動画 {cfg['video_tag_id'] or '未設定'}"
            )
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTubeConfig(bot))
