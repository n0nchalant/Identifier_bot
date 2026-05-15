import discord
from discord.ext import commands
import os
import asyncio
import random
import asyncpg
import re

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
#  Database helpers  —  custom commands
# ─────────────────────────────────────────────
async def setup_custom_commands_db():
    """
    Each custom command has:
      - name       : the trigger word (e.g. "rules" → user types _rules)
      - text       : optional text response
      - media_url  : optional image/video/file URL (Discord CDN link)
    """
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_commands (
            name       TEXT PRIMARY KEY,
            text       TEXT,
            media_url  TEXT
        )
    """)
    await conn.close()


async def db_get_custom_command(name: str):
    conn = await get_db()
    row = await conn.fetchrow(
        "SELECT * FROM custom_commands WHERE name = $1", name.lower()
    )
    await conn.close()
    return row


async def db_get_all_custom_commands():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM custom_commands ORDER BY name")
    await conn.close()
    return rows


async def db_save_custom_command(name: str, text: str | None, media_url: str | None):
    conn = await get_db()
    await conn.execute("""
        INSERT INTO custom_commands (name, text, media_url)
        VALUES ($1, $2, $3)
        ON CONFLICT (name) DO UPDATE SET text = $2, media_url = $3
    """, name.lower(), text, media_url)
    await conn.close()


async def db_delete_custom_command(name: str):
    conn = await get_db()
    result = await conn.execute(
        "DELETE FROM custom_commands WHERE name = $1", name.lower()
    )
    await conn.close()
    return result



# ─────────────────────────────────────────────
#  Database helpers  —  allowed roles
# ─────────────────────────────────────────────
async def setup_roles_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS allowed_roles (
            role_id BIGINT PRIMARY KEY
        )
    """)
    await conn.close()


async def db_get_allowed_roles():
    conn = await get_db()
    rows = await conn.fetch("SELECT role_id FROM allowed_roles")
    await conn.close()
    return [row["role_id"] for row in rows]


async def db_add_allowed_role(role_id: int):
    conn = await get_db()
    await conn.execute(
        "INSERT INTO allowed_roles (role_id) VALUES ($1) ON CONFLICT DO NOTHING",
        role_id
    )
    await conn.close()


async def db_remove_allowed_role(role_id: int):
    conn = await get_db()
    result = await conn.execute(
        "DELETE FROM allowed_roles WHERE role_id = $1", role_id
    )
    await conn.close()
    return result


# ─────────────────────────────────────────────
#  Permission check
#  Passes if user is Administrator OR has an allowed role.
#  If no allowed roles are set, only Administrators can use commands.
# ─────────────────────────────────────────────
async def has_bot_permission(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    allowed = await db_get_allowed_roles()
    if not allowed:
        return False
    member_role_ids = [r.id for r in member.roles]
    return any(rid in allowed for rid in member_role_ids)


def bot_permission_check():
    """Use this decorator instead of @commands.has_permissions(administrator=True)."""
    async def predicate(ctx):
        if await has_bot_permission(ctx.author):
            return True
        raise commands.CheckFailure("no_permission")
    return commands.check(predicate)

# ─────────────────────────────────────────────
#  Reaction checker  (called from on_message)
# ─────────────────────────────────────────────
async def handle_reactions(message: discord.Message):
    rows = await db_get_all_reactions()
    content_lower = message.content.lower()
    for row in rows:
        if if "?" in row["keyword"]:
            pattern = row["keyword"]
        else:
            pattern = r'\b' + re.escape(row["keyword"]) + r'\b'
        if re.search(pattern, content_lower):
            try:
                await message.add_reaction(row["emoji"])
            except discord.HTTPException:
                print(f"⚠️  Could not react with {row['emoji']} — invalid emoji?")


# ─────────────────────────────────────────────
#  Custom command handler  (called from on_message)
# ─────────────────────────────────────────────
async def handle_custom_commands(message: discord.Message):
    """
    Check if the message is a custom command (e.g. _rules)
    and reply with the stored text and/or media.
    """
    if not message.content.startswith("_"):
        return

    # Extract the command name (first word after the prefix, lowercased)
    cmd_name = message.content[1:].split()[0].lower()

    # Skip built-in bot commands so they still work normally
    if cmd_name in bot.all_commands:
        return

    row = await db_get_custom_command(cmd_name)
    if not row:
        return

    text      = row["text"]
    media_url = row["media_url"]

    # Build the reply
    if text and media_url:
        await message.channel.send(content=text)
        await message.channel.send(media_url)
    elif text:
        await message.channel.send(content=text)
    elif media_url:
        await message.channel.send(media_url)


# ─────────────────────────────────────────────
#  Events
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅  Logged in as {bot.user} ({bot.user.id})")
    print("─" * 40)

    await setup_db()
    await setup_reactions_db()
    await setup_custom_commands_db()
    await setup_roles_db()

    rows = await db_get_all_triggers()
    for row in rows:
        cid = row["channel_id"]
        counters[cid] = 0
        next_counts[cid] = row["next_count"]
        channel = bot.get_channel(cid)
        cname = channel.name if channel else f"ID {cid}"
        print(f"📡  Watching  #{cname}  ─  next trigger in {row['next_count']} messages")

    # Print all registered custom commands
    cmds = await db_get_all_custom_commands()
    for c in cmds:
        print(f"💬  Custom command: _{c['name']}")

    print("─" * 40)


@bot.event
async def on_message(message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    # ── Message counter trigger ──
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

    # ── Keyword reactions ──
    await handle_reactions(message)

    # ── Custom commands ──
    await handle_custom_commands(message)

    await bot.process_commands(message)


# ─────────────────────────────────────────────
#  Commands  —  role management
#  Only Administrators can add/remove allowed roles
# ─────────────────────────────────────────────
@bot.command(name="addrole")
@commands.has_permissions(administrator=True)
async def add_allowed_role(ctx, role: discord.Role):
    """Allow a role to use bot commands.
    Usage: _addrole @Moderator
    """
    await db_add_allowed_role(role.id)
    embed = discord.Embed(title="✅  Role Added", color=discord.Color.green())
    embed.add_field(name="Role", value=role.mention, inline=True)
    embed.set_footer(text="Members with this role can now use bot commands.")
    await ctx.send(embed=embed)


@bot.command(name="removerole")
@commands.has_permissions(administrator=True)
async def remove_allowed_role(ctx, role: discord.Role):
    """Remove a role from bot command access.
    Usage: _removerole @Moderator
    """
    result = await db_remove_allowed_role(role.id)
    if result == "DELETE 0":
        await ctx.send(f"⚠️  {role.mention} is not in the allowed roles list.")
        return
    await ctx.send(f"🗑️  {role.mention} removed from allowed roles.")


@bot.command(name="listroles")
@commands.has_permissions(administrator=True)
async def list_allowed_roles(ctx):
    """List all roles that can use bot commands."""
    allowed = await db_get_allowed_roles()
    if not allowed:
        await ctx.send("📭  No roles set — only Administrators can use commands.")
        return
    embed = discord.Embed(title="🔑  Allowed Roles", color=discord.Color.blurple())
    roles_text = []
    for rid in allowed:
        role = ctx.guild.get_role(rid)
        roles_text.append(role.mention if role else f"Unknown role ({rid})")
    embed.description = "\n".join(roles_text)
    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
#  Commands  —  triggers
# ─────────────────────────────────────────────
@bot.command(name="addtrigger")
@bot_permission_check()
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
@bot_permission_check()
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
@bot_permission_check()
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
@bot_permission_check()
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
@bot_permission_check()
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
@bot_permission_check()
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
@bot_permission_check()
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
@bot_permission_check()
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
#  Commands  —  custom commands
# ─────────────────────────────────────────────
@bot.command(name="addcmd")
@bot_permission_check()
async def add_custom_cmd(ctx, name: str, *, text: str = ""):
    """
    Create a custom command that replies with text and/or an attached file.
    Attach an image/video/file to the message to include media.

    Usage (text only):      _addcmd rules Please read the rules!
    Usage (media only):     _addcmd meme   [attach image, no text needed]
    Usage (text + media):   _addcmd welcome Hello! [attach image]
    """
    name = name.lower()

    # Check the name doesn't clash with a built-in command
    if name in bot.all_commands:
        await ctx.send(f"❌  `_{name}` is a built-in command and cannot be overridden.")
        return

    # Grab attachment URL if a file was uploaded with the command
    media_url = None
    if ctx.message.attachments:
        media_url = ctx.message.attachments[0].url

    if not text and not media_url:
        await ctx.send("❌  Provide some text and/or attach a file.")
        return

    await db_save_custom_command(name, text or None, media_url)

    embed = discord.Embed(title="✅  Custom Command Saved", color=discord.Color.green())
    embed.add_field(name="Command", value=f"`_{name}`", inline=True)
    if text:
        embed.add_field(name="Text", value=text[:200], inline=False)
    if media_url:
        embed.add_field(name="Media", value="✅ Attachment saved", inline=True)
    embed.set_footer(text=f"Anyone can now use _{name} to trigger this response.")
    await ctx.send(embed=embed)


@bot.command(name="removecmd")
@bot_permission_check()
async def remove_custom_cmd(ctx, name: str):
    """Delete a custom command.
    Usage: _removecmd rules
    """
    result = await db_delete_custom_command(name)
    if result == "DELETE 0":
        await ctx.send(f"⚠️  No custom command found with name `_{name}`.")
        return
    await ctx.send(f"🗑️  Custom command `_{name}` deleted.")


@bot.command(name="listcmds")
async def list_custom_cmds(ctx):
    """List all custom commands. Available to everyone."""
    rows = await db_get_all_custom_commands()
    if not rows:
        await ctx.send("📭  No custom commands created yet. Admins can use `_addcmd` to add one.")
        return

    embed = discord.Embed(title="📋  Custom Commands", color=discord.Color.blurple())
    for row in rows:
        parts = []
        if row["text"]:
            parts.append("📝 Text")
        if row["media_url"]:
            parts.append("🖼️ Media")
        embed.add_field(name=f"`_{row['name']}`", value=" + ".join(parts), inline=True)
    embed.set_footer(text="Type the command to use it!")
    await ctx.send(embed=embed)


@bot.command(name="editcmd")
@bot_permission_check()
async def edit_custom_cmd(ctx, name: str, *, text: str = ""):
    """
    Edit an existing custom command. Attach a new file to replace media.
    Usage: _editcmd rules Updated rules text here  [optionally attach new image]
    """
    name = name.lower()
    existing = await db_get_custom_command(name)
    if not existing:
        await ctx.send(f"⚠️  No custom command `_{name}` found. Use `_addcmd` to create it.")
        return

    media_url = existing["media_url"]  # keep old media by default
    if ctx.message.attachments:
        media_url = ctx.message.attachments[0].url  # replace with new attachment

    new_text = text or existing["text"]  # keep old text if none provided

    await db_save_custom_command(name, new_text, media_url)

    embed = discord.Embed(title="✏️  Custom Command Updated", color=discord.Color.blue())
    embed.add_field(name="Command", value=f"`_{name}`", inline=True)
    if new_text:
        embed.add_field(name="Text", value=new_text[:200], inline=False)
    if media_url:
        embed.add_field(name="Media", value="✅ Saved", inline=True)
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
        description="🔒 = Admin only   |   🔑 = Admin or allowed role   |   🌐 = Everyone"
    )

    embed.add_field(name="​", value="**── Role Management ──**", inline=False)
    for name, desc in [
        ("_addrole @role",    "🔒 Allow a role to use bot commands."),
        ("_removerole @role", "🔒 Remove a role from bot access."),
        ("_listroles",        "🔒 List all allowed roles."),
    ]:
        embed.add_field(name=f"`{name}`", value=desc, inline=False)

    embed.add_field(name="​", value="**── Message Triggers ──**", inline=False)
    for name, desc in [
        ("_addtrigger #ch N phrase",   "🔑 Send phrase every N messages in a channel."),
        ("_removetrigger #ch",         "🔑 Remove a channel trigger."),
        ("_edittrigger #ch N phrase",  "🔑 Edit an existing trigger."),
        ("_listtriggers",              "🔑 Show all triggers with progress."),
        ("_resetcounter #ch",          "🔑 Reset a channel's message counter."),
    ]:
        embed.add_field(name=f"`{name}`", value=desc, inline=False)

    embed.add_field(name="​", value="**── Keyword Reactions ──**", inline=False)
    for name, desc in [
        ("_addreaction emoji keyword", "🔑 React with emoji when keyword is seen."),
        ("_removereaction keyword",    "🔑 Remove a reaction rule."),
        ("_listreactions",             "🔑 List all reaction rules."),
    ]:
        embed.add_field(name=f"`{name}`", value=desc, inline=False)

    embed.add_field(name="​", value="**── Custom Commands ──**", inline=False)
    for name, desc in [
        ("_addcmd name [text]",  "🔑 Create a command. Attach image/video for media."),
        ("_editcmd name [text]", "🔑 Edit a command. Attach new file to replace media."),
        ("_removecmd name",      "🔑 Delete a custom command."),
        ("_listcmds",            "🌐 List all custom commands."),
    ]:
        embed.add_field(name=f"`{name}`", value=desc, inline=False)

    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
#  Error handling
# ─────────────────────────────────────────────
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # custom commands handle unknown commands, ignore this
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
