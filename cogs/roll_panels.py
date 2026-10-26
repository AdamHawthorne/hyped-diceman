"""Components V2 daily roll and all-time coin leaderboard."""
import discord

COIN_IMAGE = "https://i.vgy.me/agW53e.png"
OGRE_IMAGE = "https://i.vgy.me/zJXBXg.png"
GOLD = 0xD19A3B
PAGE_SIZE = 15


class DailyRollPanel(discord.ui.LayoutView):
    def __init__(self, *, day, winner_text, tagline, on_roll, on_leaderboard,
                 on_stats, preview=False):
        super().__init__(timeout=60 if preview else None)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay("## Yesterday's gold coin\n" + winner_text),
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "## Hyped Rolls · Daily Reset\n"
                    f"**{tagline}**\n\n"
                    "Roll once per day. Highest player roll earns one gold coin "
                    "at midnight UK time. First roll wins a tie.\n"
                    "Bragging rights only. Ogre keep score.\n\n"
                    "`/leaderboard` · Gold coins\n`/stats` · Your stats\n"
                    f"-# Roll day: {day} · Europe/London"
                ),
                accessory=discord.ui.Thumbnail(OGRE_IMAGE, description="Hyped Ogre"),
            ),
            accent_color=GOLD,
        ))
        controls = discord.ui.ActionRow()
        for label, custom_id, callback in (
            ("Roll", "hyped_ogre_daily_roll_v2", on_roll),
            ("Leaderboard", "hyped_ogre_coin_leaderboard", on_leaderboard),
            ("My stats", "hyped_ogre_roll_stats", on_stats),
        ):
            button = discord.ui.Button(
                label=label, custom_id=custom_id,
                style=discord.ButtonStyle.secondary, disabled=preview,
            )
            button.callback = callback
            controls.add_item(button)
        self.add_item(controls)
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=COIN_IMAGE, description="Gold coin")
        self.add_item(gallery)


class CoinLeaderboard(discord.ui.LayoutView):
    def __init__(self, rows, *, owner_id, page=0):
        super().__init__(timeout=300)
        self.rows = rows
        self.owner_id = owner_id
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(page, pages - 1))
        visible = rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        lines = [f"**{r['rank']}.** <@{r['user_id']}> · **{r['coins']}** gold "
                 f"{'coin' if r['coins'] == 1 else 'coins'}" for r in visible]
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## Hyped Gold Coins\n"
                + ("\n".join(lines) or "No gold coins awarded yet. First award at midnight UK time.")
                + f"\n\n-# {sum(r['coins'] for r in rows)} coins across the server · "
                  f"Page {self.page + 1} of {pages}\n-# Ogre keep score."
            ), accent_color=GOLD,
        ))
        if pages > 1:
            controls = discord.ui.ActionRow()
            for label, delta, disabled in (
                ("‹ Previous", -1, self.page == 0),
                ("Next ›", 1, self.page == pages - 1),
            ):
                button = discord.ui.Button(label=label, disabled=disabled,
                                           style=discord.ButtonStyle.secondary)

                async def turn(interaction, delta=delta):
                    await interaction.response.edit_message(view=CoinLeaderboard(
                        self.rows, owner_id=self.owner_id, page=self.page + delta,
                    ))

                button.callback = turn
                controls.add_item(button)
            self.add_item(controls)
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=COIN_IMAGE, description="Gold coin")
        self.add_item(gallery)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Use /leaderboard to open your own leaderboard.", ephemeral=True,
            )
            return False
        return True
