"""
cogs/custom_commands.py — User-defined text/media commands with usage tracking.

Each command records:
  - who added it (user ID + display name)
  - how many times it has been used
"""
import discord
from discord.ext import commands
from db import (
    db_get_custom_command, db_get_all_custom_commands,
    db_save_custom_command, db_delete_custom_command,
    db_increment_use_count,
)
from permissions import bot_permission_check


# ─────────────────────────────────────────────
#  Help definition (consumed by HelpCog)
# ─────────────────────────────────────────────
HELP_PAGE = {
    "title": "🤖 Bot Commands",
    "subtitle": "📖 Custom Commands",
    "fields": [
        ("_addcmd name [text]",   "🔑 Create a command. Attach image/video for media."),
        ("_editcmd name [text]",  "🔑 Edit a command. Attach new file to replace media."),
        ("_removecmd name",       "🔑 Delete a custom command."),
        ("_listcmds",             "🌐 List all custom commands."),
        ("_cmdinfo name",         "🌐 Show who added a command and its use count."),
    ],
}


class CustomCommandsCog(commands.Cog, name="Custom Commands"):
    """Create, edit, remove, and trigger custom text/media commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Called from bot's on_message ──────────
    async def handle_message(self, message: discord.Message):
        """Fire a custom command if the message matches _{name}."""
        if not message.content.startswith("_"):
            return

        cmd_name = message.content[1:].split()[0].lower()

        # Skip built-in commands
        if cmd_name in self.bot.all_commands:
            return

        row = await db_get_custom_command(cmd_name)
        if not row:
            return

        # Increment use counter
        await db_increment_use_count(cmd_name)

        text      = row["text"]
        media_url = row["media_url"]

        if text and media_url:
            await message.channel.send(content=text)
            await message.channel.send(media_url)
        elif text:
            await message.channel.send(content=text)
        elif media_url:
            await message.channel.send(media_url)

    # ── Commands ──────────────────────────────

    @commands.command(name="addcmd")
    @bot_permission_check()
    async def add_custom_cmd(self, ctx, name: str, *, text: str = ""):
        """Create a custom command that replies with text and/or an attached file.

        Usage (text only):    _addcmd rules Please read the rules!
        Usage (media only):   _addcmd meme   [attach image, no text needed]
        Usage (text + media): _addcmd welcome Hello! [attach image]
        """
        name = name.lower()

        if name in self.bot.all_commands:
            await ctx.send(f"❌  `_{name}` is a built-in command and cannot be overridden.")
            return

        media_url = None
        if ctx.message.attachments:
            media_url = ctx.message.attachments[0].url

        if not text and not media_url:
            await ctx.send("❌  Provide some text and/or attach a file.")
            return

        await db_save_custom_command(
            name, text or None, media_url,
            added_by=ctx.author.id,
            added_by_name=str(ctx.author),
        )

        embed = discord.Embed(title="✅  Custom Command Saved", color=discord.Color.green())
        embed.add_field(name="Command", value=f"`_{name}`", inline=True)
        embed.add_field(name="Added by", value=ctx.author.mention, inline=True)
        if text:
            embed.add_field(name="Text", value=text[:200], inline=False)
        if media_url:
            embed.add_field(name="Media", value="✅ Attachment saved", inline=True)
        embed.set_footer(text=f"Anyone can now use _{name} to trigger this response.")
        await ctx.send(embed=embed)

    @commands.command(name="removecmd")
    @bot_permission_check()
    async def remove_custom_cmd(self, ctx, name: str):
        """Delete a custom command.
        Usage: _removecmd rules
        """
        result = await db_delete_custom_command(name)
        if result == "DELETE 0":
            await ctx.send(f"⚠️  No custom command found with name `_{name}`.")
            return
        await ctx.send(f"🗑️  Custom command `_{name}` deleted.")

    @commands.command(name="listcmds")
    async def list_custom_cmds(self, ctx):
        """List all custom commands. Available to everyone."""
        rows = await db_get_all_custom_commands()
        if not rows:
            await ctx.send("📭  No custom commands yet. Admins can use `_addcmd` to add one.")
            return

        embed = discord.Embed(title="📋  Custom Commands", color=discord.Color.blurple())
        for row in rows:
            parts = []
            if row["text"]:
                parts.append("📝 Text")
            if row["media_url"]:
                parts.append("🖼️ Media")
            embed.add_field(
                name=f"`_{row['name']}`",
                value=f"{' + '.join(parts)}  •  used **{row['use_count']}×**",
                inline=True,
            )
        embed.set_footer(text="Type the command to use it! | Use _cmdinfo <name> for details.")
        await ctx.send(embed=embed)

    @commands.command(name="editcmd")
    @bot_permission_check()
    async def edit_custom_cmd(self, ctx, name: str, *, text: str = ""):
        """Edit an existing custom command. Attach a new file to replace media.
        Usage: _editcmd rules Updated rules text here  [optionally attach new image]
        """
        name = name.lower()
        existing = await db_get_custom_command(name)
        if not existing:
            await ctx.send(f"⚠️  No custom command `_{name}` found. Use `_addcmd` to create it.")
            return

        media_url = existing["media_url"]
        if ctx.message.attachments:
            media_url = ctx.message.attachments[0].url

        new_text = text or existing["text"]

        # Preserve original author on edit
        await db_save_custom_command(name, new_text, media_url)

        embed = discord.Embed(title="✏️  Custom Command Updated", color=discord.Color.blue())
        embed.add_field(name="Command", value=f"`_{name}`", inline=True)
        embed.add_field(name="Edited by", value=ctx.author.mention, inline=True)
        if new_text:
            embed.add_field(name="Text", value=new_text[:200], inline=False)
        if media_url:
            embed.add_field(name="Media", value="✅ Saved", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="cmdinfo")
    async def cmd_info(self, ctx, name: str):
        """Show who added a command and how many times it's been used.
        Usage: _cmdinfo rules
        """
        name = name.lower()
        row = await db_get_custom_command(name)
        if not row:
            await ctx.send(f"⚠️  No custom command `_{name}` found.")
            return

        embed = discord.Embed(
            title=f"ℹ️  Command Info: `_{name}`",
            color=discord.Color.gold(),
        )

        # Added by
        if row["added_by"]:
            member = ctx.guild.get_member(row["added_by"])
            added_by_display = member.mention if member else row["added_by_name"] or f"User ID {row['added_by']}"
        else:
            added_by_display = "Unknown (added before tracking)"
        embed.add_field(name="Added by", value=added_by_display, inline=True)

        embed.add_field(name="Times used", value=f"**{row['use_count']}×**", inline=True)

        parts = []
        if row["text"]:
            parts.append("📝 Text")
        if row["media_url"]:
            parts.append("🖼️ Media")
        embed.add_field(name="Content", value=" + ".join(parts) if parts else "—", inline=True)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(CustomCommandsCog(bot))
