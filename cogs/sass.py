"""
cogs/sass.py — Channel-wise sass rules with regex, custom probability, and custom reply.
"""

import re
import random
import discord
from discord import app_commands
from discord.ext import commands
from db import get_db
from permissions import has_bot_permission


HELP_PAGE = {
    "title": "🤖 Bot Commands",
    "subtitle": "📖 Sass  (slash commands)",
    "fields": [
        ("/sass enable",  "🔑 Enable sass in this channel with default settings."),
        ("/sass disable", "🔑 Disable sass in this channel."),
        ("/sass edit",    "🔑 Edit pattern / probability / reply for this channel."),
        ("/sass info",    "🌐 Show sass settings for this channel."),
        ("/sass list",    "🌐 List all channels with sass enabled."),
    ],
}

DEFAULT_PATTERN     = r"^(what|when|how|where)\b|\?$"
DEFAULT_PROBABILITY = 0.02
DEFAULT_REPLY       = "apni maa se puch"


# ─────────────────────────────────────────────
#  DB helpers
# ─────────────────────────────────────────────
async def setup_sass_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sass_rules (
            channel_id  BIGINT  PRIMARY KEY,
            pattern     TEXT    NOT NULL,
            probability REAL    NOT NULL,
            reply       TEXT    NOT NULL
        )
    """)
    await conn.close()


async def db_get_sass_rule(channel_id: int):
    conn = await get_db()
    row = await conn.fetchrow("SELECT * FROM sass_rules WHERE channel_id = $1", channel_id)
    await conn.close()
    return row


async def db_get_all_sass_rules():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM sass_rules")
    await conn.close()
    return rows


async def db_save_sass_rule(channel_id: int, pattern: str, probability: float, reply: str):
    conn = await get_db()
    await conn.execute("""
        INSERT INTO sass_rules (channel_id, pattern, probability, reply)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (channel_id) DO UPDATE
            SET pattern = $2, probability = $3, reply = $4
    """, channel_id, pattern, probability, reply)
    await conn.close()


async def db_delete_sass_rule(channel_id: int):
    conn = await get_db()
    result = await conn.execute("DELETE FROM sass_rules WHERE channel_id = $1", channel_id)
    await conn.close()
    return result


# ─────────────────────────────────────────────
#  Slash command group (defined at module level)
# ─────────────────────────────────────────────
sass = app_commands.Group(name="sass", description="Manage sass rules for this channel.")


# ─────────────────────────────────────────────
#  Cog
# ─────────────────────────────────────────────
class SassCog(commands.Cog, name="Sass"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cache: dict[int, tuple[re.Pattern, float, str]] = {}

    async def cog_load(self):
        await setup_sass_db()
        await self._reload_cache()

    async def _reload_cache(self):
        rows = await db_get_all_sass_rules()
        self._cache.clear()
        for row in rows:
            try:
                compiled = re.compile(row["pattern"], re.IGNORECASE)
                self._cache[row["channel_id"]] = (compiled, row["probability"], row["reply"])
            except re.error:
                print(f"⚠️  Invalid sass regex for channel {row['channel_id']}, skipping.")

    async def handle_message(self, message: discord.Message):
        rule = self._cache.get(message.channel.id)
        if not rule:
            return
        pattern, probability, reply = rule
        if pattern.search(message.content) and random.random() < probability:
            await message.reply(reply)


# ─────────────────────────────────────────────
#  Slash commands (outside cog, on module-level group)
# ─────────────────────────────────────────────
async def _check_perm(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    if await has_bot_permission(interaction.user):
        return True
    await interaction.response.send_message(
        "🚫  You don't have permission to use this command.", ephemeral=True
    )
    return False


@sass.command(name="enable", description="Enable sass in this channel.")
@app_commands.describe(
    pattern="Regex pattern (default: questions starting with what/when/how/where or ending with ?)",
    probability="Trigger probability as a percentage e.g. 2 for 2% (default: 2)",
    reply="Reply text (default: apni maa se puch)",
)
async def sass_enable(
    interaction: discord.Interaction,
    pattern: str = DEFAULT_PATTERN,
    probability: float = 2.0,
    reply: str = DEFAULT_REPLY,
):
    if not await _check_perm(interaction):
        return
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        await interaction.response.send_message(f"❌ Invalid regex: `{e}`", ephemeral=True)
        return
    if not (0 <= probability <= 100):
        await interaction.response.send_message("❌ Probability must be 0–100.", ephemeral=True)
        return

    prob_float = probability / 100
    await db_save_sass_rule(interaction.channel_id, pattern, prob_float, reply)

    # Update cache on the cog
    cog = interaction.client.get_cog("Sass")
    if cog:
        cog._cache[interaction.channel_id] = (compiled, prob_float, reply)

    embed = discord.Embed(title="✅ Sass Enabled", color=discord.Color.green())
    embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
    embed.add_field(name="Probability", value=f"{probability}%", inline=True)
    embed.add_field(name="Pattern", value=f"`{pattern}`", inline=False)
    embed.add_field(name="Reply", value=reply, inline=False)
    await interaction.response.send_message(embed=embed)


@sass.command(name="disable", description="Disable sass in this channel.")
async def sass_disable(interaction: discord.Interaction):
    if not await _check_perm(interaction):
        return
    result = await db_delete_sass_rule(interaction.channel_id)
    cog = interaction.client.get_cog("Sass")
    if cog:
        cog._cache.pop(interaction.channel_id, None)
    if result == "DELETE 0":
        await interaction.response.send_message("⚠️ Sass is not enabled here.", ephemeral=True)
        return
    await interaction.response.send_message(f"🗑️ Sass disabled in {interaction.channel.mention}.")


@sass.command(name="edit", description="Edit pattern, probability, or reply for this channel.")
@app_commands.describe(
    pattern="New regex pattern (leave blank to keep current)",
    probability="New probability 0–100 as percentage (leave blank to keep current)",
    reply="New reply text (leave blank to keep current)",
)
async def sass_edit(
    interaction: discord.Interaction,
    pattern: str | None = None,
    probability: float | None = None,
    reply: str | None = None,
):
    if not await _check_perm(interaction):
        return
    existing = await db_get_sass_rule(interaction.channel_id)
    if not existing:
        await interaction.response.send_message("⚠️ Sass not enabled here. Use `/sass enable` first.", ephemeral=True)
        return

    new_pattern = pattern if pattern is not None else existing["pattern"]
    new_prob    = (probability / 100) if probability is not None else existing["probability"]
    new_reply   = reply if reply is not None else existing["reply"]

    try:
        compiled = re.compile(new_pattern, re.IGNORECASE)
    except re.error as e:
        await interaction.response.send_message(f"❌ Invalid regex: `{e}`", ephemeral=True)
        return

    await db_save_sass_rule(interaction.channel_id, new_pattern, new_prob, new_reply)
    cog = interaction.client.get_cog("Sass")
    if cog:
        cog._cache[interaction.channel_id] = (compiled, new_prob, new_reply)

    embed = discord.Embed(title="✏️ Sass Updated", color=discord.Color.blue())
    embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
    embed.add_field(name="Probability", value=f"{new_prob * 100:.1f}%", inline=True)
    embed.add_field(name="Pattern", value=f"`{new_pattern}`", inline=False)
    embed.add_field(name="Reply", value=new_reply, inline=False)
    await interaction.response.send_message(embed=embed)


@sass.command(name="info", description="Show sass settings for this channel.")
async def sass_info(interaction: discord.Interaction):
    existing = await db_get_sass_rule(interaction.channel_id)
    if not existing:
        await interaction.response.send_message("📭 Sass is not enabled here.", ephemeral=True)
        return
    embed = discord.Embed(title="ℹ️ Sass Settings", color=discord.Color.gold())
    embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
    embed.add_field(name="Probability", value=f"{existing['probability'] * 100:.1f}%", inline=True)
    embed.add_field(name="Pattern", value=f"`{existing['pattern']}`", inline=False)
    embed.add_field(name="Reply", value=existing["reply"], inline=False)
    await interaction.response.send_message(embed=embed)


@sass.command(name="list", description="List all channels with sass enabled.")
async def sass_list(interaction: discord.Interaction):
    rows = await db_get_all_sass_rules()
    if not rows:
        await interaction.response.send_message("📭 No sass rules yet. Use `/sass enable`.", ephemeral=True)
        return
    embed = discord.Embed(title="📋 Sass Rules", color=discord.Color.blurple())
    for row in rows:
        channel = interaction.client.get_channel(row["channel_id"])
        cname = channel.mention if channel else f"Unknown ({row['channel_id']})"
        embed.add_field(
            name=cname,
            value=(
                f"**Pattern:** `{row['pattern']}`\n"
                f"**Probability:** {row['probability'] * 100:.1f}%\n"
                f"**Reply:** {row['reply']}"
            ),
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SassCog(bot))
    bot.tree.add_command(sass)
