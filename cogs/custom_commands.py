"""
cogs/custom_commands.py — User-defined text/media commands with in-memory cache.
Cache is updated directly on add/edit/remove — no DB reload needed.
"""
import discord
from discord.ext import commands
from db import (
    db_get_all_custom_commands,
    db_save_custom_command, db_delete_custom_command,
    db_increment_use_count,
)
from permissions import bot_permission_check

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

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache: { name: row_dict }
        self._cache: dict[str, dict] = {}

    async def cog_load(self):
        await self._reload_cache()

    async def _reload_cache(self):
        rows = await db_get_all_custom_commands()
        self._cache = {row["name"]: dict(row) for row in rows}

    async def handle_message(self, message: discord.Message):
        if not message.content.startswith("_"):
            return

        cmd_name = message.content[1:].split()[0].lower()

        if cmd_name in self.bot.all_commands:
            return

        row = self._cache.get(cmd_name)
        if not row:
            return

        # Increment use counter in background — doesn't slow down response
        self.bot.loop.create_task(db_increment_use_count(cmd_name))
        self._cache[cmd_name]["use_count"] = row.get("use_count", 0) + 1

        text      = row["text"]
        media_url = row["media_url"]

        if text and media_url:
            await message.channel.send(content=text)
            await message.channel.send(media_url)
        elif text:
            await message.channel.send(content=text)
        elif media_url:
            await message.channel.send(media_url)

    @commands.command(name="addcmd")
    @bot_permission_check()
    async def add_custom_cmd(self, ctx, name: str, *, text: str = ""):
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

        # Update cache directly — no DB reload
        self._cache[name] = {
            "name": name,
            "text": text or None,
            "media_url": media_url,
            "added_by": ctx.author.id,
            "added_by_name": str(ctx.author),
            "use_count": 0,
        }

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
        name = name.lower()
        result = await db_delete_custom_command(name)
        if result == "DELETE 0":
            await ctx.send(f"⚠️  No custom command found with name `_{name}`.")
            return

        # Remove from cache directly
        self._cache.pop(name, None)
        await ctx.send(f"🗑️  Custom command `_{name}` deleted.")

    @commands.command(name="listcmds")
    async def list_custom_cmds(self, ctx):
        if not self._cache:
            await ctx.send("📭  No custom commands yet. Admins can use `_addcmd` to add one.")
            return

        embed = discord.Embed(title="📋  Custom Commands", color=discord.Color.blurple())
        for name, row in self._cache.items():
            parts = []
            if row["text"]:
                parts.append("📝 Text")
            if row["media_url"]:
                parts.append("🖼️ Media")
            embed.add_field(
                name=f"`_{name}`",
                value=f"{' + '.join(parts)}  •  used **{row.get('use_count', 0)}×**",
                inline=True,
            )
        embed.set_footer(text="Type the command to use it! | Use _cmdinfo <name> for details.")
        await ctx.send(embed=embed)

    @commands.command(name="editcmd")
    @bot_permission_check()
    async def edit_custom_cmd(self, ctx, name: str, *, text: str = ""):
        name = name.lower()
        existing = self._cache.get(name)
        if not existing:
            await ctx.send(f"⚠️  No custom command `_{name}` found. Use `_addcmd` to create it.")
            return

        media_url = existing["media_url"]
        if ctx.message.attachments:
            media_url = ctx.message.attachments[0].url

        new_text = text or existing["text"]
        await db_save_custom_command(name, new_text, media_url)

        # Update cache directly — no DB reload
        self._cache[name]["text"] = new_text
        self._cache[name]["media_url"] = media_url

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
        name = name.lower()
        row = self._cache.get(name)
        if not row:
            await ctx.send(f"⚠️  No custom command `_{name}` found.")
            return

        embed = discord.Embed(title=f"ℹ️  Command Info: `_{name}`", color=discord.Color.gold())
        if row.get("added_by"):
            member = ctx.guild.get_member(row["added_by"])
            added_by_display = member.mention if member else row.get("added_by_name") or f"User ID {row['added_by']}"
        else:
            added_by_display = "Unknown"
        embed.add_field(name="Added by", value=added_by_display, inline=True)
        embed.add_field(name="Times used", value=f"**{row.get('use_count', 0)}×**", inline=True)
        parts = []
        if row["text"]:
            parts.append("📝 Text")
        if row["media_url"]:
            parts.append("🖼️ Media")
        embed.add_field(name="Content", value=" + ".join(parts) if parts else "—", inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(CustomCommandsCog(bot))
