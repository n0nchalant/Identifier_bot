"""
permissions.py — Shared permission check used by all cogs.
"""
import discord
from discord.ext import commands
from db import db_get_allowed_roles


async def has_bot_permission(member: discord.Member) -> bool:
    """True if the member is an Administrator OR has an allowed role."""
    if member.guild_permissions.administrator:
        return True
    allowed = await db_get_allowed_roles()
    if not allowed:
        return False
    member_role_ids = [r.id for r in member.roles]
    return any(rid in allowed for rid in member_role_ids)


def bot_permission_check():
    """Decorator: pass if user is Administrator or has an allowed role."""
    async def predicate(ctx):
        if await has_bot_permission(ctx.author):
            return True
        raise commands.CheckFailure("no_permission")
    return commands.check(predicate)
