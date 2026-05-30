"""
cogs/sass.py — Category-wide sass rules with regex, custom probability, and custom reply.

Rules are stored per-category. When sass is enabled in a channel, it applies to
ALL channels in that channel's category.

Slash commands:
  /sass enable   — enable sass in this channel's category
  /sass disable  — disable sass in this channel's category
  /sass edit     — edit pattern, probability, or reply
  /sass info     — show settings for this channel's category
  /sass list     — list all categories with sass enabled
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
        ("/sass enable",  "🔑 Enable sass in this channel's category."),
        ("/sass disable", "🔑 Disable sass in this channel's category."),
        ("/sass edit",    "🔑 Edit pattern / probability / reply."),
        ("/sass info",    "🌐 Show sass settings for this channel's category."),
        ("/sass list",    "🌐 List all categories with sass enabled."),
    ],
}

# ─────────────────────────────────────────────
#  DB helpers
# ─────────────────────────────────────────────
async def setup_sass_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sass_rules (
            category_id BIGINT  PRIMARY KEY,
            pattern     TEXT    NOT NULL,
            probability REAL    NOT NULL,
            reply       TEXT    NOT NULL
        )
    """)
    # Migrate old channel_id column if it exists
    await conn.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='sass_rules' AND column_name='channel_id'
            ) THEN
                ALTER TABLE sass_rules RENAME COLUMN channel_id TO category_id;
            END IF;
        END $$;
    """)
    await conn.close()


async def db_get_sass_rule(category_id: int):
    conn = await get_db()
    row = await conn.fetchrow("SELECT * FROM sass_rules WHERE category_id = $1", category_id)
    await conn.close()
    return row


async def db_get_all_sass_rules():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM sass_rules")
    await conn.close()
    return rows


async def db_save_sass_rule(category_id: int, pattern: str, probability: float, reply: str):
    conn = await get_db()
    await conn.execute("""
        INSERT INTO sass_rules (category_id, pattern, probability, reply)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (category_id) DO UPDATE
            SET pattern = $2, probability = $3, reply = $4
    """, category_id, pattern, probability, reply)
    await conn.close()


async def db_delete_sass_rule(category_id: int):
    conn = await get_db()
    result = await conn.execute("DELETE FROM sass_rules WHERE category_id = $1", category_id)
    await conn.close()
    return result


def get_category_id(channel) -> int | None:
    """Return the category ID of a channel, or the channel ID itself if uncategorized."""
    if isinstance(channel, discord.TextChannel) and channel.category_id:
        return channel.category_id
    return channel.id  # fallback for channels with no category


# ─────────────────────────────────────────────
#  Slash command group
# ─────────────────────────────────────────────
sass = app_commands.Group(name="sass", description="Manage sass rules for this channel's category.")


# ─────────────────────────────────────────────
#  Cog
# ─────────────────────────────────────────────
class SassCog(commands.Cog, name="Sass"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache: { category_id: (compiled_pattern, probability, reply) }
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
                self._cache[row["category_id"]] = (compiled, row["probability"], row["reply"])
            except re.error:
                print(f"⚠️  Invalid sass regex for category {row['category_id']}, skipping.")

    async def handle_message(self, message: discord.Message):
        category_id = get_category_id(message.channel)
        rule = self._cache.get(category_id)
        if not rule:
            return
        pattern, probability, reply = rule
        if pattern.search(message.content) and random.random() < probability:
            await message.reply(reply)


# ─────────────────────────────────────────────
#  Permission helper
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


# ─────────────────────────────────────────────
#  Slash commands
# ─────────────────────────────────────────────
@sass.command(name="enable", description="Enable sass for this channel's category.")
@app_commands.describe(
    pattern="Regex pattern (default: questions starting with what/when/how/where or ending with ?)",
    probability="Trigger probability as a percentage e.g. 2 for 2% (default: 2)",
    reply="Reply text (default: apni maa se puch)",
)
async def sass_enable(
    interaction: discord.Interaction,
    pattern: str,
    probability: float,
    reply: str,
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

    category_id = get_category_id(interaction.channel)
    category_name = interaction.channel.category.name if interaction.channel.category else "No Category"
    prob_float = probability / 100

    await db_save_sass_rule(category_id, pattern, prob_float, reply)
    cog = interaction.client.get_cog("Sass")
    if cog:
        cog._cache[category_id] = (compiled, prob_float, reply)

    embed = discord.Embed(title="✅ Sass Enabled", color=discord.Color.green())
    embed.add_field(name="Category", value=f"📁 {category_name}", inline=True)
    embed.add_field(name="Probability", value=f"{probability}%", inline=True)
    embed.add_field(name="Pattern", value=f"`{pattern}`", inline=False)
    embed.add_field(name="Reply", value=reply, inline=False)
    embed.set_footer(text="Sass will trigger in all channels under this category.")
    await interaction.response.send_message(embed=embed)


@sass.command(name="disable", description="Disable sass for this channel's category.")
async def sass_disable(interaction: discord.Interaction):
    if not await _check_perm(interaction):
        return
    category_id = get_category_id(interaction.channel)
    result = await db_delete_sass_rule(category_id)
    cog = interaction.client.get_cog("Sass")
    if cog:
        cog._cache.pop(category_id, None)
    if result == "DELETE 0":
        await interaction.response.send_message("⚠️ Sass is not enabled for this category.", ephemeral=True)
        return
    category_name = interaction.channel.category.name if interaction.channel.category else "No Category"
    await interaction.response.send_message(f"🗑️ Sass disabled for category **{category_name}**.")


@sass.command(name="edit", description="Edit pattern, probability, or reply for this category.")
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
    category_id = get_category_id(interaction.channel)
    existing = await db_get_sass_rule(category_id)
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

    await db_save_sass_rule(category_id, new_pattern, new_prob, new_reply)
    cog = interaction.client.get_cog("Sass")
    if cog:
        cog._cache[category_id] = (compiled, new_prob, new_reply)

    category_name = interaction.channel.category.name if interaction.channel.category else "No Category"
    embed = discord.Embed(title="✏️ Sass Updated", color=discord.Color.blue())
    embed.add_field(name="Category", value=f"📁 {category_name}", inline=True)
    embed.add_field(name="Probability", value=f"{new_prob * 100:.1f}%", inline=True)
    embed.add_field(name="Pattern", value=f"`{new_pattern}`", inline=False)
    embed.add_field(name="Reply", value=new_reply, inline=False)
    await interaction.response.send_message(embed=embed)


@sass.command(name="info", description="Show sass settings for this channel's category.")
async def sass_info(interaction: discord.Interaction):
    category_id = get_category_id(interaction.channel)
    existing = await db_get_sass_rule(category_id)
    if not existing:
        await interaction.response.send_message("📭 Sass is not enabled for this category.", ephemeral=True)
        return
    category_name = interaction.channel.category.name if interaction.channel.category else "No Category"
    embed = discord.Embed(title="ℹ️ Sass Settings", color=discord.Color.gold())
    embed.add_field(name="Category", value=f"📁 {category_name}", inline=True)
    embed.add_field(name="Probability", value=f"{existing['probability'] * 100:.1f}%", inline=True)
    embed.add_field(name="Pattern", value=f"`{existing['pattern']}`", inline=False)
    embed.add_field(name="Reply", value=existing["reply"], inline=False)
    await interaction.response.send_message(embed=embed)


@sass.command(name="list", description="List all categories with sass enabled.")
async def sass_list(interaction: discord.Interaction):
    rows = await db_get_all_sass_rules()
    if not rows:
        await interaction.response.send_message("📭 No sass rules yet. Use `/sass enable`.", ephemeral=True)
        return
    embed = discord.Embed(title="📋 Sass Rules", color=discord.Color.blurple())
    for row in rows:
        category = interaction.guild.get_channel(row["category_id"])
        cname = f"📁 {category.name}" if category else f"Unknown ({row['category_id']})"
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
