"""Public Suggestions panel with named or genuinely anonymous staff submissions."""
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DESTINATION_CHANNEL_ID = int(
    os.getenv("SUGGESTIONS_DEST_CHANNEL_ID", "1554621739017707591")
)
ACCENT = 0x8C5A3C
COOLDOWN_SECONDS = 30


class SuggestionPost(discord.ui.LayoutView):
    def __init__(self, text, *, anonymous, user_id=None):
        super().__init__(timeout=None)
        safe = discord.utils.escape_markdown(text)
        if anonymous:
            source = "-# Anonymous submission"
        else:
            source = f"-# Submitted by <@{int(user_id)}>"

        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## Suggestion / Feedback\n"
                f"{safe}\n\n"
                f"{source}"
            ),
            accent_color=ACCENT,
        ))


class SuggestionModal(discord.ui.Modal):
    def __init__(self, cog, *, anonymous):
        super().__init__(
            title="Anonymous Feedback" if anonymous else "Feedback / Suggestion"
        )
        self.cog = cog
        self.anonymous = anonymous
        self.feedback = discord.ui.TextInput(
            label="Suggestion / feedback",
            placeholder="What would you like leadership to know?",
            style=discord.TextStyle.paragraph,
            required=True,
            min_length=3,
            max_length=2000,
        )
        self.add_item(self.feedback)

    async def on_submit(self, interaction):
        if interaction.guild_id != GUILD_ID:
            await interaction.response.send_message(
                "This is only available in Hyped.", ephemeral=True,
            )
            return

        now = time.monotonic()
        previous = self.cog.cooldowns.get(interaction.user.id, 0)
        if now - previous < COOLDOWN_SECONDS:
            remaining = max(1, int(COOLDOWN_SECONDS - (now - previous)))
            await interaction.response.send_message(
                f"Ogre need {remaining}s before another submission.",
                ephemeral=True,
            )
            return

        channel = self.cog.bot.get_channel(DESTINATION_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.cog.bot.fetch_channel(DESTINATION_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            await interaction.response.send_message(
                "Ogre cannot find the private feedback channel. Tell leadership.",
                ephemeral=True,
            )
            return

        text = " ".join(self.feedback.value.split()) if "\n" not in self.feedback.value else self.feedback.value.strip()
        try:
            await channel.send(
                view=SuggestionPost(
                    text,
                    anonymous=self.anonymous,
                    user_id=None if self.anonymous else interaction.user.id,
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Ogre couldn't deliver that feedback. Tell leadership.",
                ephemeral=True,
            )
            return

        self.cog.cooldowns[interaction.user.id] = now
        if self.anonymous:
            print("ANONYMOUS SUGGESTION SUBMITTED", flush=True)
            confirmation = (
                "✅ **Anonymous feedback sent.** "
                "Leadership won't see who submitted it."
            )
        else:
            print(
                f"NAMED SUGGESTION SUBMITTED user={interaction.user.id}",
                flush=True,
            )
            confirmation = "✅ **Feedback sent to leadership.** Thanks for taking the time."

        await interaction.response.send_message(confirmation, ephemeral=True)


class NamedSuggestionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Named",
            emoji="💬",
            style=discord.ButtonStyle.secondary,
            custom_id="hyped_suggestions_named_v1",
        )

    async def callback(self, interaction):
        await interaction.response.send_modal(
            SuggestionModal(self.view.cog, anonymous=False)
        )


class AnonymousSuggestionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Anonymous",
            emoji="🥷",
            style=discord.ButtonStyle.secondary,
            custom_id="hyped_suggestions_anonymous_v1",
        )

    async def callback(self, interaction):
        await interaction.response.send_modal(
            SuggestionModal(self.view.cog, anonymous=True)
        )


class SuggestionsPanel(discord.ui.LayoutView):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## Suggestions & Feedback\n"
                "Have an idea, suggestion or feedback for **Hyped**? "
                "Choose whether to send it with your name or anonymously.\n\n"
                "-# Submissions are sent privately to leadership."
            ),
            discord.ui.ActionRow(
                NamedSuggestionButton(),
                AnonymousSuggestionButton(),
            ),
            accent_color=ACCENT,
        ))


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class SuggestionsCog(
    commands.GroupCog,
    group_name="suggestions",
    group_description="Hyped feedback panel",
):
    def __init__(self, bot):
        self.bot = bot
        self.cooldowns = {}

    async def cog_load(self):
        self.bot.add_view(SuggestionsPanel(self))

    async def interaction_check(self, interaction):
        if (
            not OWNER_ID
            or interaction.user.id != OWNER_ID
            or interaction.guild_id != GUILD_ID
        ):
            await interaction.response.send_message(
                "Only the Hyped Ogre owner can configure suggestions.",
                ephemeral=True,
            )
            return False
        return True

    @app_commands.command(
        name="setup",
        description="Post the Suggestions & Feedback panel in this channel",
    )
    async def setup_panel(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use this in the text channel where the suggestions panel should live.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await interaction.channel.send(
                view=SuggestionsPanel(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Ogre couldn't post the suggestions panel here. Check channel permissions.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Suggestions panel posted: {message.jump_url}",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(SuggestionsCog(bot))
