"""Leadership-only V2 raid poster: menu-driven setup, no public applicant list."""
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
import sqlite3

from cogs.raid_schedule import (
    RAID_IMAGE, ACCENT, format_discord_date, validate_check_message_url,
    raid_panel_when,
    list_raids, parse_uk_datetime, read_raid, save_raid,
    validate_softres_url, validate_banner_url, set_softres_url,
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
    "custom": "Choose exact announcement date & time…",
}


def draft_from_event(event):
    """Existing raid loads without losing optional instance or SoftRes values."""
    if not event:
        return {
            "number": None, "day": None, "time": "20:00", "channel_id": None, "ping_role_ids": [], "default_show_discord": True,
            "end_time": None, "check_message_url": None,
            "instance_1": None, "instance_2": None, "instance_3": None,
            "announce": "1", "custom_announce": None, "softres_url": None,
            "custom_title": "", "banner_url": None,
        }
    start = datetime.fromisoformat(event["raid_at"]).astimezone(UK)
    return {
        "number": event["raid_id"], "channel_id": event.get("panel_channel_id"), "ping_role_ids": [],
        "default_show_discord": bool(event.get("default_show_discord", 1)),
        "day": start.date().isoformat(),
        "time": start.strftime("%H:%M"),
        "end_time": (datetime.fromisoformat(event["end_at"]).astimezone(UK).strftime("%H:%M")
                     if event.get("end_at") else None),
        "check_message_url": event.get("check_message_url"),
        "instance_1": event.get("instance_1"),
        "instance_2": event.get("instance_2"),
        "instance_3": event.get("instance_3"),
        "announce": "custom" if event.get("roster_announce_at") else "none",
        "custom_announce": event.get("roster_announce_at"),
        "softres_url": event.get("softres_url"),
        "custom_title": event.get("custom_title") or "",
        "banner_url": event.get("banner_url"),
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


def poster_end_at(draft):
    """Return optional UK end datetime, or None for a three-hour default."""
    if not draft.get("end_time"):
        return None
    start = parse_uk_datetime(f'{draft["day"]} {draft["time"]}')
    try:
        end_clock = datetime.strptime(draft["end_time"], "%H:%M").time()
    except (ValueError, TypeError) as exc:
        raise ValueError("End time must use HH:MM (UK time).") from exc
    # A clock time at/before the start means an end after midnight.
    end_day = start.date() + timedelta(days=end_clock <= start.time())
    end = parse_uk_datetime(f'{end_day.isoformat()} {end_clock:%H:%M}')
    duration = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
    if not 0 < duration <= 12 * 3600:
        raise ValueError("End time must be after the start, within 12 hours.")
    return end.isoformat()


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
            max_length=(1500 if field == "softres_url"
                        else 512 if field in ("banner_url", "check_message_url")
                        else 70 if field == "custom_title"
                        else 5 if field in ("time", "end_time") else 80),
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
            elif self.field == "custom_title":
                value = " ".join(value.split())
            elif self.field in ("time", "end_time"):
                if value:
                    datetime.strptime(value, "%H:%M")
            elif self.field == "banner_url":
                value = validate_banner_url(value)
            elif self.field == "check_message_url":
                value = validate_check_message_url(value)
            elif self.field.startswith("instance_"):
                if not value:
                    raise ValueError("First raid instance cannot be blank.")
        except ValueError as exc:
            await interaction.response.send_message(f"Ogre couldn't use that value: {exc}", ephemeral=True)
            return
        new_draft = dict(self.poster.draft)
        new_draft[self.field] = value or None
        if self.field in ("time", "end_time") and new_draft["day"]:
            try:
                poster_end_at(new_draft)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
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
        self.add_destination_picker()
        self.add_ping_role_picker()
        self.add_visibility_picker()
        self.add_date_picker()
        self.add_time_picker()
        self.add_end_time_picker()
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
            raid_date = raid_panel_when(parse_uk_datetime(raid_at).isoformat(), poster_end_at(d))
            announce_date = (
                format_discord_date(parse_uk_datetime(announce).isoformat(),
                                    style="R", with_relative=False)
                if announce else "To be confirmed"
            )
        except ValueError:
            raid_date = f'{d["day"] or "Choose date"} · {d["time"]} UK'
            announce_date = ANNOUNCEMENTS[d["announce"]]
        softres = "Set" if d["softres_url"] else "Not set (optional)"
        banner = "Custom image" if d.get("banner_url") else "Default image"
        custom_title = " ".join((d.get("custom_title") or "").split())
        display_title = f"<Hyped> Raid #{d['number']}"
        if custom_title:
            display_title += " - " + discord.utils.escape_mentions(
                discord.utils.escape_markdown(custom_title)
            )
        return (
            f"## Raid Poster · {display_title}\n"
            "-# Leadership setup only. Choose options, then publish the real raid panel.\n\n"
            f"**Raid date:** {raid_date}\n"
            f"**Check message:** {'[View message](' + d['check_message_url'] + ')' if d.get('check_message_url') else 'Not set (optional)'}\n"
            f"{instances}\n"
            f"**Confirmation:** {announce_date}\n"
            f"**SoftRes:** {softres}\n"
            f"**Bottom banner:** {banner}\n"
            f"**Post destination:** {'<#' + str(d['channel_id']) + '>' if d.get('channel_id') else 'Current channel (private #ogre)'}\n\n"
            f"**Ping roles on new post:** {', '.join('<@&' + str(r) + '>' for r in d.get('ping_role_ids', [])) or 'None'}\n\n"
            f"**Default Show on Discord:** {'Yes' if d.get('default_show_discord', True) else 'No'}\n\n"
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

    def add_destination_picker(self):
        picker = discord.ui.ChannelSelect(
            placeholder="Choose destination channel",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1,
        )
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await self.changed(interaction, channel_id=picker.values[0].id)
        picker.callback = choose
        self.add_item(discord.ui.ActionRow(picker))

    def add_ping_role_picker(self):
        picker = discord.ui.RoleSelect(
            placeholder="Roles to ping (optional, up to 10)",
            min_values=0, max_values=10,
        )
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await self.changed(interaction, ping_role_ids=[role.id for role in picker.values])
        picker.callback = choose
        self.add_item(discord.ui.ActionRow(picker))

    def add_visibility_picker(self):
        async def choose(interaction):
            await self.changed(interaction, default_show_discord=(picker.values[0] == "yes"))
        picker = self._select("Default Show on Discord", [
            discord.SelectOption(label="Yes - show selected players", value="yes",
                                 default=bool(self.draft.get("default_show_discord", True))),
            discord.SelectOption(label="No - hide until individually enabled", value="no",
                                 default=not bool(self.draft.get("default_show_discord", True))),
        ], choose)

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
        options.append(discord.SelectOption(label="Other start time (HH:MM)...", value="custom"))
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            if picker.values[0] == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id,
                    "time", "Choose raid start time", "Start time HH:MM (UK)",
                    "21:00",
                ))
            else:
                await self.changed(interaction, time=picker.values[0])
        picker = self._select("Choose start time (UK)", options, choose)

    def add_end_time_picker(self):
        d = self.draft
        day = d["day"] or datetime.now(UK).date().isoformat()
        try:
            start = parse_uk_datetime(f'{day} {d["time"]}')
        except ValueError:
            start = datetime.now(UK)
        # Convenient options around the raid start; custom HH:MM is also supported.
        suggested = []
        for half_hours in range(2, 13):
            end = (start.astimezone(timezone.utc) +
                   timedelta(minutes=30 * half_hours)).astimezone(UK)
            value = end.strftime("%H:%M")
            if value not in suggested:
                suggested.append(value)
        auto_end = (start.astimezone(timezone.utc) + timedelta(hours=3)).astimezone(UK)
        current = d.get("end_time")
        options = [discord.SelectOption(
            label=f"Default: +3h ({auto_end:%H:%M} UK)",
            value="auto", default=current is None,
        )]
        for value in suggested:
            options.append(discord.SelectOption(
                label=f"{value} UK" + (" (next day)" if value <= d["time"] else ""),
                value=value, default=value == current,
            ))
        if current and current not in suggested:
            options.append(discord.SelectOption(
                label=f"{current} UK (custom)",
                value=current, default=True,
            ))
        options.append(discord.SelectOption(label="Other time (HH:MM)...", value="custom"))
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            selection = picker.values[0]
            if selection == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id,
                    "end_time", "Choose raid end time", "End time HH:MM (UK)",
                    "00:00",
                ))
            else:
                await self.changed(interaction, end_time=None if selection == "auto" else selection)
        picker = self._select("Choose end time (UK)", options, choose)

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
        picker = self._select("When to announce the roster?", options, choose)

    def add_controls(self):
        row = discord.ui.ActionRow()
        title = discord.ui.Button(label="Optional Raid Title",
                                  style=discord.ButtonStyle.secondary)
        banner = discord.ui.Button(label="Bottom Banner",
                                   style=discord.ButtonStyle.secondary)
        softres = discord.ui.Button(label="Optional SoftRes", style=discord.ButtonStyle.secondary)
        clear = discord.ui.Button(label="Remove SoftRes", style=discord.ButtonStyle.secondary,
                                  disabled=not bool(self.draft["softres_url"]))
        check_link = discord.ui.Button(label="Check Message Link",
                                       style=discord.ButtonStyle.secondary)
        clear_check = discord.ui.Button(label="Remove Check Link",
                                        style=discord.ButtonStyle.secondary,
                                        disabled=not bool(self.draft.get("check_message_url")))
        publish = discord.ui.Button(label="Post / Update Raid", style=discord.ButtonStyle.success)
        manage = discord.ui.Button(label="Manage Raid",
                                   style=discord.ButtonStyle.primary,
                                   disabled=self.draft["number"] not in self.saved_raid_ids)
        delete = discord.ui.Button(label="Delete Raid", style=discord.ButtonStyle.danger,
                                  disabled=self.draft["number"] not in self.saved_raid_ids)

        async def set_title(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await interaction.response.send_modal(PosterTextModal(
                self, self.owner_id, interaction.message.id,
                "custom_title", "Optional raid title", "Text after Raid #",
                "e.g. First clear / Progression", required=False,
            ))

        async def set_banner(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await interaction.response.send_modal(PosterTextModal(
                self, self.owner_id, interaction.message.id,
                "banner_url", "Bottom banner image", "HTTPS image URL",
                "https://i.vgy.me/yourimage.png", required=False,
            ))

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

        async def set_check_link(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await interaction.response.send_modal(PosterTextModal(
                self, self.owner_id, interaction.message.id,
                "check_message_url", "Optional Check message",
                "Discord message URL",
                "https://discord.com/channels/server/channel/message",
                required=False,
            ))

        async def remove_check_link(interaction):
            await self.changed(interaction, check_message_url=None)

        async def post(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            try:
                raid_at, announce = poster_schedule(self.draft)
                end_at = poster_end_at(self.draft)
                event = save_raid(
                    self.cog.raid_db_path(), self.draft["number"], raid_at,
                    self.draft["instance_1"], self.draft["instance_2"],
                    self.draft["instance_3"], announce, self.draft["softres_url"],
                    custom_title=self.draft.get("custom_title") or "",
                    banner_url=self.draft.get("banner_url") or "",
                    end_at=end_at,
                    check_message_url=self.draft.get("check_message_url"),
                )
                from cogs.raid_schedule import database
                with database(self.cog.raid_db_path()) as con:
                    con.execute("UPDATE raids SET default_show_discord=? WHERE raid_id=?",
                                (int(self.draft.get("default_show_discord", True)), event["raid_id"]))
                event = read_raid(self.cog.raid_db_path(), event["raid_id"])
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
            await self.cog.post_panel(interaction, event, destination_id=self.draft.get("channel_id"), ping_role_ids=self.draft.get("ping_role_ids", []))

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

        async def open_manage(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            event = read_raid(self.cog.raid_db_path(), self.draft["number"])
            if not event:
                await interaction.response.send_message("Save the raid first.", ephemeral=True)
                return
            if not event.get("roster_published"):
                await interaction.response.send_message(
                    "Confirm the roster before managing attendance.", ephemeral=True)
                return
            from cogs.raid_signup import RaidManageView
            await interaction.response.send_message(
                f"Raid #{event['raid_id']} management", ephemeral=True,
                view=RaidManageView(self.cog, event["raid_id"]))

        manage.callback = open_manage
        title.callback = set_title
        banner.callback = set_banner
        softres.callback = set_link
        clear.callback = remove_link
        publish.callback = post
        check_link.callback = set_check_link
        clear_check.callback = remove_check_link
        delete.callback = ask_delete
        row.add_item(title)
        row.add_item(banner)
        row.add_item(softres)
        row.add_item(clear)
        self.add_item(row)
        self.add_item(discord.ui.ActionRow(check_link, clear_check))
        self.add_item(discord.ui.ActionRow(publish, manage, delete))
