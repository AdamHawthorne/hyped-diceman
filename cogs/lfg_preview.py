"""Read-only, guild-scoped preview of the planned Hyped LFG noticeboard.

All public names are fictional. The private editor keeps a draft in memory only.
No SQLite access, Discord roles, signups, or other bot systems are changed.
"""
import os

import discord
from discord import app_commands
from discord.ext import commands


# Fictional entries, intentionally sorted at render time rather than grouped.
SAMPLES = [
    ("Snore", 18, "Priest", "Healer", "Evening", "Group questing"),
    ("Grum", 26, "Hunter", "DPS", "All day", "SFK, BFD"),
    ("Rook", 34, "Shaman", "Healer", "After work", "Dungeons"),
    ("Kami", 36, "Warrior", "Tank", "On now", "Scarlet Monastery, group quests"),
    ("Mog", 54, "Rogue", "DPS", "Late night", "BRD, levelling"),
    ("Karg", 60, "Warrior", "Tank", "Evening", "Stratholme, Scholomance"),
]

CLASSES = (
    "Warrior", "Paladin", "Hunter", "Rogue", "Priest",
    "Shaman", "Mage", "Warlock", "Druid",
)
ROLES = ("Tank", "Healer", "DPS", "Flexible")
AVAILABILITY = ("On now", "All day", "After work", "Evening", "Late night", "Flexible")
ACTIVITIES = ("Dungeons", "Group questing", "Levelling", "PvP", "Farming", "Anything")


def board_embed():
    lines = []
    for name, level, cls, role, when, plans in sorted(
        SAMPLES, key=lambda entry: (-entry[1], entry[0].lower())
    ):
        lines.append(
            f"**Lv {level} · {discord.utils.escape_markdown(name)} · {cls} · {role}**\n"
            f"{when}  ·  {plans}"
        )
    embed = discord.Embed(
        title="HYPED | LFG-LFM  (PREVIEW)",
        description=(
            "Who is around, when are they free, and what do they want to do?\n"
            "One list, ordered by level.\n\n"
            + "\n\n".join(lines)
        ),
        color=discord.Color.from_rgb(217, 154, 67),
    )
    embed.set_footer(text="Fictional players · Editor is private · Nothing is saved")
    return embed


class ListingDraft:
    def __init__(self):
        self.character = ""
        self.level = 1
        self.cls = "Warrior"
        self.role = "DPS"
        self.availability = "Evening"
        self.activities = ["Dungeons"]
        self.notes = ""

    def summary(self):
        name = discord.utils.escape_markdown(self.character or "Your character")
        plan = ", ".join(self.activities)
        if self.notes:
            plan += " · " + discord.utils.escape_markdown(self.notes)
        return (
            f"**Lv {self.level} · {name} · {self.cls} · {self.role}**\n"
            f"{self.availability} · {plan}"
        )


def editor_embed(draft, message=None):
    embed = discord.Embed(
        title="Your LFG Listing | Private Preview",
        description=(
            "Choose your class, role, availability and activities below. "
            "Use **Character / Level / Notes** to enter text, then "
            "**Preview My Listing** to see the result.\n\n"
            + draft.summary()
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=message or "Only you can see this editor. Nothing is saved.")
    return embed


class CharacterModal(discord.ui.Modal, title="Character / Level / Notes"):
    character = discord.ui.TextInput(
        label="Character name", placeholder="e.g. Grum", max_length=32
    )
    level = discord.ui.TextInput(
        label="Character level (1-60)", placeholder="e.g. 26", max_length=2
    )
    notes = discord.ui.TextInput(
        label="Specific dungeon or notes (optional)", required=False,
        placeholder="e.g. SFK, BFD, after 7pm", max_length=100
    )

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.character.default = editor.draft.character
        self.level.default = str(editor.draft.level)
        self.notes.default = editor.draft.notes

    async def on_submit(self, interaction: discord.Interaction):
        name = self.character.value.strip()
        number = self.level.value.strip()
        if not name or not number.isdecimal() or not (1 <= int(number) <= 60):
            await interaction.response.send_message(
                "Please provide a character name and a whole-number level from 1 to 60.",
                ephemeral=True,
            )
            return
        self.editor.draft.character = name
        self.editor.draft.level = int(number)
        self.editor.draft.notes = self.notes.value.strip()
        await interaction.response.edit_message(
            embed=editor_embed(self.editor.draft, "Character details updated in your private draft."),
            view=self.editor,
        )


class DraftSelect(discord.ui.Select):
    def __init__(self, field, placeholder, options, max_values=1):
        self.field = field
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=max_values,
            row={"cls": 0, "role": 1, "availability": 2, "activities": 3}[field],
            options=[discord.SelectOption(label=option, value=option) for option in options],
        )

    async def callback(self, interaction: discord.Interaction):
        draft = self.view.draft
        setattr(draft, self.field, list(self.values) if self.field == "activities" else self.values[0])
        await interaction.response.edit_message(
            embed=editor_embed(draft, "Private draft updated. Nothing has been published."),
            view=self.view,
        )


class EditorView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.draft = ListingDraft()
        self.add_item(DraftSelect("cls", "Class", CLASSES))
        self.add_item(DraftSelect("role", "Role", ROLES))
        self.add_item(DraftSelect("availability", "Availability", AVAILABILITY))
        self.add_item(DraftSelect("activities", "What do you want to do? (choose up to 3)", ACTIVITIES, 3))

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This editor belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Character / Level / Notes", style=discord.ButtonStyle.secondary, row=4)
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CharacterModal(self))

    @discord.ui.button(label="Preview My Listing", style=discord.ButtonStyle.primary, row=4)
    async def show_preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.draft.character:
            await interaction.response.send_message(
                "Add your character name and level first.", ephemeral=True
            )
            return
        embed = discord.Embed(
            title="How you'd appear on the LFG board",
            description=self.draft.summary(),
            color=discord.Color.from_rgb(217, 154, 67),
        )
        embed.set_footer(text="Preview only · Not added to the public board or database")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BoardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=86400)

    @discord.ui.button(label="Add / Update Me", style=discord.ButtonStyle.primary)
    async def edit_listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        editor = EditorView(interaction.user.id)
        await interaction.response.send_message(
            embed=editor_embed(editor.draft), view=editor, ephemeral=True
        )

    @discord.ui.button(label="Remove Me", style=discord.ButtonStyle.secondary)
    async def remove_listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "In the real board, this will remove your own listing. "
            "This preview has no saved listings to remove.",
            ephemeral=True,
        )


class LFGPreview(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="lfg-preview", description="Preview the single-list Hyped LFG board and editor")
    @app_commands.guilds(discord.Object(id=int(os.environ["HYPED_GUILD_ID"])))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def lfg_preview(self, interaction: discord.Interaction):
        guild_id = int(os.getenv("HYPED_GUILD_ID", "0") or "0")
        if guild_id and interaction.guild_id != guild_id:
            await interaction.response.send_message(
                "This preview is limited to the Hyped server.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=board_embed(), view=BoardView())


async def setup(bot):
    await bot.add_cog(LFGPreview(bot))
