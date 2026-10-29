"""Leadership-only V2 raid poster: menu-driven setup, no public applicant list."""
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

import discord
import sqlite3

from cogs.raid_schedule import (
    RAID_IMAGE, ACCENT, format_discord_date,
    list_raids, parse_uk_datetime, read_raid, save_raid,
    validate_softres_url, set_softres_url,
)

UK = ZoneInfo("Europe/London")
INSTANCES = ("Hyjal (20m)", "Barrows (10m)", "Ony (40m)")
TIMES = ("18:00", "18:30", "19:00", "19:30", "20:00", "20:30",
         "21:00", "21:30", "22:00")
ANNOUNCEMENTS = {
    "1": "1 day before",
    "2": "2 days before",
    "3": "3 days before",
    "7": "1 week before",
    "0": "On raid day",
    "none": "To be confirmed",
    "custom": "Custom time",
}


def draft_from_event(event):
    """Existing raid loads without losing optional instance or SoftRes values."""
    if not event:
        return {
            "number": None, "day": None, "time": "20:00",
            "instance_1": None, "instance_2": None, "instance_3": None,
            "announce": "1", "custom_announce": None, "softres_url": None,
        }
    start = datetime.fromisoformat(event["raid_at"]).astimezone(UK)
    return {
        "number": event["raid_id"],
        "day": start.date().isoformat(),
        "time": start.strftime("%H:%M"),
        "instance_1": event.get("instance_1"),
        "instance_2": event.get("instance_2"),
        "instance_3": event.get("instance_3"),
        "announce": "custom" if event.get("roster_announce_at") else "none",
        "custom_announce": event.get("roster_announce_at"),
        "softres_url": event.get("softres_url"),
    }


def poster_schedule(draft):
    """Validated UK local clock times to save in the same format as /raid setup."""
    if not draft["day"]:
        raise ValueError("Choose the raid day.")
    if not draft["instance_1"]:
        raise ValueError("Choose raid instance #1.")
    raid_at = f'{draft["day"]} {draft["time"]}'
    parse_uk_datetime(raid_at)
    announce_type = draft["announce"]
    if announce_type == "none":
        announce = None
    elif announce_type == "custom":
        if not draft["custom_announce"]:
            raise ValueError("Set the custom roster announcement date.")
        announce_at = datetime.fromisoformat(draft["custom_announce"])
        announce = announce_at.astimezone(UK).strftime("%Y-%m-%d %H:%M")
    else:
        day = date.fromisoformat(draft["day"]) - timedelta(days=int(announce_type))
        announce = f'{day.isoformat()} {draft["time"]}'
    return raid_at, announce


class PosterTextModal(discord.ui.Modal):
    """Only custom values require typing; most setup uses dropdowns."""
    def __init__(self, poster, owner_id, message_id, field, title, label, placeholder,
                 required=True):
        super().__init__(title=title)
        self.poster = poster
        self.owner_id = owner_id
        self.message_id = message_id
        self.field = field
        self.text_field = discord.ui.TextInput(
            label=label, placeholder=placeholder,
            max_length=1500 if field == "softres_url" else 80,
            required=required,
        )
        self.add_item(self.text_field)

    async def on_submit(self, interaction):
        if not self.poster.authorised(interaction):
            await interaction.response.send_message("Only Ogre's owner can edit raids.", ephemeral=True)
            return
        value = self.text_field.value.strip()
        try:
            if self.field == "softres_url":
                value = validate_softres_url(value)
            elif self.field == "day":
                date.fromisoformat(value)
            elif self.field == "custom_announce":
                value = parse_uk_datetime(value).isoformat()
            elif self.field.startswith("instance_"):
                if not value:
                    raise ValueError("First raid instance cannot be blank.")
        except ValueError as exc:
            await interaction.response.send_message(f"Ogre couldn't use that value: {exc}", ephemeral=True)
            return
        new_draft = dict(self.poster.draft)
        new_draft[self.field] = value or None
        if self.field == "custom_announce":
            new_draft["announce"] = "custom"
        await interaction.response.defer(ephemeral=True)
        try:
            message = await interaction.channel.fetch_message(self.message_id)
            await message.edit(view=RaidPosterView(
                self.poster.cog, self.owner_id, new_draft
            ))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await interaction.followup.send("Ogre couldn't update raid poster.", ephemeral=True)


class RaidDeleteConfirmation(discord.ui.LayoutView):
    """Destructive action needs a second, explicit owner-only confirmation."""

    def __init__(self, cog, owner_id, raid_number):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.raid_number = raid_number
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Delete <Hyped> Raid #{raid_number}?\n\n"
                "**This cannot be undone.**\n"
                "Ogre will remove its raid card, sign-ups and attendance.\n"
                "Saved characters, gear exports and other raids remain untouched."
            ), accent_color=0xBA4C40,
        ))
        confirm = discord.ui.Button(label=f"Yes, delete Raid #{raid_number}",
                                    style=discord.ButtonStyle.danger)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

        async def do_delete(interaction):
            if not self.cog.allowed(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "Only Ogre's owner may delete raids.", ephemeral=True
                )
                return
            await self.cog.delete_raid_from_poster(interaction, self.raid_number)

        async def undo(interaction):
            if not self.cog.allowed(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "Only Ogre's owner may edit raids.", ephemeral=True
                )
                return
            from cogs.raid_schedule import read_raid
            event = read_raid(self.cog.raid_db_path(), self.raid_number)
            await interaction.response.edit_message(
                view=RaidPosterView(self.cog, self.owner_id, draft_from_event(event))
            )

        confirm.callback = do_delete
        cancel.callback = undo
        self.add_item(discord.ui.ActionRow(confirm, cancel))


class RaidPosterView(discord.ui.LayoutView):
    """Owner-controlled non-ephemeral setup card; published raid is a separate message."""
    def __init__(self, cog, owner_id, draft=None):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.draft = dict(draft or draft_from_event(None))
        raids = list_raids(cog.raid_db_path())
        if self.draft["number"] is None:
            self.draft["number"] = (raids[0]["raid_id"] + 1) if raids else 1
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(self.description()),
                accessory=discord.ui.Thumbnail(RAID_IMAGE),
            ), accent_color=ACCENT,
        ))
        self.saved_raid_ids = {r["raid_id"] for r in raids}
        self.add_raid_picker(raids)
        self.add_date_picker()
        self.add_time_picker()
        for i in (1, 2, 3):
            self.add_instance_picker(i)
        self.add_announcement_picker()
        self.add_controls()

    def authorised(self, interaction):
        return self.cog.allowed(interaction) and interaction.user.id == self.owner_id

    def description(self):
        d = self.draft
        instances = "\n".join(
            f"- Instance #{n}: **{discord.utils.escape_markdown(d[f'instance_{n}'] or 'Not selected')}**"
            for n in (1, 2, 3)
        )
        try:
            raid_at, announce = poster_schedule(d)
            raid_date = format_discord_date(parse_uk_datetime(raid_at).isoformat())
            announce_date = (
                format_discord_date(parse_uk_datetime(announce).isoformat())
                if announce else "To be confirmed"
            )
        except ValueError:
            raid_date = f'{d["day"] or "Choose date"} · {d["time"]} UK'
            announce_date = ANNOUNCEMENTS[d["announce"]]
        softres = "Set" if d["softres_url"] else "Not set (optional)"
        return (
            f"## Raid Poster · <Hyped> Raid #{d['number']}\n"
            "-# Leadership setup only. Choose options, then publish the real raid panel.\n\n"
            f"**Raid date:** {raid_date}\n"
            f"{instances}\n"
            f"**Roster announcement:** {announce_date}\n"
            f"**SoftRes:** {softres}\n"
            "**Post destination:** private #ogre (testing)\n\n"
            "Applicant list will never be shown on the raid panel."
        )

    def _select(self, label, options, callback):
        select = discord.ui.Select(
            placeholder=label, options=options, min_values=1, max_values=1
        )
        select.callback = callback
        self.add_item(discord.ui.ActionRow(select))
        return select

    async def changed(self, interaction, **updates):
        if not self.authorised(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can edit raids.", ephemeral=True
            )
            return
        draft = {**self.draft, **updates}
        await interaction.response.edit_message(
            view=RaidPosterView(self.cog, self.owner_id, draft)
        )

    def add_raid_picker(self, raids):
        options = [discord.SelectOption(
            label=f"New Raid #{(raids[0]['raid_id'] + 1) if raids else 1}",
            value="new",
        )]
        options += [
            discord.SelectOption(label=f"Edit Raid #{r['raid_id']}", value=str(r["raid_id"]))
            for r in raids
        ]
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            selection = picker.values[0]
            d = draft_from_event(None if selection == "new" else read_raid(
                self.cog.raid_db_path(), int(selection)
            ))
            await interaction.response.edit_message(
                view=RaidPosterView(self.cog, self.owner_id, d)
            )
        picker = self._select("Choose new or existing raid", options, choose)

    def add_date_picker(self):
        today = datetime.now(UK).date()
        dates = [today + timedelta(days=i) for i in range(24)]
        chosen = self.draft.get("day")
        if chosen and chosen not in (d.isoformat() for d in dates):
            dates = [date.fromisoformat(chosen)] + dates[:23]
        options = [
            discord.SelectOption(
                label=d.strftime("%a %d %b %Y"),
                value=d.isoformat(), default=d.isoformat() == chosen,
            ) for d in dates
        ]
        options.append(discord.SelectOption(label="Other date...", value="custom"))
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            if picker.values[0] == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id, "day",
                    "Choose raid date", "YYYY-MM-DD", "2026-11-05",
                ))
            else:
                await self.changed(interaction, day=picker.values[0])
        picker = self._select("Choose raid date (UK)", options, choose)

    def add_time_picker(self):
        choices = list(TIMES)
        if self.draft["time"] not in choices:
            choices.append(self.draft["time"])
        options = [
            discord.SelectOption(label=f"{t} UK", value=t, default=t == self.draft["time"])
            for t in choices
        ]
        async def choose(interaction):
            await self.changed(interaction, time=picker.values[0])
        picker = self._select("Choose start time (UK)", options, choose)

    def add_instance_picker(self, number):
        field = f"instance_{number}"
        current = self.draft[field]
        choices = [
            discord.SelectOption(
                label="None", value="none", default=current is None,
            )
        ] if number > 1 else []
        for label in INSTANCES:
            choices.append(discord.SelectOption(
                label=label,
                value="custom" if label == "Other / Custom" else label,
                default=label == current,
            ))
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            value = picker.values[0]
            if value == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id, field,
                    f"Instance #{number}", "Instance name", "Raid instance name",
                ))
            else:
                updates = {field: None if value == "none" else value}
                if number == 2 and value == "none":
                    updates["instance_3"] = None
                await self.changed(interaction, **updates)
        picker = self._select(f"Raid instance #{number}", choices, choose)

    def add_announcement_picker(self):
        options = [
            discord.SelectOption(
                label=label, value=key, default=key == self.draft["announce"]
            ) for key, label in ANNOUNCEMENTS.items()
        ]
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            if picker.values[0] == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id,
                    "custom_announce", "Roster announcement",
                    "YYYY-MM-DD HH:MM (UK)", "2026-11-04 20:00",
                ))
            else:
                await self.changed(interaction, announce=picker.values[0])
        picker = self._select("Roster announce date", options, choose)

    def add_controls(self):
        row = discord.ui.ActionRow()
        softres = discord.ui.Button(label="Optional SoftRes", style=discord.ButtonStyle.secondary)
        clear = discord.ui.Button(label="Remove SoftRes", style=discord.ButtonStyle.secondary,
                                  disabled=not bool(self.draft["softres_url"]))
        publish = discord.ui.Button(label="Post / Update Raid", style=discord.ButtonStyle.success)
        delete = discord.ui.Button(label="Delete Raid", style=discord.ButtonStyle.danger,
                                  disabled=self.draft["number"] not in self.saved_raid_ids)

        async def set_link(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await interaction.response.send_modal(PosterTextModal(
                self, self.owner_id, interaction.message.id,
                "softres_url", "Optional SoftRes link", "SoftRes raid URL",
                "https://softres.it/raid/...", required=False,
            ))

        async def remove_link(interaction):
            await self.changed(interaction, softres_url=None)

        async def post(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            try:
                raid_at, announce = poster_schedule(self.draft)
                event = save_raid(
                    self.cog.raid_db_path(), self.draft["number"], raid_at,
                    self.draft["instance_1"], self.draft["instance_2"],
                    self.draft["instance_3"], announce, self.draft["softres_url"],
                )
                if self.draft["softres_url"] is None:
                    event = set_softres_url(
                        self.cog.raid_db_path(), self.draft["number"], None
                    )
            except (ValueError, sqlite3.Error) as exc:
                # Configuration errors should never publish a partial raid.
                await interaction.response.send_message(
                    f"Check raid details before posting: {exc}", ephemeral=True
                )
                return
            await self.cog.post_panel(interaction, event)

        async def ask_delete(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            if self.draft["number"] not in self.saved_raid_ids:
                await interaction.response.send_message(
                    "This raid is not saved yet.", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=RaidDeleteConfirmation(
                self.cog, self.owner_id, self.draft["number"]
            ))

        softres.callback = set_link
        clear.callback = remove_link
        publish.callback = post
        delete.callback = ask_delete
        row.add_item(softres)
        row.add_item(clear)
        row.add_item(publish)
        row.add_item(delete)
        self.add_item(row)
