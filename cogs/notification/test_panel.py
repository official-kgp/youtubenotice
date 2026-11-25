from __future__ import annotations

import discord
from discord.ext import commands

from cogs.notification.core import notification_layout

TEST_TYPES = {
    "live": ("LIVE", "live", "live", "🔴 テスト配信が開始されました。"),
    "premiere": ("プレミア公開", "upcoming", "premiere", "📅 プレミア公開の予定枠です。"),
    "horizontal": ("横動画", "video", "horizontal", "🎬 横動画の公開通知です。"),
    "short": ("Shorts", "video", "short", "📱 Shortsの公開通知です。"),
}


class TestForumSelect(discord.ui.ChannelSelect):
    def __init__(self, cog):
        super().__init__(
            channel_types=[discord.ChannelType.forum],
            placeholder="テスト投稿先フォーラムを選択",
            min_values=1,
            max_values=1,
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        forum = self.values[0]
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message("フォーラムを選択してください。", ephemeral=True)
            return
        self.cog.db.execute(
            """INSERT INTO admin_test_config(guild_id,forum_id) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET forum_id=excluded.forum_id""",
            (interaction.guild.id, forum.id),
        )
        await interaction.response.edit_message(
            content=f"テスト先を {forum.mention} に保存しました。\nテストする通知タイプを選択してください。",
            view=TestTypeView(self.cog, forum.id),
        )


class TestTypeSelect(discord.ui.Select):
    def __init__(self, cog, forum_id: int):
        self.cog = cog
        self.forum_id = forum_id
        options = [
            discord.SelectOption(label=label, value=key, description=f"{label}通知のサンプルを投稿")
            for key, (label, *_rest) in TEST_TYPES.items()
        ]
        super().__init__(
            placeholder="テストする通知タイプを選択",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        forum = interaction.guild.get_channel(self.forum_id)
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.edit_message(
                content="保存されたテスト先が見つかりません。コマンドを再実行してください。",
                view=None,
            )
            return
        label, status, content_type, description = TEST_TYPES[key]
        item = {
            "video_id": "TEST0000001",
            "channel_id": "UC0000000000000000000000",
            "title": f"YouTubeNotice {label} 通知テスト",
            "channel_title": "YouTubeNotice Test Channel",
            "status": status,
            "content_type": content_type,
            "published_at": "2026-01-01T12:00:00Z",
            "actual_start_time": "2026-01-01T12:00:00Z" if status == "live" else None,
            "scheduled_start_time": "2026-01-02T12:00:00Z" if status == "upcoming" else None,
            "category_id": "20",
            "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        }
        try:
            await forum.create_thread(
                name=f"[TEST] {label} 通知",
                view=notification_layout(item),
                reason=f"YouTubeNotice owner-only {label} UI test",
            )
            await interaction.response.edit_message(
                content=f"✅ **{label}** のテスト通知を {forum.mention} に投稿しました。\n"
                        "YouTube API、通常の通知処理、通知履歴は使用していません。",
                view=None,
            )
        except discord.Forbidden:
            await interaction.response.edit_message(
                content="❌ 投稿できませんでした。フォーラムの閲覧・投稿・スレッド作成権限を確認してください。",
                view=None,
            )
        except discord.HTTPException:
            await interaction.response.edit_message(
                content="❌ Discordへの投稿に失敗しました。",
                view=None,
            )


class TestForumView(discord.ui.LayoutView):
    def __init__(self, cog):
        super().__init__(timeout=180)
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("### テスト投稿先の初期設定"))
        card.add_item(discord.ui.TextDisplay(
            "初回のみ、テスト通知を投稿するフォーラムを選択してください。\n"
            "選択したフォーラムIDはサーバーごとに保存され、次回以降は通知タイプの選択だけになります。"
        ))
        card.add_item(discord.ui.ActionRow(TestForumSelect(cog)))
        self.add_item(card)


class TestTypeView(discord.ui.LayoutView):
    def __init__(self, cog, forum_id: int):
        super().__init__(timeout=180)
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("### 通知タイプのテスト"))
        card.add_item(discord.ui.TextDisplay(
            "投稿するサンプル通知を選択してください。実際のYouTube APIや通知履歴には触れません。"
        ))
        card.add_item(discord.ui.ActionRow(TestTypeSelect(cog, forum_id)))
        self.add_item(card)


class YouTubeOwnerTest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        db = getattr(self.bot, "youtube_db", None)
        if db is None:
            raise RuntimeError("Notification database is not ready.")
        return db

    @commands.command(name="youtubetest")
    @commands.guild_only()
    async def youtubetest(self, ctx: commands.Context):
        if not await self.bot.is_owner(ctx.author):
            await ctx.reply("このコマンドはBot Owner専用です。", mention_author=False)
            return
        config = self.db.fetchone(
            "SELECT forum_id FROM admin_test_config WHERE guild_id=?",
            (ctx.guild.id,),
        )
        if config:
            forum = ctx.guild.get_channel(config["forum_id"])
            if isinstance(forum, discord.ForumChannel):
                await ctx.send(
                    f"テスト先：{forum.mention}\nテストする通知タイプを選択してください。",
                    view=TestTypeView(self, forum.id),
                )
                return
        await ctx.send(view=TestForumView(self))


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTubeOwnerTest(bot))
