"""Offline tests of isolated raid sign-up preview and attendance schema."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from cogs import raid_schedule

import cogs.raid_signup as raid


class RaidPrototypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_attendance_fields_and_status_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with raid.database(path) as con:
                tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"characters", "raids", "signups", "gear_exports", "attendance"} <= tables)
                fields = {row[1] for row in con.execute("PRAGMA table_info(attendance)")}
                self.assertTrue({"raid_id", "discord_id", "character_id", "status", "late_minutes", "afk_minutes", "leadership_notes"} <= fields)
        self.assertEqual(set(raid.ATTENDANCE_OPTIONS), {"Pending", "Attended", "Late", "AFK", "No-show", "Excused"})
        self.assertIn("Attendance", raid.SHEET_TABS)

    async def test_schedule_migration_and_v2_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.ensure_raid_fields(path)  # startup migration is safe to repeat
            event = raid_schedule.save_raid(
                path, 1, "2026-11-05 20:00", "Molten Core",
                roster_announce="2026-11-04 19:00",
            )
            self.assertEqual(event["raid_id"], 1)
            self.assertIsNone(event["instance_2"])
            panel = raid_schedule.RaidEventPanel(
                event, on_register=AsyncMock(), on_characters=AsyncMock()
            )
            text = json.dumps(panel.to_components())
            self.assertIn("<Hyped> Raid #1", text)
            self.assertIn("Raid instance #1:", text)
            self.assertIn("Molten Core", text)
            self.assertNotIn("Raid instance #2:", text)
            self.assertNotIn("Raid instance #3:", text)
            self.assertIn("XIszIH.png", text)
            self.assertIn("Roster announce date:", text)
            self.assertIn("Sign-ups hidden", text)
            self.assertNotIn("~~Sign-ups hidden~~", text)
            self.assertIn("https://i.vgy.me/WGNGdr.png", text)
            self.assertNotIn("SoftRes sign-up", text)
            self.assertTrue(any(isinstance(c, raid_schedule.discord.ui.MediaGallery) for c in panel.children))
            self.assertIn("<t:", text)
            self.assertNotIn("Snore Mon", text)
            self.assertTrue(panel.is_persistent())
            self.assertIn('"disabled": true', text)
            event = raid_schedule.save_raid(
                path, 1, "2026-11-06 20:00", "Molten Core",
                "Onyxia", "Blackwing Lair", "2026-11-05 18:00",
            )
            updated = json.dumps(raid_schedule.RaidEventPanel(
                event, on_register=AsyncMock(), on_characters=AsyncMock()
            ).to_components())
            self.assertIn("Raid instance #2:", updated)
            self.assertIn("Raid instance #3:", updated)
            self.assertEqual(raid_schedule.latest_raid(path)["raid_id"], 1)
            raid_schedule.record_panel(path, 1, raid.TEST_CHANNEL_ID, 12345)
            self.assertEqual(raid_schedule.active_panels(path)[0]["panel_message_id"], 12345)

    async def test_optional_inline_softres_link_and_separate_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-11-05 20:00", "Molten Core")
            event = raid_schedule.set_softres_url(path, 1, "https://softres.it/raid/abc123")
            panel = raid_schedule.RaidEventPanel(
                event, on_register=AsyncMock(), on_characters=AsyncMock()
            )
            components = json.dumps(panel.to_components())
            self.assertIn("[SoftRes sign-up](https://softres.it/raid/abc123)", components)
            self.assertNotIn('"label": "SoftRes"', components)
            self.assertNotIn('"url": "https://softres.it/raid/abc123"', components)
            # Editing raid details without a link does not wipe the existing SoftRes.
            event = raid_schedule.save_raid(path, 1, "2026-11-06 20:00", "Onyxia")
            self.assertEqual(event["softres_url"], "https://softres.it/raid/abc123")
            event = raid_schedule.set_softres_url(path, 1, None)
            self.assertIsNone(event["softres_url"])
            self.assertNotIn("SoftRes sign-up", raid_schedule.raid_panel_text(event))
            self.assertEqual(raid_schedule.validate_softres_url(
                "https://legacy.softres.it/raid/gcz7uk"),
                "https://legacy.softres.it/raid/gcz7uk")
            with self.assertRaises(ValueError):
                raid_schedule.set_softres_url(path, 1, "http://softres.it/raid/abc123")
            with self.assertRaises(ValueError):
                raid_schedule.set_softres_url(path, 1, "https://example.com/raid/abc123")
            with self.assertRaises(ValueError):
                raid_schedule.set_softres_url(path, 999, "https://softres.it/raid/abc123")

    async def test_schedule_connections_are_closed_even_on_errors(self):
        """All raid schedule reads/writes must release SQLite handles on Windows."""
        class TrackedConnection(sqlite3.Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.was_closed = False

            def close(self):
                self.was_closed = True
                super().close()

        original_connect = sqlite3.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            connection = original_connect(
                *args, factory=TrackedConnection, **kwargs
            )
            opened.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with patch.object(raid_schedule.sqlite3, "connect",
                              side_effect=tracked_connect):
                raid_schedule.ensure_raid_fields(path)
                raid_schedule.save_raid(
                    path, 1, "2026-11-05 20:00", "Molten Core"
                )
                raid_schedule.read_raid(path, 1)
                raid_schedule.latest_raid(path)
                raid_schedule.active_panels(path)
                raid_schedule.record_panel(path, 1, raid.TEST_CHANNEL_ID, 7654)
                raid_schedule.set_softres_url(
                    path, 1, "https://softres.it/raid/abc123"
                )
                with self.assertRaises(ValueError):
                    raid_schedule.set_softres_url(
                        path, 999, "https://softres.it/raid/abc123"
                    )
            self.assertTrue(opened)
            self.assertTrue(all(con.was_closed for con in opened),
                            "A raid scheduling SQLite connection was left open")

    async def test_uk_time_is_discord_localised_and_validated(self):
        summer = raid_schedule.parse_uk_datetime("2026-07-01 20:00")
        winter = raid_schedule.parse_uk_datetime("2026-11-01 20:00")
        self.assertEqual(summer.utcoffset().total_seconds(), 3600)
        self.assertEqual(winter.utcoffset().total_seconds(), 0)
        self.assertIn("<t:", raid_schedule.format_discord_date(summer.isoformat()))
        with self.assertRaises(ValueError):
            raid_schedule.parse_uk_datetime("tomorrow")
        with self.assertRaises(ValueError):
            raid_schedule.parse_uk_datetime("2026-03-29 01:30")
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            with self.assertRaises(ValueError):
                raid_schedule.save_raid(path, 1, "2026-11-05 20:00", "MC", roster_announce="2026-11-06 20:00")

    async def test_live_panel_posts_normal_message_then_edits_same_message(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(raid, "RAID_DB", str(Path(tmp) / "raid.db")), \
             patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            raid.create_schema(raid.RAID_DB)
            raid_schedule.ensure_raid_fields(raid.RAID_DB)
            cog = raid.RaidPrototype(SimpleNamespace())
            response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
            message = SimpleNamespace(id=7654, jump_url="https://discord.com/channels/77/55/7654", edit=AsyncMock())
            channel = SimpleNamespace(send=AsyncMock(return_value=message),
                                      fetch_message=AsyncMock(return_value=message))
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
                channel=channel, followup=SimpleNamespace(send=AsyncMock()),
            )
            await cog.raid_setup.callback(cog, interaction, 2, "2026-11-05 20:00", "Molten Core")
            response.defer.assert_awaited_once_with(ephemeral=True)
            kwargs = channel.send.await_args.kwargs
            self.assertIsInstance(kwargs["view"], raid_schedule.RaidEventPanel)
            self.assertTrue(kwargs["view"].is_persistent())
            self.assertEqual(raid_schedule.read_raid(raid.RAID_DB, 2)["panel_message_id"], 7654)
            self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
            await cog.raid_setup.callback(cog, interaction, 2, "2026-11-05 20:00", "Onyxia")
            channel.send.assert_awaited_once()
            message.edit.assert_awaited_once()
            self.assertIn("Onyxia", json.dumps(message.edit.await_args.kwargs["view"].to_components()))
            response.send_message.reset_mock()
            interaction.channel_id = raid.RAID_CHANNEL_ID
            await cog.raid_setup.callback(cog, interaction, 3, "2026-11-06 20:00", "Onyxia")
            self.assertNotIn("view", response.send_message.await_args.kwargs)
            self.assertIsNone(raid_schedule.read_raid(raid.RAID_DB, 3))

    async def test_character_profile_can_be_updated_without_signup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid.save_character(42, "Snore Mon", "Mage", "Frost", "DPS", path)
            raid.save_character(42, "snore mon", "Mage", "Fire", "DPS", path)
            self.assertEqual(raid.list_characters(42, path),
                             [("Snore Mon", "Mage", "Fire", "DPS")])
            self.assertEqual(raid.list_characters(99, path), [])
            with raid.database(path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM signups").fetchone()[0], 0)
                self.assertEqual(con.execute("SELECT realm FROM characters").fetchone()[0], "")

    async def test_old_realm_record_is_updated_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with raid.database(path) as con:
                con.execute(
                    "INSERT INTO characters (discord_id,name,realm,class_name,spec,role) "
                    "VALUES (42,'Snore Mon','PvP','Mage','Frost','DPS')"
                )
                old_id = con.execute("SELECT character_id FROM characters").fetchone()[0]
            raid.save_character(42, "Snore Mon", "Mage", "Fire", "DPS", path)
            with raid.database(path) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM characters").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT character_id FROM characters").fetchone()[0], old_id)
            self.assertEqual(raid.list_characters(42, path),
                             [("Snore Mon", "Mage", "Fire", "DPS")])

    async def test_class_spec_role_validation(self):
        self.assertEqual(len(raid.CLASS_SPECS), 9)
        self.assertEqual(set(raid.CLASS_SPECS["Druid"]["Feral"]), {"Tank", "DPS"})
        self.assertEqual(raid.CLASS_SPECS["Mage"]["Frost"], ("DPS",))
        self.assertNotIn("Demon Hunter", raid.CLASS_SPECS)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            with self.assertRaises(ValueError):
                raid.save_character(42, "Snore", "Mage", "Frost", "Tank", path)
            with self.assertRaises(ValueError):
                raid.save_character(42, "Snore", "Mage", "Retribution", "DPS", path)

    async def test_registration_uses_smart_selects_and_name_only_modal(self):
        class_data = json.dumps(raid.ClassPickerView(42).to_components())
        spec_data = json.dumps(raid.SpecPickerView(42, "Druid").to_components())
        roles = json.dumps(
            raid.RolePickerView(42, "Druid", "Feral", ("Tank", "DPS")).to_components()
        )
        self.assertIn("Choose your class", class_data)
        self.assertIn("Choose your specialisation", spec_data)
        self.assertIn('"label": "Feral"', spec_data)
        self.assertIn('"label": "Tank"', roles)
        self.assertIn('"label": "DPS"', roles)
        modal = raid.CharacterNameModal(42, "Mage", "Frost", "DPS")
        self.assertEqual(len(modal.children), 1)
        self.assertEqual(modal.children[0].label, "Character name")

    async def test_character_lookup_command_is_private(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            cog = raid.RaidPrototype(SimpleNamespace())
            response = SimpleNamespace(send_message=AsyncMock())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
            )
            with patch.object(raid, "character_list_text", return_value="Snore Mon"):
                await cog.characters.callback(cog, interaction)
            self.assertTrue(response.send_message.await_args.kwargs["ephemeral"])
            self.assertIn("Snore Mon", response.send_message.await_args.args[0])
            response.send_message.reset_mock()
            interaction.channel_id = raid.RAID_CHANNEL_ID
            await cog.characters.callback(cog, interaction)
            self.assertNotIn("Snore Mon", response.send_message.await_args.args[0])

    async def test_loaded_but_public_channel_not_posted(self):
        source = (Path(__file__).resolve().parents[1] / "rollf.py").read_text()
        self.assertIn('await bot.load_extension("cogs.raid_signup")', source)
