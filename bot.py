import discord
from discord.ext import commands
import os
import asyncio
import random
import asyncpg

# ─────────────────────────────────────────────
#  Re-roll range (min/max messages after trigger)
# ─────────────────────────────────────────────
REROLL_MIN = 500
REROLL_MAX = 800

# ─────────────────────────────────────────────
#  Bot setup
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="_", intents=intents, help_command=None)

# In-memory counters (reset on restart, that's fine)
# { channel_id: current_count }
counters = {}
# { channel_id: next_threshold }
next_counts = {}

# ─────────────────────────────────────────────
#  Database helpers  —  triggers
# ─────────────────────────────────────────────
async def get_db():
    return await asyncpg.connect(os.environ["DATABASE_URL"])


async def setup_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS triggers (
            channel_id     BIGINT PRIMARY KEY,
            message_count  INT    NOT NULL,
            custom_message TEXT   NOT NULL,
            next_count     INT    NOT NULL
        )
    """)
    await conn.close()


async def db_get_all_triggers():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM triggers")
    await conn.close()
    return rows


async def db_get_trigger(channel_id: int):
    conn = await get_db()
    row = await conn.fetchrow("SELECT * FROM triggers WHERE channel_id = $1", channel_id)
    await conn.close()
    return row


async def db_add_trigger(channel_id: int, message_count: int, custom_message: str):
    conn = await get_db()
    await conn.execute("""
        INSERT INTO triggers (channel_id, message_count, custom_message, next_count)
        VALUES ($1, $2, $3, $2)
    """, channel_id, message_count, custom_message)
    await conn.close()


async def db_remove_trigger(channel_id: int):
    conn = await get_db()
    result = await conn.execute("DELETE FROM triggers WHERE channel_id = $1", channel_id)
    await conn.close()
    return result


async def db_update_trigger(channel_id: int, message_count: int, custom_message: str):
    conn = await get_db()
    await conn.execute("""
        UPDATE triggers
        SET message_count = $2, custom_message = $3, next_count = $2
        WHERE channel_id = $1
    """, channel_id, message_count, custom_message)
    await conn.close()


async def db_update_next_count(channel_id: int, next_count: int):
    conn = await get_db()
    await conn.execute(
        "UPDATE triggers SET next_count = $2 WHERE channel_id = $1",
        channel_id, next_count
    )
    await conn.close()


# ─────────────────────────────────────────────
#  Database helpers  —  reactions
# ─────────────────────────────────────────────
async def setup_reactions_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS reactions (
            id      SERIAL PRIMARY KEY,
            keyword TEXT   NOT NULL,
            emoji   TEXT   NOT NULL,
            UNIQUE(keyword)
        )
    """)
    await conn.close()


async def db_get_all_reactions():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM reactions ORDER BY id")
    await conn.close()
    return rows


async def db_add_reaction(keyword: str, emoji: str):
    conn = await get_db()
    await conn.execute(
        "INSERT INTO reactions (keyword, emoji) VALUES ($1, $2) "
        "ON CONFLICT (keyword) DO UPDATE SET emoji = $2",
        keyword.lower(), emoji
    )
    await conn.close()


async def db_remove_reaction(keyword: str):
    conn = await get_db()
    result = await conn.execute(
        "DELETE FROM reactions WHERE keyword = $1", keyword.lower()
    )
    await conn.close()
    return result


# ─────────────────────────────────────────────
#  Reaction checker  (called from on_message)
# ─────────────────────────────────────────────
async def handle_reactions(message: discord.Message):
    """Check message text against all keyword→emoji rules and react."""
    rows = await db_get_all_reactions()
    content_lower = message.content.lower()
    for row in rows:
        if row["keyword"] in content_lower:
            try:
                await message.add_reaction(row["emoji"])
            except discord.HTTPException:
                print(f"⚠️  Could not react with {row['emoji']} — invalid emoji?")


# ─────────────────────────────────────────────
#  Events
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅  Logged in as {bot.user} ({bot.user.id})")
    print("─" * 40)

    await setup_db()
    await setup_reactions_db()

    rows = await db_get_all_triggers()
    for row in rows:
        cid = row["channel_id"]
        counters[cid] = 0
        next_counts[cid] = row["next_count"]
        channel = bot.get_channel(cid)
        cname = channel.name if channel else f"ID {cid}"
        print(f"📡  Watching  #{cname}  ─  next trigger in {row['next_count']} messages")

    print("─" * 40)


@bot.event
async def on_message(message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    cid = message.channel.id
    trigger = await db_get_trigger(cid)

    if trigger:
        counters[cid] = counters.get(cid, 0) + 1
        target = next_counts.get(cid, trigger["next_count"])
        current = counters[cid]

        print(f"[#{message.channel.name}] {current}/{target}")

        if current >= target:
            counters[cid] = 0
            new_target = random.randint(REROLL_MIN, REROLL_MAX)
            next_counts[cid] = new_target
            await db_update_next_count(cid, new_target)
            print(f"🎲  Re-rolled #{message.channel.name}  ─  next trigger in {new_target} messages")
            await message.channel.send(trigger["custom_message"])

    await handle_reactions(message)
    await bot.process_commands(message)


# ─────────────────────────────────────────────
#  Commands  —  triggers
# ─────────────────────────────────────────────
@bot.command(name="addtrigger")
@commands.has_permissions(administrator=True)
async def add_trigger(ctx, channel: discord.TextChannel, count: int, *, phrase: str):
    """Usage: _addtrigger #channel 50 Hello everyone!"""
    existing = await db_get_trigger(channel.id)
    if existing:
        await ctx.send(
            f"⚠️  A trigger for {channel.mention} already exists. "
            f"Remove it first with `_removetrigger`."
        )
        return

    if count < 1:
        await ctx.send("❌  Message count must be at least 1.")
        return

    await db_add_trigger(channel.id, count, phrase)
    counters[channel.id] = 0
    next_counts[channel.id] = count

    embed = discord.Embed(title="✅  Trigger Added", color=discord.Color.green())
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="First trigger at", value=f"{count} messages", inline=True)
    embed.add_field(name="After that", value=f"Re-rolls randomly between {REROLL_MIN}–{REROLL_MAX} messages", inline=True)
    embed.add_field(name="Message", value=phrase, inline=False)
    await ctx.send(embed=embed)


@bot.command(name="removetrigger")
@commands.has_permissions(administrator=True)
async def remove_trigger(ctx, channel: discord.TextChannel):
    """Usage: _removetrigger #channel"""
    result = await db_remove_trigger(channel.id)
    if result == "DELETE 0":
        await ctx.send(f"⚠️  No trigger found for {channel.mention}.")
        return

    counters.pop(channel.id, None)
    next_counts.pop(channel.id, None)
    await ctx.send(f"🗑️  Trigger for {channel.mention} removed.")


@bot.command(name="edittrigger")
@commands.has_permissions(administrator=True)
async def edit_trigger(ctx, channel: discord.TextChannel, count: int, *, phrase: str):
    """Usage: _edittrigger #channel 100 New message here!"""
    existing = await db_get_trigger(channel.id)
    if not existing:
        await ctx.send(f"⚠️  No trigger found for {channel.mention}. Use `_addtrigger` first.")
        return

    await db_update_trigger(channel.id, count, phrase)
    counters[channel.id] = 0
    next_counts[channel.id] = count

    embed = discord.Embed(title="✏️  Trigger Updated", color=discord.Color.blue())
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Next trigger at", value=f"{count} messages", inline=True)
    embed.add_field(name="After that", value=f"Re-rolls randomly between {REROLL_MIN}–{REROLL_MAX} messages", inline=True)
    embed.add_field(name="Message", value=phrase, inline=False)
    await ctx.send(embed=embed)


@bot.command(name="listtriggers")
@commands.has_permissions(administrator=True)
async def list_triggers(ctx):
    """List all active triggers."""
    rows = await db_get_all_triggers()

    if not rows:
        await ctx.send("📭  No triggers configured yet. Use `_addtrigger` to add one.")
        return

    embed = discord.Embed(title="📋  Active Triggers", color=discord.Color.blurple())
    for row in rows:
        channel = bot.get_channel(row["channel_id"])
        cname = channel.mention if channel else f"Unknown ({row['channel_id']})"
        current = counters.get(row["channel_id"], 0)
        nxt = next_counts.get(row["channel_id"], row["next_count"])
        embed.add_field(
            name=cname,
            value=(
                f"**Next trigger at:** {nxt} messages\n"
                f"**Progress:** {current}/{nxt}\n"
                f"**Re-roll range:** {REROLL_MIN}–{REROLL_MAX} messages\n"
                f"**Message:** {row['custom_message']}"
            ),
            inline=False,
        )
    await ctx.send(embed=embed)


@bot.command(name="resetcounter")
@commands.has_permissions(administrator=True)
async def reset_counter(ctx, channel: discord.TextChannel):
    """Usage: _resetcounter #channel"""
    counters[channel.id] = 0
    new_target = random.randint(REROLL_MIN, REROLL_MAX)
    next_counts[channel.id] = new_target
    await db_update_next_count(channel.id, new_target)
    await ctx.send(f"🔄  Counter for {channel.mention} reset. Next trigger in **{new_target}** messages.")


# ─────────────────────────────────────────────
#  Commands  —  reactions
# ─────────────────────────────────────────────
@bot.command(name="addreaction")
@commands.has_permissions(administrator=True)
async def add_reaction_cmd(ctx, emoji: str, *, keyword: str):
    """React with an emoji whenever a keyword appears in a message.
    Usage: _addreaction 👍 good job
    """
    await db_add_reaction(keyword, emoji)
    embed = discord.Embed(title="✅  Reaction Rule Added", color=discord.Color.green())
    embed.add_field(name="Keyword", value=f"`{keyword.lower()}`", inline=True)
    embed.add_field(name="Emoji", value=emoji, inline=True)
    embed.set_footer(text="Bot will react whenever this keyword appears in any message.")
    await ctx.send(embed=embed)


@bot.command(name="removereaction")
@commands.has_permissions(administrator=True)
async def remove_reaction_cmd(ctx, *, keyword: str):
    """Remove a keyword reaction rule.
    Usage: _removereaction good job
    """
    result = await db_remove_reaction(keyword)
    if result == "DELETE 0":
        await ctx.send(f"⚠️  No reaction rule found for keyword `{keyword}`.")
        return
    await ctx.send(f"🗑️  Reaction rule for `{keyword}` removed.")


@bot.command(name="listreactions")
@commands.has_permissions(administrator=True)
async def list_reactions_cmd(ctx):
    """List all keyword→emoji reaction rules."""
    rows = await db_get_all_reactions()
    if not rows:
        await ctx.send("📭  No reaction rules set yet. Use `_addreaction` to add one.")
        return
    embed = discord.Embed(title="📋  Reaction Rules", color=discord.Color.blurple())
    for row in rows:
        embed.add_field(name=f"`{row['keyword']}`", value=row["emoji"], inline=True)
    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
#  Help
# ─────────────────────────────────────────────
@bot.command(name="help")
async def bot_help(ctx):
    """Show all available commands."""
    embed = discord.Embed(
        title="🤖  Bot Commands",
        color=discord.Color.gold(),
        description="All commands require **Administrator** permission."
    )
    embed.add_field(name="​", value="**── Message Triggers ──**", inline=False)
    trigger_cmds = [
        ("_addtrigger #channel N phrase",  "Send *phrase* every N messages in *channel*."),
        ("_removetrigger #channel",        "Delete the trigger for a channel."),
        ("_edittrigger #channel N phrase", "Update an existing trigger."),
        ("_listtriggers",                  "Show all triggers with live progress."),
        ("_resetcounter #channel",         "Reset the message counter for a channel."),
    ]
    for name, desc in trigger_cmds:
        embed.add_field(name=f"`{name}`", value=desc, inline=False)

    embed.add_field(name="​", value="**── Keyword Reactions ──**", inline=False)
    reaction_cmds = [
        ("_addreaction emoji keyword",  "React with emoji whenever keyword appears in a message."),
        ("_removereaction keyword",     "Remove a reaction rule."),
        ("_listreactions",              "List all reaction rules."),
    ]
    for name, desc in reaction_cmds:
        embed.add_field(name=f"`{name}`", value=desc, inline=False)

    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
#  Error handling
# ─────────────────────────────────────────────
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫  You need **Administrator** permission to use that command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌  Missing argument: `{error.param.name}`. Type `_help` for usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌  Invalid argument. Make sure you mention a valid channel and a number.")
    else:
        await ctx.send(f"⚠️  Unexpected error: {error}")
        raise error


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        try:
            import json
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            with open(cfg_path) as f:
                token = json.load(f).get("bot_token", "")
        except FileNotFoundError:
            pass
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        print("❌  No bot token found. Set BOT_TOKEN env variable or config.json.")
        exit(1)
    bot.run(token)
