"""Forever opt-in role panel and leadership application preview."""
import discord

ACCENT = 0x8C5A3C
FOREVER_THUMBNAIL = "https://i.vgy.me/asI88C.png"
FOREVER_IMAGE = "https://i.vgy.me/WGNGdr.png"
LEADERSHIP_APPLICATIONS_CHANNEL_ID = 1225020051418255370

LEADERSHIP_INTERESTS = (
    "Leading",
    "Prep",
    "Logs",
    "Recruitment",
    "Loot",
    "Boosting",
    "Coding",
    "Mentoring",
    "Admin",
    "Anything",
    "Other",
)


async def toggle_forever_role(interaction, guild_id, role_id, log_event):
    guild = interaction.guild

    if guild is None or guild.id != guild_id:
        await interaction.response.send_message(
            "This role button is only available in Hyped.",
            ephemeral=True,
        )
        return

    role = guild.get_role(role_id)

    if role is None:
        await interaction.response.send_message(
            "The Forever role is not configured correctly.",
            ephemeral=True,
        )
        return

    member = interaction.user

    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "I couldn't update your role.",
            ephemeral=True,
        )
        return

    try:
        if role in member.roles:
            await member.remove_roles(
                role,
                reason="Forever role removed via Hyped Ogre role panel",
            )
            await interaction.response.send_message(
                "**Forever** role removed.",
                ephemeral=True,
            )
            log_event(f"FOREVER ROLE REMOVED user={member.id}")
        else:
            await member.add_roles(
                role,
                reason="Forever role added via Hyped Ogre role panel",
            )
            await interaction.response.send_message(
                (
                    "**Forever** role added. By taking the role, you agree to:\n"
                    "• Keep the atmosphere positive\n"
                    "• Show up prepared and focused\n"
                    "• Be reliable and communicate openly\n"
                    "• No racism or hate of any kind"
                ),
                ephemeral=True,
            )
            log_event(f"FOREVER ROLE ADDED user={member.id}")
    except discord.Forbidden:
        await interaction.response.send_message(
            "I can't manage the Forever role. Check my role hierarchy and Manage Roles permission.",
            ephemeral=True,
        )


class ForeverRoleView(discord.ui.View):
    """Legacy production role panel; kept unchanged while V2 is tested."""

    def __init__(self, guild_id, role_id, log_event):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.role_id = role_id
        self.log_event = log_event

    @discord.ui.button(
        label="Forever Role",
        style=discord.ButtonStyle.secondary,
        custom_id="hyped_ogre_forever_role",
    )
    async def forever_role_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await toggle_forever_role(
            interaction,
            self.guild_id,
            self.role_id,
            self.log_event,
        )


def build_forever_role_embed():
    embed = discord.Embed(
        title="<Hyped> for WoW: Forever.",
        description=(
            "**Click the button below** to get access to the Forever channels.\n\n"
            "Click it again at any time to remove the role."
        ),
        color=ACCENT,
    )

    embed.set_thumbnail(url=FOREVER_THUMBNAIL)
    embed.set_image(url=FOREVER_IMAGE)

    return embed


class LeadershipApplicationPost(discord.ui.LayoutView):
    def __init__(self, *, applicant_id, interest, experience, extra):
        super().__init__(timeout=None)
        interest = discord.utils.escape_markdown(interest)
        experience = discord.utils.escape_markdown(experience)
        extra = discord.utils.escape_markdown(extra)

        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## Leadership Application\n"
                f"**Interested in:** {interest}\n\n"
                f"**Experience**\n{experience}\n\n"
                f"**Anything to add?**\n{extra or '—'}\n\n"
                f"-# Sent by <@{int(applicant_id)}>"
            ),
            accent_color=ACCENT,
        ))


class LeadershipApplicationModal(discord.ui.Modal):
    def __init__(self, *, interest):
        super().__init__(title="Leadership Application")
        self.interest = interest

        if interest == "Other":
            self.other_interest = discord.ui.TextInput(
                label="What are you interested in?",
                placeholder="Tell us where you'd like to help",
                required=True,
                max_length=100,
            )
            self.add_item(self.other_interest)
        else:
            self.other_interest = None

        self.experience = discord.ui.TextInput(
            label="Experience",
            placeholder="Tell us briefly about any relevant experience",
            style=discord.TextStyle.paragraph,
            required=True,
            min_length=3,
            max_length=1000,
        )
        self.add_item(self.experience)

        self.extra = discord.ui.TextInput(
            label="Anything to add?",
            placeholder="Strengths, useful skills, free time, or anything else",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
        )
        self.add_item(self.extra)

    async def on_submit(self, interaction):
        interest = (
            self.other_interest.value.strip()
            if self.other_interest is not None
            else self.interest
        )

        channel = interaction.client.get_channel(
            LEADERSHIP_APPLICATIONS_CHANNEL_ID
        )
        if channel is None:
            try:
                channel = await interaction.client.fetch_channel(
                    LEADERSHIP_APPLICATIONS_CHANNEL_ID
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Ogre couldn't find the leadership applications channel.",
                ephemeral=True,
            )
            return

        try:
            await channel.send(
                view=LeadershipApplicationPost(
                    applicant_id=interaction.user.id,
                    interest=interest,
                    experience=self.experience.value.strip(),
                    extra=self.extra.value.strip(),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Ogre couldn't send that application. Tell leadership.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "✅ **Application sent to leadership.** We'll have a look and get back to you.",
            ephemeral=True,
        )


class LeadershipInterestSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="What are you interested in?",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=interest, value=interest)
                for interest in LEADERSHIP_INTERESTS
            ],
        )

    async def callback(self, interaction):
        await interaction.response.send_modal(
            LeadershipApplicationModal(interest=self.values[0])
        )


class LeadershipInterestView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.add_item(LeadershipInterestSelect())

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This application belongs to someone else.",
                ephemeral=True,
            )
            return False
        return True


class ForeverRoleV2Panel(discord.ui.LayoutView):
    """Proposed V2 Get Role panel used by the owner preview command."""

    def __init__(self, guild_id, role_id, log_event):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.role_id = role_id
        self.log_event = log_event

        heading = discord.ui.Section(
            discord.ui.TextDisplay(
                "## <Hyped> for WoW: Forever.\n"
                "-# • **Click the button below** to get access to the Forever channels.\n"
                "-# • Click it again at any time to remove the role.\n\n"
                "**The plan so far:**\n"
                "-# • **Horde PvP**\n"
                "-# • **Main raid:** Sunday, 8pm - 11pm UK\n"
                "-# • **New content:** Wednesday added for the first 2 weeks, 8pm - 11pm UK\n"
                "-# • **Focus:** 10 & 20-player raids, with organised pugs for 40-player content\n\n"
                "Interested in helping out with Hyped? "
                "**Send us a short leadership application below.**"
            ),
            accessory=discord.ui.Thumbnail(
                FOREVER_THUMBNAIL,
                description="Hyped Forever",
            ),
        )

        self.add_item(discord.ui.Container(
            heading,
            accent_color=ACCENT,
        ))

        role_button = discord.ui.Button(
            label="Forever Role",
            style=discord.ButtonStyle.secondary,
        )
        leadership_button = discord.ui.Button(
            label="Leadership Application",
            style=discord.ButtonStyle.secondary,
        )

        async def role_callback(interaction):
            await toggle_forever_role(
                interaction,
                self.guild_id,
                self.role_id,
                self.log_event,
            )

        async def leadership_callback(interaction):
            await interaction.response.send_message(
                view=LeadershipInterestView(interaction.user.id),
                ephemeral=True,
            )

        role_button.callback = role_callback
        leadership_button.callback = leadership_callback

        self.add_item(discord.ui.ActionRow(
            role_button,
            leadership_button,
        ))

        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=FOREVER_IMAGE, description="Hyped")
        self.add_item(gallery)
