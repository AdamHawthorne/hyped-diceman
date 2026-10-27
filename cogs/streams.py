"""Preview-only Twitch submission panel. No registrations or public posts yet."""
import os
import discord
from discord import app_commands
from discord.ext import commands

GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PREVIEW_CHANNEL_ID = 1564594901746716713
PUBLIC_CHANNEL_ID = 1562121249176424619
OGRE_STREAM_ICON = "https://i.vgy.me/Rqn4hP.png"
ACCENT = 0x8C5A3C


class TwitchPanel(discord.ui.LayoutView):
    def __init__(self, *, preview=True):
        super().__init__(timeout=300 if preview else None)
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "## Live streams:\n"
                    "Give Ogre stream name. Me shout when live."
                ),
                accessory=discord.ui.Thumbnail(
                    OGRE_STREAM_ICON, description="Hyped Ogre livestream icon",
                ),
            ),
            discord.ui.TextDisplay(
                "### List of streams:\n"
                "-# No Twitch streams registered yet."
            ),
            accent_color=ACCENT,
        ))
        # ActionRow is outside the Components V2 container, as with Suggestions.
        self.add_item(discord.ui.ActionRow(discord.ui.Button(
            label="Twitch",
            style=discord.ButtonStyle.secondary,
            custom_id="hyped_streams_twitch_v1",
            disabled=True,  # Preview only: no silent/nonfunctional registrations.
        )))


class TwitchLivePreview(discord.ui.LayoutView):
    """Sample live notification; no Twitch calls or public posting."""
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## 🔴 Ogre spot live stream!\\n"
                "**ExampleStreamer** is live on Twitch!\\n\\n"
                "**Playing:** World of Warcraft\\n"
                "**Stream:** Hyped guild adventures\\n\\n"
                "Me smell adventure. Go watch!"
            ),
            accent_color=0x9146FF,
        ))
        self.add_item(discord.ui.ActionRow(discord.ui.Button(
            label="Watch stream",
            style=discord.ButtonStyle.link,
            url="https://www.twitch.tv/example",
        )))


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class StreamsCog(commands.GroupCog, group_name="streams",
                 group_description="Hyped streams panel"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="preview", description="Preview the Twitch panel in private #ogre")
    async def preview(self, interaction: discord.Interaction):
        # Acknowledge immediately so Discord never times out during rendering.
        await interaction.response.defer(ephemeral=True, thinking=True)
        if (interaction.user.id != OWNER_ID or interaction.guild_id != GUILD_ID
                or interaction.channel_id != PREVIEW_CHANNEL_ID):
            await interaction.followup.send(
                "Ogre only show this preview to owner in private #ogre.",
                ephemeral=True,
            )
            return
        try:
            panel = TwitchPanel(preview=True)
            await interaction.followup.send(
                view=panel,
                ephemeral=True,
            )
        except Exception:
            import logging
            logging.exception("STREAMS PREVIEW FAILED")
            await interaction.followup.send(
                "Ogre couldn't draw stream panel. Check bot logs.",
                ephemeral=True,
            )


    @app_commands.command(name="previewlive", description="Privately preview a Twitch go-live announcement")
    async def preview_live(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if (interaction.user.id != OWNER_ID or interaction.guild_id != GUILD_ID
                or interaction.channel_id != PREVIEW_CHANNEL_ID):
            await interaction.followup.send(
                "Ogre only show this preview to owner in private #ogre.",
                ephemeral=True,
            )
            return
        try:
            await interaction.followup.send(
                view=TwitchLivePreview(),
                ephemeral=True,
            )
        except Exception:
            import logging
            logging.exception("STREAMS LIVE PREVIEW FAILED")
            await interaction.followup.send(
                "Ogre couldn't draw live alert. Check bot logs.",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(StreamsCog(bot))
