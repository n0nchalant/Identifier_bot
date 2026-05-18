"""
bot.py — Entry point. Loads all cogs and wires shared events.
Each feature lives in its own cog under cogs/. Adding a new feature
means creating a new cog file — nothing here needs to change.
"""
import os
import sys
import asyncio
import discord
from discord.ext import commands

from db import setup_all_tables

# ─────────────────────────────────────────────
#  Bot setup
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="_", intents=intents, help_command=None)

# Cogs to load (order matters for help page ordering)
COGS = [
    "cogs.custom_commands",
    "cogs.reactions",
    "cogs.triggers",
    "cogs.roles",
    "cogs.sass",
    "cogs.help",
]


# ─────────────────────────────────────────────
#  Events
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅  Logged in as {bot.user} ({bot.user.id})")
    print("─" * 40)
    await setup_all_tables()
    # Let the triggers cog pre-load its state from DB
    triggers_cog = bot.get_cog("Triggers")
    if triggers_cog:
        await triggers_cog.load_from_db()
    # Temporary hard resync - remove after it works
    bot.tree.clear_commands(guild=discord.Object(id=929226506926960660))
    await bot.tree.sync(guild=discord.Object(id=929226506926960660))
    print("✅  Slash commands synced")
    print("─" * 40)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    # Delegate to each feature cog's handle_message
    triggers_cog = bot.get_cog("Triggers")
    if triggers_cog:
        await triggers_cog.handle_message(message)

    reactions_cog = bot.get_cog("Reactions")
    if reactions_cog:
        await reactions_cog.handle_message(message)

    custom_cog = bot.get_cog("Custom Commands")
    if custom_cog:
        await custom_cog.handle_message(message)

    sass_cog = bot.get_cog("Sass")
    if sass_cog:
        await sass_cog.handle_message(message)

    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # custom commands handle unknown input
    if isinstance(error, commands.CheckFailure):
        await ctx.send("🚫  You don't have permission to use that command.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫  You need **Administrator** permission to use that command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌  Missing argument: `{error.param.name}`. Type `_help` for usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌  Invalid argument. Make sure you mention a valid channel and a number.")
    else:
        await ctx.send(f"⚠️  Unexpected error: {error}")
        raise error


# ─────────────────────────────────────────────
#  Startup
# ─────────────────────────────────────────────
async def main():
    async with bot:
        for cog in COGS:
            await bot.load_extension(cog)
            print(f"🔌  Loaded cog: {cog}")
        await bot.start(token)


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
        print("❌  No bot token found. Set BOT_TOKEN env variable or add it to config.json.")
        sys.exit(1)

    asyncio.run(main())
