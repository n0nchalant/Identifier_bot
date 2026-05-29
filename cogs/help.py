"""
cogs/help.py — Paginated help command.
Automatically pulls HELP_PAGE dicts from every loaded cog that defines one.
"""
import discord
from discord.ext import commands
import importlib


def _collect_help_pages(bot: commands.Bot) -> list[dict]:
    pages = []
    for cog in bot.cogs.values():
        module = cog.__class__.__module__
        try:
            mod = importlib.import_module(module)
            if hasattr(mod, "HELP_PAGE"):
                pages.append(mod.HELP_PAGE)
        except Exception:
            pass
    return pages


def build_help_embed(pages: list[dict], page: int) -> discord.Embed:
    data = pages[page]
    embed = discord.Embed(
        title=data["title"],
        description=(
            "🔒 = Admin only   |   "
            "🔑 = Admin or allowed role   |   "
            "🌐 = Everyone"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="\u200b",
        value=f"**── {data['subtitle']} ──**",
        inline=False,
    )
    for name, desc in data["fields"]:
        embed.add_field(name=f"`{name}`", value=desc, inline=False)
    embed.set_footer(text=f"Page {page + 1}/{len(pages)}")
    return embed


class HelpView(discord.ui.View):
    def __init__(self, pages: list[dict]):
        super().__init__(timeout=120)
        self.page = 0
        self.pages = pages
        self.prev_button.disabled = True
        self.next_button.disabled = len(pages) <= 1

    def _update_buttons(self):
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= len(self.pages) - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=build_help_embed(self.pages, self.page),
            view=self,
        )

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=build_help_embed(self.pages, self.page),
            view=self,
        )


class HelpCog(commands.Cog, name="Help"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help")
    async def show_help(self, ctx):
        pages = _collect_help_pages(self.bot)
        if not pages:
            await ctx.send("No help pages registered yet.")
            return
        view = HelpView(pages)
        await ctx.send(embed=build_help_embed(pages, 0), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
