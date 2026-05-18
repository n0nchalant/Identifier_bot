"""
cogs/roles.py — Role management commands.
Administrators can grant/revoke roles that are allowed to use bot commands.
"""
import discord
from discord.ext import commands
from db import db_get_allowed_roles, db_add_allowed_role, db_remove_allowed_role


# ─────────────────────────────────────────────
#  Help definition (consumed by HelpCog)
# ─────────────────────────────────────────────
HELP_PAGE = {
    "title": "🤖 Bot Commands",
    "subtitle": "📖 Role Management",
    "fields": [
        ("_addrole @role",    "🔒 Allow a role to use bot commands."),
        ("_removerole @role", "🔒 Remove a role from bot access."),
        ("_listroles",        "🔒 List all allowed roles."),
    ],
}


class RolesCog(commands.Cog, name="Roles"):
    """Manage which roles can use bot commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── _addrole ──────────────────────────────
    @commands.command(name="addrole")
    @commands.has_permissions(administrator=True)
    async def add_allowed_role(self, ctx, role: discord.Role):
        """Allow a role to use bot commands.
        Usage: _addrole @Moderator
        """
        await db_add_allowed_role(role.id)
        embed = discord.Embed(title="✅  Role Added", color=discord.Color.green())
        embed.add_field(name="Role", value=role.mention, inline=True)
        embed.set_footer(text="Members with this role can now use bot commands.")
        await ctx.send(embed=embed)

    # ── _removerole ───────────────────────────
    @commands.command(name="removerole")
    @commands.has_permissions(administrator=True)
    async def remove_allowed_role(self, ctx, role: discord.Role):
        """Remove a role from bot command access.
        Usage: _removerole @Moderator
        """
        result = await db_remove_allowed_role(role.id)
        if result == "DELETE 0":
            await ctx.send(f"⚠️  {role.mention} is not in the allowed roles list.")
            return
        await ctx.send(f"🗑️  {role.mention} removed from allowed roles.")

    # ── _listroles ────────────────────────────
    @commands.command(name="listroles")
    @commands.has_permissions(administrator=True)
    async def list_allowed_roles(self, ctx):
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


async def setup(bot: commands.Bot):
    await bot.add_cog(RolesCog(bot))
