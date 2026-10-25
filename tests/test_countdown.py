"""Countdown/reminder regression tests requiring neither Discord nor a bot token."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from cogs.countdown_core import (
    parse_uk_datetime, upcoming_string, remaining_hours_string, next_cycle,
    pre_reminder_due, due_reminder_due, render_key, validate_image_url,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "cogs" / "countdown.py").read_text(encoding="utf-8")


class CountdownClockTests(unittest.TestCase):
    def test_uk_winter_and_summer_are_stored_in_utc(self):
        winter = parse_uk_datetime("2026-11-04 23:00")
        summer = parse_uk_datetime("2026-07-01 19:00")
        self.assertEqual(datetime.fromtimestamp(winter, timezone.utc).hour, 23)
        self.assertEqual(datetime.fromtimestamp(summer, timezone.utc).hour, 18)

    def test_invalid_and_ambiguous_uk_times_are_rejected(self):
        for value in ("nonsense", "2026-10-25 01:30", "2026-03-29 01:30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_uk_datetime(value)

    def test_human_countdown_uses_day_hour_minute_format(self):
        self.assertEqual(upcoming_string(610, 0), "11m")
        self.assertEqual(upcoming_string(7200, 0), "2h 00m")
        self.assertEqual(upcoming_string(90061, 0), "1d 01h 02m")
        self.assertEqual(upcoming_string(100, 100), "00m · Ready")

    def test_legacy_total_hours_helper_is_unchanged(self):
        self.assertEqual(remaining_hours_string(90061, 0), "25h 02m remaining")

    def test_repeating_cooldowns_skip_missed_cycles(self):
        self.assertEqual(next_cycle(100, 3600, 100), 3700)
        self.assertEqual(next_cycle(100, 3600, 7400), 10900)
        self.assertEqual(next_cycle(100, 0, 7400), 100)

    def test_reminders_fire_once_per_target(self):
        target = 3600
        self.assertTrue(pre_reminder_due(target, 1800, 30, 0))
        self.assertFalse(pre_reminder_due(target, 1800, 30, target))
        self.assertTrue(due_reminder_due(target, 3600, 0))
        self.assertFalse(due_reminder_due(target, 3600, target))

    def test_render_key_changes_by_minute(self):
        self.assertEqual(render_key(3650, 10, 1), render_key(3650, 20, 1))
        self.assertNotEqual(render_key(3650, 10, 1), render_key(3650, 71, 1))

    def test_only_public_https_image_urls(self):
        self.assertEqual(validate_image_url(None), "")
        self.assertEqual(
            validate_image_url("https://i.vgy.me/ABC123.png"),
            "https://i.vgy.me/ABC123.png",
        )
        for candidate in (
            "file:///private/file.png",
            "http://example.com/image.png",
            "https://me:password@example.com/p.png",
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                validate_image_url(candidate)


class CountdownSafetyTests(unittest.TestCase):
    def test_schema_supports_multiple_reminders_and_is_isolated(self):
        tree = ast.parse(SOURCE)
        fn = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "init_db"
        )
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "countdown.db"

            def connect():
                db = sqlite3.connect(filename)
                db.row_factory = sqlite3.Row
                return db

            scope = {"connect": connect}
            exec(
                compile(ast.Module(body=[fn], type_ignores=[]), str(filename), "exec"),
                scope,
            )
            scope["init_db"]()
            scope["init_db"]()
            with connect() as db:
                tables = {
                    row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("countdown_reminders", tables)
                self.assertIn("countdown_reminder_subscribers", tables)
                reminder_columns = {
                    row[1] for row in db.execute(
                        "PRAGMA table_info(countdown_reminders)"
                    )
                }
                self.assertIn("id", reminder_columns)
                self.assertIn("channel_id", reminder_columns)
                subscriber_columns = {
                    row[1] for row in db.execute(
                        "PRAGMA table_info(countdown_reminder_subscribers)"
                    )
                }
                self.assertIn("reminder_id", subscriber_columns)

    def test_roll_database_is_never_opened_or_migrated(self):
        self.assertIn('"countdown.db"', SOURCE)
        self.assertNotIn('"bot.db"', SOURCE)
        self.assertNotIn("'bot.db'", SOURCE)
        self.assertNotIn("DROP TABLE", SOURCE)
        self.assertNotIn("DELETE FROM rolls", SOURCE)
        self.assertNotIn("daily_roll", SOURCE.lower())

    def test_owner_security_is_hard_check_plus_discord_filter(self):
        self.assertIn("interaction.user.id != OWNER_ID", SOURCE)
        self.assertIn("interaction.guild_id != GUILD_ID", SOURCE)
        self.assertIn("@app_commands.default_permissions(manage_guild=True)", SOURCE)

    def test_only_setup_and_edit_slash_commands_exist(self):
        command_lines = [
            line.strip() for line in SOURCE.splitlines()
            if "@app_commands.command" in line
        ]
        self.assertEqual(len(command_lines), 2)
        self.assertTrue(any('name="setup"' in line for line in command_lines))
        self.assertTrue(any('name="edit"' in line for line in command_lines))
        for removed in ("preview", "post", "testdm", "status", "stop", "remove"):
            self.assertNotIn(f'name="{removed}"', SOURCE)

    def test_public_card_is_reminder_then_event_date_space_countdown(self):
        self.assertIn('COUNTDOWN_ICON_URL = "https://i.vgy.me/MC2gD0.png"', SOURCE)
        self.assertIn('"## Reminder:\\n"', SOURCE)
        self.assertIn('f"**{event}**\\n"', SOURCE)
        self.assertIn('f"-# {date_line}\\n"', SOURCE)
        self.assertIn('"\\n"\n                f"**{remaining}**"', SOURCE)
        self.assertNotIn("Subtitle / description", SOURCE)
        self.assertNotIn("description TEXT", SOURCE)
        self.assertIn("upcoming_string(target, now)", SOURCE)

    def test_multiple_reminders_are_keyed_by_id_not_channel(self):
        self.assertIn("id INTEGER PRIMARY KEY AUTOINCREMENT", SOURCE)
        self.assertNotIn("channel_id INTEGER PRIMARY KEY", SOURCE)
        self.assertIn("WHERE reminder_id=? AND user_id=?", SOURCE)
        self.assertIn("WHERE id=?", SOURCE)
        self.assertIn("list_channel_timers", SOURCE)

    def test_public_dm_and_ping_are_per_reminder_and_persistent(self):
        self.assertIn('custom_id=f"hyped_countdown_{self.reminder_id}_{mode}_v2"', SOURCE)
        self.assertIn('label="DM" if mode == "dm" else "Ping"', SOURCE)
        self.assertIn("countdown_reminder_subscribers", SOURCE)
        self.assertIn("last_notified_target", SOURCE)
        self.assertIn("self.bot.add_view(", SOURCE)

    def test_ping_is_same_channel_and_self_deletes(self):
        self.assertIn('channel = self.bot.get_channel(row["channel_id"])', SOURCE)
        self.assertIn("users=True, roles=False, everyone=False", SOURCE)
        self.assertIn("delete_after=60", SOURCE)

    def test_setup_always_creates_and_edit_reopens_selector(self):
        self.assertIn('description="Create a new reminder"', SOURCE)
        self.assertIn('description="Edit or remove a reminder in this channel"', SOURCE)
        self.assertIn("class EditReminderSelect", SOURCE)
        self.assertIn("class SetupRemoveButton", SOURCE)
        self.assertIn("SetupDetailsModal(SetupState(self, interaction))", SOURCE)

    def test_date_picker_reaches_100_days(self):
        self.assertIn("for start in (0, 25, 50, 75)", SOURCE)
        self.assertIn("state.date_range_start + 25", SOURCE)
        self.assertIn("class SetupDateRangeSelect", SOURCE)

    def test_confirmation_routes_to_office_logs(self):
        self.assertIn('OFFICE_LOG_CHANNEL_ID = int(os.getenv("OFFICE_LOG_CHANNEL_ID", "0"))', SOURCE)
        self.assertIn('normalized == "office-logs"', SOURCE)
        self.assertIn("await self.state.cog.log_office(", SOURCE)
        self.assertIn("interaction.delete_original_response()", SOURCE)

    def test_countdown_extension_and_lfg_remain_registered(self):
        rollf = (ROOT / "rollf.py").read_text(encoding="utf-8")
        self.assertIn('await bot.load_extension("cogs.countdown")', rollf)
        self.assertIn('await bot.load_extension("cogs.lfg_preview")', rollf)


if __name__ == "__main__":
    unittest.main()
