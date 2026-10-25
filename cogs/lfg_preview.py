"""Guild-only, opt-in LFG board preview.

No live signups, roster writes or automatic messages. All data below is fictional.
"""
import os
import discord
from discord import app_commands
from discord.ext import commands

SAMPLES = {
    "10-19": [("Pip", 16, "Hunter", "DPS", "All day", "Levelling, questing")],
    "20-29": [("Mog", 22, "Paladin", "Tank", "After work", "Dungeons or quests")],
    "30-39": [
        ("Grum", 34, "Warrior", "DPS", "Evening", "Dungeons or group quests"),
        ("Kami", 36, "Priest", "Healer", "Evening", "Dungeon runs"),
        ("Kev", 35, "Mage", "DPS", "Evening", "Questing or dungeons"),
    ],
    "40-49": [("Rook", 44, "Druid", "Flexible", "Late", "Questing")],
    "50-59": [],
    "60": [
        ("Karg", 60, "Warrior", "Tank", "Evening", "Stratholme / Scholomance"),
        ("Nessa", 60, "Mage", "DPS", "Evening", "BRD / LBRS"),
    ],
}


def home_embed():
    lines = []
    for bracket, players in SAMPLES.items():
        if players:
            overview = " · ".join(p[0] for p in players[:4])
            lines.append(f"**{bracket}**  •  {len(players)} looking  •  {overview}")
        else:
            lines.append(f"**{bracket}**  •  No plans yet")
    return discord.Embed(
        title="HYPED | DAILY LFG  (PREVIEW)",
        description=(
            "OGRE SEE WHO WANT ZUG. Pick a level range to see plans.\n\n"
            + "\n".join(lines)
            + "\n\n*Fictional preview data. No real groups or commitments.*"
        ),
        color=discord.Color.dark_green(),
    )


def detail_embed(bracket):
    players = SAMPLES[bracket]
    embed = discord.Embed(
        title=f"LFG | Levels {bracket}",
        description="Choose a player intention below. This is a non-persistent preview.",
        color=discord.Color.green(),
    )
    if not players:
        embed.description = "Nobody listed yet. Preview data only."
    for name, lvl, cls, role, when, plan in players:
        embed.add_field(
            name=f"{name}  •  {cls} {lvl}  •  {role}",
            value=f"**When:** {when}\n**Wants:** {plan}",
            inline=False,
        )
    embed.set_footer(text="Hyped Ogre preview · Interest/commit are simulated")
    return embed


class LevelSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="View a level bracket",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=f"Levels {key}", value=key,
                    description=f"{len(value)} sample players")
                for key, value in SAMPLES.items()
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        bracket = self.values[0]
        await interaction.response.send_message(
            embed=detail_embed(bracket), view=PlanView(bracket), ephemeral=True
        )


class BoardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=86400)
        self.add_item(LevelSelect())


class PlayerSelect(discord.ui.Select):
    def __init__(self, bracket):
        self.bracket = bracket
        super().__init__(
            placeholder="Choose a plan",
            options=[
                discord.SelectOption(label=f"{p[0]} ({p[2]} {p[1]})", value=p[0],
                    description=p[5][:90])
                for p in SAMPLES[bracket]
            ] if SAMPLES[bracket] else
            [discord.SelectOption(label="No players", value="none")],
            disabled=not bool(SAMPLES[bracket])
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.selected = self.values[0]
        await interaction.response.send_message(
            f"Selected **{self.values[0]}**. Use the Interest / Commit buttons "
            "on the same preview panel. Nothing is saved.", ephemeral=True
        )


class PlanView(discord.ui.View):
    def __init__(self, bracket):
        super().__init__(timeout=900)
        self.selected = None
        self.add_item(PlayerSelect(bracket))

    async def interaction_check(self, interaction):
        # This panel is ephemeral; Discord restricts visibility to its original user.
        return True

    @discord.ui.button(label="Interested", style=discord.ButtonStyle.primary)
    async def interested(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.respond(interaction, "Interested")

    @discord.ui.button(label="Commit", style=discord.ButtonStyle.success)
    async def commit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.respond(interaction, "Committed")

    async def respond(self, interaction, status):
        if not self.selected:
            await interaction.response.send_message(
                "Choose a character from the dropdown first.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"**Preview:** You marked **{status.lower()}** for "
            f"**{self.selected}**. This is only a demonstration; "
            "no real signup or group was changed.", ephemeral=True
        )


class LFGPreview(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="lfg-preview", description="See a fictional Hyped LFG board")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def lfg_preview(self, interaction: discord.Interaction):
        guild_id = int(os.getenv("HYPED_GUILD_ID", "0") or "0")
        if guild_id and interaction.guild_id != guild_id:
            await interaction.response.send_message(
                "This preview is limited to the Hyped server.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=home_embed(), view=BoardView())


async def setup(bot):
    await bot.add_cog(LFGPreview(bot))
