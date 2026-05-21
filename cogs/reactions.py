"""
cogs/reactions.py — Keyword → emoji auto-reaction with in-memory cache.
"""
import re
import discord
from discord.ext import commands
from db import db_get_all_reactions, db_add_reaction, db_remove_reaction
from permissions import bot_permission_check

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

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache: [ { keyword, emoji, compiled_pattern } ]
        self._cache: list[dict] = []

    async def cog_load(self):
        await self._reload_cache()

    async def _reload_cache(self):
        rows = await db_get_all_reactions()
        self._cache = []
        for row in rows:
            try:
                pattern = re.compile(r'\b' + re.escape(row["keyword"]) + r'\b', re.IGNORECASE)
                self._cache.append({
                    "keyword": row["keyword"],
                    "emoji": row["emoji"],
                    "pattern": pattern,
                })
            except re.error:
                pass

    async def handle_message(self, message: discord.Message):
        content_lower = message.content.lower()
        for rule in self._cache:
            if rule["pattern"].search(content_lower):
                try:
                    await message.add_reaction(rule["emoji"])
                except discord.HTTPException:
                    print(f"⚠️  Could not react with {rule['emoji']} — invalid emoji?")

    @commands.command(name="addreaction")
    @bot_permission_check()
    async def add_reaction_cmd(self, ctx, emoji: str, *, keyword: str):
        await db_add_reaction(keyword, emoji)
        await self._reload_cache()
        embed = discord.Embed(title="✅  Reaction Rule Added", color=discord.Color.green())
        embed.add_field(name="Keyword", value=f"`{keyword.lower()}`", inline=True)
        embed.add_field(name="Emoji", value=emoji, inline=True)
        embed.set_footer(text="Bot will react whenever this exact word appears in a message.")
        await ctx.send(embed=embed)

    @commands.command(name="removereaction")
    @bot_permission_check()
    async def remove_reaction_cmd(self, ctx, *, keyword: str):
        result = await db_remove_reaction(keyword)
        if result == "DELETE 0":
            await ctx.send(f"⚠️  No reaction rule found for keyword `{keyword}`.")
            return
        await self._reload_cache()
        await ctx.send(f"🗑️  Reaction rule for `{keyword}` removed.")

    @commands.command(name="listreactions")
    @bot_permission_check()
    async def list_reactions_cmd(self, ctx):
        if not self._cache:
            await ctx.send("📭  No reaction rules set yet. Use `_addreaction` to add one.")
            return
        embed = discord.Embed(title="📋  Reaction Rules", color=discord.Color.blurple())
        for rule in self._cache:
            embed.add_field(name=f"`{rule['keyword']}`", value=rule["emoji"], inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionsCog(bot))
