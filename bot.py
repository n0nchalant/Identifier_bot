import discord
from discord.ext import commands
import json
import os
import asyncio
import random

# ─────────────────────────────────────────────
#  Re-roll range (min/max messages after trigger)
# ─────────────────────────────────────────────
REROLL_MIN = 50
REROLL_MAX = 500

# ─────────────────────────────────────────────
#  Load config
# ─────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ─────────────────────────────────────────────
#  Bot setup
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory message counters: { channel_id: count }
counters = {}
# Next trigger threshold (re-rolled after each fire): { channel_id: count }
next_counts = {}

# ─────────────────────────────────────────────
#  Events
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅  Logged in as {bot.user} ({bot.user.id})")
    print("─" * 40)
    cfg = load_config()
    for trigger in cfg.get("triggers", []):
        cid = trigger["channel_id"]
        counters.setdefault(cid, 0)
        # Use the saved next_count if present, otherwise use config value as first threshold
        next_counts.setdefault(cid, trigger["message_count"])
        channel = bot.get_channel(cid)
        cname = channel.name if channel else f"ID {cid}"
        print(f"📡  Watching  #{cname}  ─  next trigger in {next_counts[cid]} messages")
    print("─" * 40)


@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        await bot.process_commands(message)
        return

    cfg = load_config()
    cid = message.channel.id

    for trigger in cfg.get("triggers", []):
        if trigger["channel_id"] == cid:
            counters[cid] = counters.get(cid, 0) + 1
            # Use the live next_count (re-rolled), fall back to config value on first run
            target = next_counts.get(cid, trigger["message_count"])
            current = counters[cid]

            # Optional: show progress in console
            print(f"[#{message.channel.name}] {current}/{target}")

            if current >= target:
                counters[cid] = 0          # reset counter
                new_target = random.randint(REROLL_MIN, REROLL_MAX)
                next_counts[cid] = new_target
                print(f"🎲  Re-rolled #{message.channel.name}  ─  next trigger in {new_target} messages")
                await message.channel.send(trigger["custom_message"])

    await bot.process_commands(message)


# ─────────────────────────────────────────────
#  Commands  (admin / management)
# ─────────────────────────────────────────────

@bot.command(name="addtrigger")
@commands.has_permissions(administrator=True)
async def add_trigger(ctx, channel: discord.TextChannel, count: int, *, phrase: str):
    """Add a new message-count trigger.
    Usage: !addtrigger #channel 50 Hello everyone!
    """
    cfg = load_config()
    triggers = cfg.setdefault("triggers", [])

    # Check for duplicate channel
    for t in triggers:
        if t["channel_id"] == channel.id:
            await ctx.send(
                f"⚠️  A trigger for {channel.mention} already exists. "
                f"Remove it first with `!removetrigger {channel.id}`."
            )
            return

    if count < 1:
        await ctx.send("❌  Message count must be at least 1.")
        return

    triggers.append({
        "channel_id": channel.id,
        "message_count": count,
        "custom_message": phrase,
    })
    counters[channel.id] = 0
    next_counts[channel.id] = count   # first threshold uses the provided count
    save_config(cfg)

    embed = discord.Embed(
        title="✅  Trigger Added",
        color=discord.Color.green()
    )
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="First trigger at", value=f"{count} messages", inline=True)
    embed.add_field(name="After that", value=f"Re-rolls randomly between {REROLL_MIN}–{REROLL_MAX} messages", inline=True)
    embed.add_field(name="Message", value=phrase, inline=False)
    await ctx.send(embed=embed)


@bot.command(name="removetrigger")
@commands.has_permissions(administrator=True)
async def remove_trigger(ctx, channel: discord.TextChannel):
    """Remove the trigger for a channel.
    Usage: !removetrigger #channel
    """
    cfg = load_config()
    before = len(cfg.get("triggers", []))
    cfg["triggers"] = [t for t in cfg.get("triggers", []) if t["channel_id"] != channel.id]

    if len(cfg["triggers"]) == before:
        await ctx.send(f"⚠️  No trigger found for {channel.mention}.")
        return

    counters.pop(channel.id, None)
    next_counts.pop(channel.id, None)
    save_config(cfg)
    await ctx.send(f"🗑️  Trigger for {channel.mention} removed.")


@bot.command(name="edittrigger")
@commands.has_permissions(administrator=True)
async def edit_trigger(ctx, channel: discord.TextChannel, count: int, *, phrase: str):
    """Edit an existing trigger.
    Usage: !edittrigger #channel 100 New message here!
    """
    cfg = load_config()
    for t in cfg.get("triggers", []):
        if t["channel_id"] == channel.id:
            t["message_count"] = count
            t["custom_message"] = phrase
            counters[channel.id] = 0
            next_counts[channel.id] = count   # reset to provided count, rerolls after next fire
            save_config(cfg)

            embed = discord.Embed(title="✏️  Trigger Updated", color=discord.Color.blue())
            embed.add_field(name="Channel", value=channel.mention, inline=True)
            embed.add_field(name="Next trigger at", value=f"{count} messages", inline=True)
            embed.add_field(name="After that", value=f"Re-rolls randomly between {REROLL_MIN}–{REROLL_MAX} messages", inline=True)
            embed.add_field(name="Message", value=phrase, inline=False)
            await ctx.send(embed=embed)
            return

    await ctx.send(f"⚠️  No trigger found for {channel.mention}. Use `!addtrigger` to create one.")


@bot.command(name="listtriggers")
@commands.has_permissions(administrator=True)
async def list_triggers(ctx):
    """List all active triggers."""
    cfg = load_config()
    triggers = cfg.get("triggers", [])

    if not triggers:
        await ctx.send("📭  No triggers configured yet. Use `!addtrigger` to add one.")
        return

    embed = discord.Embed(title="📋  Active Triggers", color=discord.Color.blurple())
    for t in triggers:
        channel = bot.get_channel(t["channel_id"])
        cname = channel.mention if channel else f"Unknown ({t['channel_id']})"
        current = counters.get(t["channel_id"], 0)
        nxt = next_counts.get(t["channel_id"], t["message_count"])
        embed.add_field(
            name=f"{cname}",
            value=(
                f"**Next trigger at:** {nxt} messages\n"
                f"**Progress:** {current}/{nxt}\n"
                f"**Re-roll range:** {REROLL_MIN}–{REROLL_MAX} messages\n"
                f"**Message:** {t['custom_message']}"
            ),
            inline=False,
        )
    await ctx.send(embed=embed)


@bot.command(name="resetcounter")
@commands.has_permissions(administrator=True)
async def reset_counter(ctx, channel: discord.TextChannel):
    """Manually reset the counter for a channel.
    Usage: !resetcounter #channel
    """
    cfg = load_config()
    counters[channel.id] = 0
    # Roll a fresh random target on manual reset too
    new_target = random.randint(REROLL_MIN, REROLL_MAX)
    next_counts[channel.id] = new_target
    await ctx.send(f"🔄  Counter for {channel.mention} reset. Next trigger in **{new_target}** messages.")


@bot.command(name="bothelp")
async def bot_help(ctx):
    """Show all available commands."""
    embed = discord.Embed(
        title="🤖  Message Counter Bot — Commands",
        color=discord.Color.gold(),
        description="All commands require **Administrator** permission unless noted."
    )
    cmds = [
        ("!addtrigger #channel N phrase", "Add a trigger: send *phrase* every N messages in *channel*."),
        ("!removetrigger #channel",       "Delete the trigger for a channel."),
        ("!edittrigger #channel N phrase","Update an existing trigger."),
        ("!listtriggers",                 "Show all active triggers and their progress."),
        ("!resetcounter #channel",        "Reset the message counter for a channel."),
        ("!bothelp",                      "Show this help message. (No permission needed)"),
    ]
    for name, desc in cmds:
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
        await ctx.send(f"❌  Missing argument: `{error.param.name}`. Type `!bothelp` for usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌  Invalid argument. Make sure you mention a valid channel and a number.")
    else:
        await ctx.send(f"⚠️  Unexpected error: {error}")
        raise error


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # Railway (and most cloud platforms) inject secrets as environment variables.
    # Falls back to config.json for local development.
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        cfg = load_config()
        token = cfg.get("bot_token", "")
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        print("❌  No bot token found. Set BOT_TOKEN env variable or config.json.")
        exit(1)
    bot.run(token)
