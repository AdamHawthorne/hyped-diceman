"""Forever opt-in role panel, isolated from Dice and LFG."""
import discord

class ForeverRoleView(discord.ui.View):
    def __init__(self, guild_id, role_id, log_event):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.role_id = role_id
        self.log_event = log_event

    @discord.ui.button(
        label="Forever Role",
        style=discord.ButtonStyle.secondary,
        custom_id="hyped_ogre_forever_role"
    )
    async def forever_role_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        guild = interaction.guild

        if guild is None or guild.id != self.guild_id:
            await interaction.response.send_message(
                "This role button is only available in Hyped.",
                ephemeral=True
            )
            return

        role = guild.get_role(self.role_id)

        if role is None:
            await interaction.response.send_message(
                "The Forever role is not configured correctly.",
                ephemeral=True
            )
            return

        member = interaction.user

        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "I couldn't update your role.",
                ephemeral=True
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(
                    role,
                    reason="Forever role removed via Hyped Ogre role panel"
                )
                await interaction.response.send_message(
                    "**Forever** role removed.",
                    ephemeral=True
                )
                self.log_event(f"FOREVER ROLE REMOVED user={member.id}")
            else:
                await member.add_roles(
                    role,
                    reason="Forever role added via Hyped Ogre role panel"
                )
                await interaction.response.send_message(
                    (
                        "**Forever** role added. By taking the role, you agree to:\n"
                        "• keep the atmosphere positive and enjoyable\n"
                        "• show up prepared and focused\n"
                        "• be reliable and communicate clearly\n"
                        "• no racism or hate of any kind"
                    ),
                    ephemeral=True
                )
                self.log_event(f"FOREVER ROLE ADDED user={member.id}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can't manage the Forever role. Check my role hierarchy and Manage Roles permission.",
                ephemeral=True
            )


def build_forever_role_embed():
    embed = discord.Embed(
        title="<Hyped> for WoW: Forever.",
        description=(
            "**Click the button below** to get access to the Forever channels.\n\n"
            "Click it again at any time to remove the role."
        ),
        color=0x8C5A3C
    )

    embed.set_thumbnail(url="https://i.vgy.me/asI88C.png")
    embed.set_image(url="https://i.vgy.me/WGNGdr.png")

    return embed


