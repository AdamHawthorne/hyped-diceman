"""Regression tests for the four-button raid member flow and private gear uploads."""
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from PIL import Image

from cogs import raid_schedule, raid_signup as raid, raid_sheets, raid_sheets_v3


class RaidMemberFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_raid_sheet_names_resolve_from_discord_without_changing_rolls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid.save_character(42, "Character One", "Mage", "Frost", "DPS", path)
            raid.save_character(42, "Character Two", "Mage", "Frost", "DPS", path)
            member = SimpleNamespace(display_name="Ogre Leader")
            guild = SimpleNamespace(
                get_member=Mock(return_value=None),
                fetch_member=AsyncMock(return_value=member),
            )
            bot = SimpleNamespace(
                get_guild=Mock(return_value=guild),
                get_user=Mock(return_value=None),
            )
            cog = raid.RaidPrototype(bot)
            with patch.object(raid, "RAID_DB", path):
                names = await cog.raid_discord_names()
                self.assertEqual(names, {42: "Ogre Leader"})
                guild.fetch_member.assert_awaited_once_with(42)
                # A cached name avoids additional requests on the next sync.
                self.assertEqual(await cog.raid_discord_names(), names)
                guild.fetch_member.assert_awaited_once_with(42)
                with patch.object(raid_sheets_v3, "sync_once", return_value=(
                    {"characters": 2}, set()
                )) as sync:
                    self.assertEqual(
                        await cog.sync_raid_sheets(),
                        ({"characters": 2}, set()),
                    )
                    sync.assert_called_once_with(path, {42: "Ogre Leader"})

    async def test_main_panel_has_exactly_four_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            event = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)",
            )
            panel = raid_schedule.RaidEventPanel(
                event, on_signup=AsyncMock(), on_update=AsyncMock(),
                on_gear=AsyncMock(), on_absent=AsyncMock()
            )
            buttons = [
                item for item in panel.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual([b.label for b in buttons],
                             ["Attend", "Characters", "SimC", "Alarm", "Absent"])
            self.assertTrue(all(not b.disabled for b in buttons if b.label != "Alarm"))
            self.assertTrue(next(b for b in buttons if b.label == "Alarm").disabled)
            self.assertEqual(buttons[0].style, discord.ButtonStyle.success)
            self.assertEqual(buttons[-1].style, discord.ButtonStyle.danger)
            self.assertTrue(panel.is_persistent())

    async def test_signup_notes_optional_and_one_signup_per_raid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 2, "2026-12-09 21:00", "Ony (40m)")
            raid.save_character(42, "First Last", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            raid.save_signup(2, 42, char_id, path, note="Need to leave at 22:30")
            with raid.database(path) as con:
                first = con.execute(
                    "SELECT note, submitted_at FROM signups WHERE raid_id=2"
                ).fetchone()
            self.assertEqual(first[0], "Need to leave at 22:30")
            raid.save_signup(2, 42, char_id, path)
            with raid.database(path) as con:
                rows = con.execute("SELECT note, submitted_at FROM signups").fetchall()
            self.assertEqual(rows, [first])
            raid.save_signup(2, 42, char_id, path, note="")
            with raid.database(path) as con:
                self.assertEqual(con.execute(
                    "SELECT note FROM signups"
                ).fetchone()[0], "")
            with self.assertRaises(ValueError):
                raid.save_signup(2, 42, char_id, path, note="X" * 501)
            raid.save_signup(2, 42, char_id, path, note="Leaving early")
            raid.save_absence(2, 42, "Not coming", path)
            with raid.database(path) as con:
                self.assertEqual(
                    con.execute("SELECT note FROM signups WHERE raid_id=2"
                    ).fetchone()[0], "Leaving early"
                )
            self.assertEqual(raid.my_absence(2, 42, path)[0], "Not coming")

    async def test_profile_edit_keeps_character_and_raid_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            raid.save_character(42, "First Last", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            raid.save_signup(1, 42, char_id, path)
            self.assertEqual(raid.update_character_profile(
                42, char_id, "Warrior", "Protection", "Tank", path
            ), "First Last")
            self.assertEqual(raid.member_characters(42, path)[0][0], char_id)
            self.assertEqual(raid.member_characters(42, path)[0][2:5],
                             ("Warrior", "Protection", "Tank"))
            self.assertEqual(raid.my_signup(1, 42, path), ("First Last", "Available"))
            with self.assertRaises(ValueError):
                raid.update_character_profile(
                    99, char_id, "Mage", "Frost", "DPS", path
                )

    async def test_screenshot_private_jpeg_and_sheets_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid.save_character(42, "First Last", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            image = io.BytesIO()
            Image.new("RGB", (400, 250), (110, 120, 130)).save(
                image, format="PNG"
            )
            raid.save_gear_image(
                42, char_id, "character.png", image.getvalue(), path
            )
            filename, payload = raid.last_gear_image(char_id, path)
            self.assertEqual(filename, "character-panel.jpg")
            self.assertTrue(payload.startswith(b"\xff\xd8"))
            row = raid_sheets._desired_rows(path)["gear"][str(char_id)]
            self.assertEqual(row[4], "Screenshot")
            self.assertEqual(len(row), 5)
            self.assertNotIn(payload[:20].hex(), json.dumps(row))
            with self.assertRaises(ValueError):
                raid.save_gear_image(99, char_id, "character.png",
                                     image.getvalue(), path)
            with self.assertRaises(ValueError):
                raid.save_gear_image(42, char_id, "not-image.png", b"x" * 150, path)

    async def test_gear_modal_accepts_text_and_optional_uploads(self):
        modal = raid.GearUploadModal(42, 15, "First Last")
        self.assertFalse(modal.simc.required)
        self.assertFalse(modal.uploads.required)
        self.assertEqual(modal.uploads.max_values, 2)
        self.assertEqual(modal.uploads.min_values, 0)
        self.assertEqual(modal.simc.max_length, 4000)

    async def test_signup_and_update_are_private_discord_views(self):
        with patch.object(raid, "member_characters", return_value=[
            (7, "First Last", "Mage", "Frost", "DPS", None)
        ]):
            for purpose in ("signup", "gear", "update"):
                view = raid.CharacterActionView(42, raid_id=1, purpose=purpose)
                labels = json.dumps(view.to_components())
                self.assertIn("First Last", labels)
                self.assertNotIn('"label": "Absent"', labels)
        first_time = raid.CharacterNameModal(
            42, "Mage", "Frost", "DPS", signup_raid_id=1,
        )
        self.assertEqual(len(first_time.children), 2)
        self.assertEqual(first_time.note.label, "Raid note (optional)")
        self.assertFalse(first_time.note.required)
        editing = raid.ClassPickerView(42, editing_character_id=7)
        self.assertIn("Update character", json.dumps(editing.to_components()))
        picker = raid.SpecPickerView(42, "Mage", editing_character_id=7)
        self.assertEqual(picker.editing_character_id, 7)

    async def test_signup_note_modal_saves_optional_note_for_correct_member(self):
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "my_signup", return_value=None), \
             patch.object(raid, "save_signup") as saving:
            modal = raid.SignupNoteModal(42, 1, 5, "First Last")
            modal.note._value = "Can stay for 2 hours"
            response = SimpleNamespace(send_message=AsyncMock())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
            )
            await modal.on_submit(interaction)
            saving.assert_called_once_with(
                1, 42, 5, note="Can stay for 2 hours"
            )
            self.assertTrue(response.send_message.await_args.kwargs["ephemeral"])
            response.send_message.reset_mock()
            interaction.user.id = 99
            await modal.on_submit(interaction)
            saving.assert_called_once()
            self.assertIn("isn't yours", response.send_message.await_args.args[0])

    async def test_update_wizard_saves_profile_in_place(self):
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "update_character_profile",
                          return_value="First Last") as saving:
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                response=SimpleNamespace(
                    edit_message=AsyncMock(), send_message=AsyncMock()
                ),
            )
            await raid.apply_profile_update(
                interaction, 42, 7, "Warrior", "Protection", "Tank"
            )
            saving.assert_called_once_with(
                42, 7, "Warrior", "Protection", "Tank"
            )
            self.assertIsInstance(
                interaction.response.edit_message.await_args.kwargs["view"],
                raid.ProfileUpdatedView,
            )

    async def test_absent_button_opens_modal_directly(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            cog = raid.RaidPrototype(SimpleNamespace())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                response=SimpleNamespace(send_modal=AsyncMock()),
            )
            await cog.on_absent(interaction, 5)
            modal = interaction.response.send_modal.await_args.args[0]
            self.assertIsInstance(modal, raid.AbsenceNoteModal)
            self.assertEqual(modal.raid_id, 5)
