"""
cogs/reactions.py — Keyword → emoji auto-reaction commands.
The bot reacts to messages containing registered keywords.
"""
import re
import discord
from discord.ext import commands
from db import db_get_all_reactions, db_add_reaction, db_remove_reaction
from permissions import bot_permission_check


# ─────────────────────────────────────────────
#  Help definition (consumed by HelpCog)
# ─────────────────────────────────────────────
HELP_PAGE = {
    "title": "🤖 Bot Commands",
    "subtitle": "📖 Keyword Reactions",
    "fields": [
        ("_addreaction emoji keyword", "🔑 React with emoji when keyword is seen."),
        ("_removereaction keyword",    "🔑 Remove a reaction rule."),
        ("_listreactions",             "🔑 List all reaction rules."),
    ],
}


class ReactionsCog(commands.Cog, name="Reactions"):
    """Auto-react to messages that contain registered keywords."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Called from bot's on_message ──────────
    async def handle_message(self, message: discord.Message):
        rows = await db_get_all_reactions()
        content_lower = message.content.lower()
        for row in rows:
            pattern = r'\b' + re.escape(row["keyword"]) + r'\b'
            if re.search(pattern, content_lower):
                try:
                    await message.add_reaction(row["emoji"])
                except discord.HTTPException:
                    print(f"⚠️  Could not react with {row['emoji']} — invalid emoji?")

    # ── Commands ──────────────────────────────

    @commands.command(name="addreaction")
    @bot_permission_check()
    async def add_reaction_cmd(self, ctx, emoji: str, *, keyword: str):
        """React with an emoji whenever a keyword appears in a message.
        Usage: _addreaction 👍 good job
        """
        await db_add_reaction(keyword, emoji)
        embed = discord.Embed(title="✅  Reaction Rule Added", color=discord.Color.green())
        embed.add_field(name="Keyword", value=f"`{keyword.lower()}`", inline=True)
        embed.add_field(name="Emoji", value=emoji, inline=True)
        embed.set_footer(text="Bot will react whenever this keyword appears in any message.")
        await ctx.send(embed=embed)

    @commands.command(name="removereaction")
    @bot_permission_check()
    async def remove_reaction_cmd(self, ctx, *, keyword: str):
        """Remove a keyword reaction rule.
        Usage: _removereaction good job
        """
        result = await db_remove_reaction(keyword)
        if result == "DELETE 0":
            await ctx.send(f"⚠️  No reaction rule found for keyword `{keyword}`.")
            return
        await ctx.send(f"🗑️  Reaction rule for `{keyword}` removed.")

    @commands.command(name="listreactions")
    @bot_permission_check()
    async def list_reactions_cmd(self, ctx):
        """List all keyword→emoji reaction rules."""
        rows = await db_get_all_reactions()
        if not rows:
            await ctx.send("📭  No reaction rules set yet. Use `_addreaction` to add one.")
            return
        embed = discord.Embed(title="📋  Reaction Rules", color=discord.Color.blurple())
        for row in rows:
            embed.add_field(name=f"`{row['keyword']}`", value=row["emoji"], inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionsCog(bot))
