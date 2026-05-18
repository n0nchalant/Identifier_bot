"""
cogs/triggers.py — Message-count trigger commands.
Sends a custom message after every N messages in a channel (re-rolling randomly after the first hit).
"""
import random
import discord
from discord.ext import commands
from db import (
    db_get_all_triggers, db_get_trigger,
    db_add_trigger, db_remove_trigger,
    db_update_trigger, db_update_next_count,
)
from permissions import bot_permission_check

REROLL_MIN = 500
REROLL_MAX = 800


# ─────────────────────────────────────────────
#  Help definition (consumed by HelpCog)
# ─────────────────────────────────────────────
HELP_PAGE = {
    "title": "🤖 Bot Commands",
    "subtitle": "📖 Message Triggers",
    "fields": [
        ("_addtrigger #ch N phrase",  "🔑 Send phrase every N messages in a channel."),
        ("_removetrigger #ch",        "🔑 Remove a channel trigger."),
        ("_edittrigger #ch N phrase", "🔑 Edit an existing trigger."),
        ("_listtriggers",             "🔑 Show all triggers with progress."),
        ("_resetcounter #ch",         "🔑 Reset a channel's message counter."),
    ],
}


class TriggersCog(commands.Cog, name="Triggers"):
    """Send automatic messages after N messages in a channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-memory counters (reset on restart — acceptable)
        self.counters: dict[int, int] = {}
        self.next_counts: dict[int, int] = {}

    # ── Called from bot's on_ready to pre-load DB state ──
    async def load_from_db(self):
        rows = await db_get_all_triggers()
        for row in rows:
            cid = row["channel_id"]
            self.counters[cid] = 0
            self.next_counts[cid] = row["next_count"]
            channel = self.bot.get_channel(cid)
            cname = channel.name if channel else f"ID {cid}"
            print(f"📡  Watching  #{cname}  ─  next trigger in {row['next_count']} messages")

    # ── Called from bot's on_message ──────────
    async def handle_message(self, message: discord.Message):
        cid = message.channel.id
        trigger = await db_get_trigger(cid)
        if not trigger:
            return

        self.counters[cid] = self.counters.get(cid, 0) + 1
        target = self.next_counts.get(cid, trigger["next_count"])
        current = self.counters[cid]

        print(f"[#{message.channel.name}] {current}/{target}")

        if current >= target:
            self.counters[cid] = 0
            new_target = random.randint(REROLL_MIN, REROLL_MAX)
            self.next_counts[cid] = new_target
            await db_update_next_count(cid, new_target)
            print(f"🎲  Re-rolled #{message.channel.name}  ─  next in {new_target} messages")
            await message.channel.send(trigger["custom_message"])

    # ── Commands ──────────────────────────────

    @commands.command(name="addtrigger")
    @bot_permission_check()
    async def add_trigger(self, ctx, channel: discord.TextChannel, count: int, *, phrase: str):
        """Send a phrase every N messages in a channel.
        Usage: _addtrigger #channel 50 Hello everyone!
        """
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
        self.counters[channel.id] = 0
        self.next_counts[channel.id] = count

        embed = discord.Embed(title="✅  Trigger Added", color=discord.Color.green())
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="First trigger at", value=f"{count} messages", inline=True)
        embed.add_field(name="After that", value=f"Re-rolls {REROLL_MIN}–{REROLL_MAX} messages", inline=True)
        embed.add_field(name="Message", value=phrase, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="removetrigger")
    @bot_permission_check()
    async def remove_trigger(self, ctx, channel: discord.TextChannel):
        """Remove a channel trigger.
        Usage: _removetrigger #channel
        """
        result = await db_remove_trigger(channel.id)
        if result == "DELETE 0":
            await ctx.send(f"⚠️  No trigger found for {channel.mention}.")
            return
        self.counters.pop(channel.id, None)
        self.next_counts.pop(channel.id, None)
        await ctx.send(f"🗑️  Trigger for {channel.mention} removed.")

    @commands.command(name="edittrigger")
    @bot_permission_check()
    async def edit_trigger(self, ctx, channel: discord.TextChannel, count: int, *, phrase: str):
        """Edit an existing trigger.
        Usage: _edittrigger #channel 100 New message here!
        """
        existing = await db_get_trigger(channel.id)
        if not existing:
            await ctx.send(f"⚠️  No trigger for {channel.mention}. Use `_addtrigger` first.")
            return

        await db_update_trigger(channel.id, count, phrase)
        self.counters[channel.id] = 0
        self.next_counts[channel.id] = count

        embed = discord.Embed(title="✏️  Trigger Updated", color=discord.Color.blue())
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Next trigger at", value=f"{count} messages", inline=True)
        embed.add_field(name="After that", value=f"Re-rolls {REROLL_MIN}–{REROLL_MAX} messages", inline=True)
        embed.add_field(name="Message", value=phrase, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="listtriggers")
    @bot_permission_check()
    async def list_triggers(self, ctx):
        """List all active triggers."""
        rows = await db_get_all_triggers()
        if not rows:
            await ctx.send("📭  No triggers configured yet. Use `_addtrigger` to add one.")
            return

        embed = discord.Embed(title="📋  Active Triggers", color=discord.Color.blurple())
        for row in rows:
            channel = self.bot.get_channel(row["channel_id"])
            cname = channel.mention if channel else f"Unknown ({row['channel_id']})"
            current = self.counters.get(row["channel_id"], 0)
            nxt = self.next_counts.get(row["channel_id"], row["next_count"])
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

    @commands.command(name="resetcounter")
    @bot_permission_check()
    async def reset_counter(self, ctx, channel: discord.TextChannel):
        """Reset a channel's message counter.
        Usage: _resetcounter #channel
        """
        self.counters[channel.id] = 0
        new_target = random.randint(REROLL_MIN, REROLL_MAX)
        self.next_counts[channel.id] = new_target
        await db_update_next_count(channel.id, new_target)
        await ctx.send(f"🔄  Counter for {channel.mention} reset. Next trigger in **{new_target}** messages.")


async def setup(bot: commands.Bot):
    await bot.add_cog(TriggersCog(bot))
