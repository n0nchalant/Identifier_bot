"""
cogs/triggers.py — Message-count trigger commands with in-memory cache.
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

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.counters: dict[int, int] = {}
        self.next_counts: dict[int, int] = {}
        # Cache: { channel_id: trigger_row_dict }
        self._cache: dict[int, dict] = {}

    async def load_from_db(self):
        rows = await db_get_all_triggers()
        self._cache.clear()
        for row in rows:
            cid = row["channel_id"]
            self._cache[cid] = dict(row)
            self.counters[cid] = 0
            self.next_counts[cid] = row["next_count"]
            channel = self.bot.get_channel(cid)
            cname = channel.name if channel else f"ID {cid}"
            print(f"📡  Watching  #{cname}  ─  next trigger in {row['next_count']} messages")

    async def handle_message(self, message: discord.Message):
        cid = message.channel.id
        trigger = self._cache.get(cid)
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
            self._cache[cid]["next_count"] = new_target
            print(f"🎲  Re-rolled #{message.channel.name}  ─  next in {new_target} messages")
            await message.channel.send(trigger["custom_message"])

    @commands.command(name="addtrigger")
    @bot_permission_check()
    async def add_trigger(self, ctx, channel: discord.TextChannel, count: int, *, phrase: str):
        existing = self._cache.get(channel.id)
        if existing:
            await ctx.send(f"⚠️  A trigger for {channel.mention} already exists. Remove it first with `_removetrigger`.")
            return
        if count < 1:
            await ctx.send("❌  Message count must be at least 1.")
            return

        await db_add_trigger(channel.id, count, phrase)
        self.counters[channel.id] = 0
        self.next_counts[channel.id] = count
        self._cache[channel.id] = {
            "channel_id": channel.id,
            "message_count": count,
            "custom_message": phrase,
            "next_count": count,
        }

        embed = discord.Embed(title="✅  Trigger Added", color=discord.Color.green())
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="First trigger at", value=f"{count} messages", inline=True)
        embed.add_field(name="After that", value=f"Re-rolls {REROLL_MIN}–{REROLL_MAX} messages", inline=True)
        embed.add_field(name="Message", value=phrase, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="removetrigger")
    @bot_permission_check()
    async def remove_trigger(self, ctx, channel: discord.TextChannel):
        result = await db_remove_trigger(channel.id)
        if result == "DELETE 0":
            await ctx.send(f"⚠️  No trigger found for {channel.mention}.")
            return
        self.counters.pop(channel.id, None)
        self.next_counts.pop(channel.id, None)
        self._cache.pop(channel.id, None)
        await ctx.send(f"🗑️  Trigger for {channel.mention} removed.")

    @commands.command(name="edittrigger")
    @bot_permission_check()
    async def edit_trigger(self, ctx, channel: discord.TextChannel, count: int, *, phrase: str):
        if not self._cache.get(channel.id):
            await ctx.send(f"⚠️  No trigger for {channel.mention}. Use `_addtrigger` first.")
            return

        await db_update_trigger(channel.id, count, phrase)
        self.counters[channel.id] = 0
        self.next_counts[channel.id] = count
        self._cache[channel.id] = {
            "channel_id": channel.id,
            "message_count": count,
            "custom_message": phrase,
            "next_count": count,
        }

        embed = discord.Embed(title="✏️  Trigger Updated", color=discord.Color.blue())
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Next trigger at", value=f"{count} messages", inline=True)
        embed.add_field(name="After that", value=f"Re-rolls {REROLL_MIN}–{REROLL_MAX} messages", inline=True)
        embed.add_field(name="Message", value=phrase, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="listtriggers")
    @bot_permission_check()
    async def list_triggers(self, ctx):
        if not self._cache:
            await ctx.send("📭  No triggers configured yet. Use `_addtrigger` to add one.")
            return

        embed = discord.Embed(title="📋  Active Triggers", color=discord.Color.blurple())
        for cid, trigger in self._cache.items():
            channel = self.bot.get_channel(cid)
            cname = channel.mention if channel else f"Unknown ({cid})"
            current = self.counters.get(cid, 0)
            nxt = self.next_counts.get(cid, trigger["next_count"])
            embed.add_field(
                name=cname,
                value=(
                    f"**Next trigger at:** {nxt} messages\n"
                    f"**Progress:** {current}/{nxt}\n"
                    f"**Re-roll range:** {REROLL_MIN}–{REROLL_MAX} messages\n"
                    f"**Message:** {trigger['custom_message']}"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="resetcounter")
    @bot_permission_check()
    async def reset_counter(self, ctx, channel: discord.TextChannel):
        self.counters[channel.id] = 0
        new_target = random.randint(REROLL_MIN, REROLL_MAX)
        self.next_counts[channel.id] = new_target
        await db_update_next_count(channel.id, new_target)
        if channel.id in self._cache:
            self._cache[channel.id]["next_count"] = new_target
        await ctx.send(f"🔄  Counter for {channel.mention} reset. Next trigger in **{new_target}** messages.")


async def setup(bot: commands.Bot):
    await bot.add_cog(TriggersCog(bot))
