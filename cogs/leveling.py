"""
cogs/leveling.py — XP-based leveling system with automatic role assignment.

Rules:
  - Each message earns 15–25 XP with a 60-second cooldown per user
  - Levels are defined by XP thresholds (100 * level^2)
  - Admins map levels to roles via _addlevelrole
  - Only one level role is held at a time — old role removed on level up
  - No pings on level up or role assignment
  - If a user has a level role higher than their earned level (manually assigned),
    they still earn XP but the bot won't touch their roles until they catch up
"""
import random
import time
import discord
from discord.ext import commands
from db import get_db
from permissions import bot_permission_check


HELP_PAGE = {
    "title": "🤖 Bot Commands",
    "subtitle": "📖 Leveling",
    "fields": [
        ("_rank [user]",           "🌐 Show your (or another user's) level and XP."),
        ("_leaderboard",           "🌐 Top 10 users by XP."),
        ("_addlevelrole N @role",  "🔑 Assign a role at level N."),
        ("_removelevelrole N",     "🔑 Remove the role assigned at level N."),
        ("_listlevelroles",        "🔑 List all level → role mappings."),
        ("_setxp @user N",         "🔒 Manually set a user's XP."),
        ("_resetxp @user",         "🔒 Reset a user's XP to 0."),
    ],
}

XP_MIN     = 15
XP_MAX     = 25
XP_COOLDOWN = 60  # seconds


def xp_for_level(level: int) -> int:
    """Total XP required to reach this level."""
    return 100 * (level ** 2)


def level_from_xp(xp: int) -> int:
    """Current level based on total XP."""
    level = 0
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


# ─────────────────────────────────────────────
#  DB helpers
# ─────────────────────────────────────────────
async def setup_leveling_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_xp (
            guild_id  BIGINT NOT NULL,
            user_id   BIGINT NOT NULL,
            xp        INT    NOT NULL DEFAULT 0,
            username  TEXT,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    await conn.execute("""
        ALTER TABLE user_xp ADD COLUMN IF NOT EXISTS username TEXT
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS level_roles (
            guild_id  BIGINT NOT NULL,
            level     INT    NOT NULL,
            role_id   BIGINT NOT NULL,
            PRIMARY KEY (guild_id, level)
        )
    """)
    await conn.close()


async def db_get_xp(guild_id: int, user_id: int) -> int:
    conn = await get_db()
    row = await conn.fetchrow(
        "SELECT xp FROM user_xp WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id
    )
    await conn.close()
    return row["xp"] if row else 0


async def db_add_xp(guild_id: int, user_id: int, amount: int, username: str = None) -> int:
    """Add XP and return new total."""
    conn = await get_db()
    row = await conn.fetchrow("""
        INSERT INTO user_xp (guild_id, user_id, xp, username)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id, user_id) DO UPDATE
            SET xp = user_xp.xp + $3,
                username = COALESCE($4, user_xp.username)
        RETURNING xp
    """, guild_id, user_id, amount, username)
    await conn.close()
    return row["xp"]


async def db_set_xp(guild_id: int, user_id: int, amount: int):
    conn = await get_db()
    await conn.execute("""
        INSERT INTO user_xp (guild_id, user_id, xp)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = $3
    """, guild_id, user_id, amount)
    await conn.close()


async def db_get_leaderboard(guild_id: int, limit: int = 100):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT user_id, xp, username FROM user_xp WHERE guild_id = $1 ORDER BY xp DESC LIMIT $2",
        guild_id, limit
    )
    await conn.close()
    return rows


async def db_get_all_level_roles(guild_id: int):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT level, role_id FROM level_roles WHERE guild_id = $1 ORDER BY level",
        guild_id
    )
    await conn.close()
    return rows


async def db_add_level_role(guild_id: int, level: int, role_id: int):
    conn = await get_db()
    await conn.execute("""
        INSERT INTO level_roles (guild_id, level, role_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id, level) DO UPDATE SET role_id = $3
    """, guild_id, level, role_id)
    await conn.close()


async def db_remove_level_role(guild_id: int, level: int):
    conn = await get_db()
    result = await conn.execute(
        "DELETE FROM level_roles WHERE guild_id = $1 AND level = $2",
        guild_id, level
    )
    await conn.close()
    return result


# ─────────────────────────────────────────────
#  Cog
# ─────────────────────────────────────────────
class LevelingCog(commands.Cog, name="Leveling"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cooldown tracker: { (guild_id, user_id): last_xp_timestamp }
        self._cooldowns: dict[tuple, float] = {}
        # Level roles cache: { guild_id: { level: role_id } }
        self._level_roles: dict[int, dict[int, int]] = {}
        # XP cache: { (guild_id, user_id): xp }
        self._xp_cache: dict[tuple, int] = {}
        # Username cache: { (guild_id, user_id): username }
        self._username_cache: dict[tuple, str] = {}

    async def cog_load(self):
        await setup_leveling_db()
        await self._reload_level_roles()
        await self._load_xp_cache()

    async def _load_xp_cache(self):
        """Load all XP data into memory on startup."""
        conn = await get_db()
        rows = await conn.fetch("SELECT guild_id, user_id, xp, username FROM user_xp")
        await conn.close()
        self._xp_cache.clear()
        self._username_cache.clear()
        for row in rows:
            key = (row["guild_id"], row["user_id"])
            self._xp_cache[key] = row["xp"]
            if row["username"]:
                self._username_cache[key] = row["username"]
        print(f"📊  Loaded {len(self._xp_cache)} XP records into cache")

    async def _reload_level_roles(self):
        """Load level roles for all guilds into cache."""
        self._level_roles.clear()
        for guild in self.bot.guilds:
            rows = await db_get_all_level_roles(guild.id)
            self._level_roles[guild.id] = {row["level"]: row["role_id"] for row in rows}

    def _get_level_roles(self, guild_id: int) -> dict[int, int]:
        return self._level_roles.get(guild_id, {})

    async def _handle_level_up(self, member: discord.Member, old_level: int, new_level: int):
        """Assign new level role, remove old one. Skips if user has a higher role manually."""
        level_roles = self._get_level_roles(member.guild.id)
        if not level_roles:
            return

        # Find all level role IDs for this guild
        all_level_role_ids = set(level_roles.values())

        # Find the highest level role the user currently has
        member_role_ids = {r.id for r in member.roles}
        held_level_role_levels = [
            lvl for lvl, rid in level_roles.items() if rid in member_role_ids
        ]
        highest_held = max(held_level_role_levels) if held_level_role_levels else 0

        # Option 2: if user has a manually assigned higher role, skip role changes
        # but still allow XP to accumulate until they catch up
        if highest_held > new_level:
            return

        # Find the role to assign for new_level (highest defined role <= new_level)
        eligible = {lvl: rid for lvl, rid in level_roles.items() if lvl <= new_level}
        if not eligible:
            return

        target_level = max(eligible.keys())
        target_role_id = eligible[target_level]
        target_role = member.guild.get_role(target_role_id)
        if not target_role:
            return

        # Remove all other level roles
        roles_to_remove = [
            member.guild.get_role(rid)
            for lvl, rid in level_roles.items()
            if rid in member_role_ids and rid != target_role_id
        ]
        roles_to_remove = [r for r in roles_to_remove if r]

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Level up — removing old level role")
            if target_role not in member.roles:
                await member.add_roles(target_role, reason=f"Reached level {target_level}")
        except discord.Forbidden:
            print(f"⚠️  Missing permissions to manage roles for {member}")
            return

        # Send level up message without pinging
        # Find a suitable channel — system channel or first text channel
        channel = member.guild.system_channel
        if not channel:
            channel = next((c for c in member.guild.text_channels if c.permissions_for(member.guild.me).send_messages), None)
        if channel:
            await channel.send(
                f"Level up! {member.display_name} reached level {new_level} and was awarded the {target_role.name} role.",
                allowed_mentions=discord.AllowedMentions.none()
            )

    async def handle_message(self, message: discord.Message):
        if not message.guild:
            return

        key = (message.guild.id, message.author.id)
        now = time.time()

        # Check cooldown
        last = self._cooldowns.get(key, 0)
        if now - last < XP_COOLDOWN:
            return

        self._cooldowns[key] = now
        xp_gain = random.randint(XP_MIN, XP_MAX)
        key = (message.guild.id, message.author.id)

        old_xp = self._xp_cache.get(key, 0)
        new_xp = old_xp + xp_gain

        # Update cache instantly
        self._xp_cache[key] = new_xp
        self._username_cache[key] = message.author.display_name

        # Update DB in background — doesn't slow down response
        self.bot.loop.create_task(
            db_add_xp(message.guild.id, message.author.id, xp_gain, message.author.display_name)
        )

        old_level = level_from_xp(old_xp)
        new_level = level_from_xp(new_xp)

        if new_level > old_level:
            await self._handle_level_up(message.author, old_level, new_level)

    # ── Commands ──────────────────────────────

    @commands.command(name="rank")
    async def rank(self, ctx, member: discord.Member = None):
        """Show your current level and XP.
        Usage: _rank  or  _rank @user
        """
        member = member or ctx.author
        xp = self._xp_cache.get((ctx.guild.id, member.id), 0)
        level = level_from_xp(xp)
        current_threshold = xp_for_level(level)
        next_threshold = xp_for_level(level + 1)
        progress = xp - current_threshold
        needed = next_threshold - current_threshold

        # Progress bar
        filled = int((progress / needed) * 20)
        bar = "█" * filled + "░" * (20 - filled)

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="XP", value=f"{xp:,}", inline=True)
        embed.add_field(name="Progress", value=f"`{bar}` {progress}/{needed} XP to level {level + 1}", inline=False)

        # Show current level role if any
        level_roles = self._get_level_roles(ctx.guild.id)
        eligible = {lvl: rid for lvl, rid in level_roles.items() if lvl <= level}
        if eligible:
            role = ctx.guild.get_role(eligible[max(eligible.keys())])
            if role:
                embed.add_field(name="Role", value=role.mention, inline=True)

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="leaderboard")
    async def leaderboard(self, ctx):
        """Show the leaderboard with pagination."""
        rows = await db_get_leaderboard(ctx.guild.id, limit=100)
        if not rows:
            await ctx.send("No XP data yet. Start chatting!")
            return

        # Build entries list
        entries = []
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows):
            # Prefer live display name, fall back to stored username
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else (row["username"] or "Unknown User")
            level = level_from_xp(row["xp"])
            prefix = medals[i] if i < 3 else f"{i + 1}."
            entries.append(f"{prefix} **{name}** — Level {level} ({row['xp']:,} XP)")

        view = LeaderboardView(entries)
        await ctx.send(embed=view.build_embed(), view=view, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="addlevelrole")
    @bot_permission_check()
    async def add_level_role(self, ctx, level: int, role: discord.Role):
        """Assign a role when a user reaches a level.
        Usage: _addlevelrole 5 @Role
        """
        if level < 1:
            await ctx.send("Level must be at least 1.")
            return

        await db_add_level_role(ctx.guild.id, level, role.id)
        self._level_roles.setdefault(ctx.guild.id, {})[level] = role.id

        embed = discord.Embed(title="Level Role Added", color=discord.Color.green())
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Role", value=role.mention, inline=True)
        embed.set_footer(text="Users who reach this level will automatically receive this role.")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="removelevelrole")
    @bot_permission_check()
    async def remove_level_role(self, ctx, level: int):
        """Remove the role assigned at a level.
        Usage: _removelevelrole 5
        """
        result = await db_remove_level_role(ctx.guild.id, level)
        if result == "DELETE 0":
            await ctx.send(f"No role assigned at level {level}.")
            return

        self._level_roles.get(ctx.guild.id, {}).pop(level, None)
        await ctx.send(f"Level role for level {level} removed.")

    @commands.command(name="listlevelroles")
    @bot_permission_check()
    async def list_level_roles(self, ctx):
        """List all level → role mappings."""
        level_roles = self._get_level_roles(ctx.guild.id)
        if not level_roles:
            await ctx.send("No level roles set. Use _addlevelrole to add one.")
            return

        embed = discord.Embed(title="Level Roles", color=discord.Color.blurple())
        for level in sorted(level_roles.keys()):
            role = ctx.guild.get_role(level_roles[level])
            rname = role.mention if role else f"Unknown role ({level_roles[level]})"
            xp_needed = xp_for_level(level)
            embed.add_field(
                name=f"Level {level}",
                value=f"{rname}\n{xp_needed:,} XP needed",
                inline=True
            )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="setxp")
    @commands.has_permissions(administrator=True)
    async def set_xp(self, ctx, member: discord.Member, amount: int):
        """Manually set a user's XP.
        Usage: _setxp @user 500
        """
        if amount < 0:
            await ctx.send("XP cannot be negative.")
            return
        await db_set_xp(ctx.guild.id, member.id, amount)
        level = level_from_xp(amount)
        await ctx.send(
            f"Set {member.display_name}'s XP to {amount:,} (Level {level}).",
            allowed_mentions=discord.AllowedMentions.none()
        )

    @commands.command(name="resetxp")
    @commands.has_permissions(administrator=True)
    async def reset_xp(self, ctx, member: discord.Member):
        """Reset a user's XP to 0.
        Usage: _resetxp @user
        """
        await db_set_xp(ctx.guild.id, member.id, 0)
        await ctx.send(
            f"Reset {member.display_name}'s XP to 0.",
            allowed_mentions=discord.AllowedMentions.none()
        )



class LeaderboardView(discord.ui.View):
    PAGE_SIZE = 10

    def __init__(self, entries: list[str]):
        super().__init__(timeout=120)
        self.entries = entries
        self.page = 0
        self.total_pages = max(1, (len(entries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self._update_buttons()

    def _update_buttons(self):
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> discord.Embed:
        start = self.page * self.PAGE_SIZE
        chunk = self.entries[start:start + self.PAGE_SIZE]
        embed = discord.Embed(
            title="Leaderboard",
            description="\n".join(chunk),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages}  |  {len(self.entries)} users total")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


async def setup(bot: commands.Bot):
    await bot.add_cog(LevelingCog(bot))
