"""Countdown/cooldown tests requiring neither Discord nor a bot token."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from cogs.countdown_core import (
    parse_uk_datetime, upcoming_string, next_cycle, pre_reminder_due,
    due_reminder_due, render_key, validate_image_url,
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

    def test_human_countdown_rounds_seconds_to_next_minute(self):
        self.assertEqual(upcoming_string(610, 0), "11m")
        self.assertEqual(upcoming_string(7200, 0), "2h 00m")
        self.assertEqual(upcoming_string(90061, 0), "1d 01h 02m")
        self.assertEqual(upcoming_string(100, 100), "00m · Ready")

    def test_repeating_cooldowns_skip_missed_cycles(self):
        self.assertEqual(next_cycle(100, 3600, 100), 3700)
        self.assertEqual(next_cycle(100, 3600, 7400), 10900)
        self.assertEqual(next_cycle(100, 0, 7400), 100)
        self.assertEqual(next_cycle(100, 3600, 99), 100)

    def test_reminders_fire_once_per_target_not_every_minute(self):
        target = 3600
        self.assertFalse(pre_reminder_due(target, 1700, 30, 0))
        self.assertTrue(pre_reminder_due(target, 1800, 30, 0))
        self.assertFalse(pre_reminder_due(target, 1800, 30, target))
        self.assertFalse(pre_reminder_due(target, 3600, 30, 0))
        self.assertTrue(due_reminder_due(target, 3600, 0))
        self.assertFalse(due_reminder_due(target, 3600, target))

    def test_same_countdown_minute_does_not_trigger_another_edit(self):
        self.assertEqual(render_key(3650, 10, 1), render_key(3650, 20, 1))
        self.assertNotEqual(render_key(3650, 10, 1), render_key(3650, 71, 1))
        self.assertEqual(render_key(100, 500, 1), render_key(100, 700, 1))

    def test_only_public_https_image_urls(self):
        self.assertEqual(validate_image_url(None), "")
        self.assertEqual(
            validate_image_url("https://i.vgy.me/ABC123.png"),
            "https://i.vgy.me/ABC123.png",
        )
        for candidate in ("file:///private/file.png", "http://example.com/image.png",
                          "https://me:password@example.com/p.png"):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                validate_image_url(candidate)


class CountdownSafetyTests(unittest.TestCase):
    def test_separate_sqlite_schema_does_not_replace_other_features(self):
        tree = ast.parse(SOURCE)
        fn = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "init_db")
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "countdown.db"
            def connect():
                db = sqlite3.connect(filename)
                db.row_factory = sqlite3.Row
                return db
            scope = {"connect": connect}
            exec(compile(ast.Module(body=[fn], type_ignores=[]),
                         str(filename), "exec"), scope)
            scope["init_db"]()
            scope["init_db"]()
            with connect() as db:
                columns = {row[1] for row in
                           db.execute("PRAGMA table_info(countdowns)")}
                self.assertTrue({
                    "message_id", "owner_id", "target_ts", "repeat_seconds",
                    "pre_notified_target", "due_notified_target",
                }.issubset(columns))
                tables = {r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                self.assertEqual(tables, {"countdowns"})

    def test_owner_check_is_not_just_discord_default_permissions(self):
        self.assertIn("interaction.user.id != OWNER_ID", SOURCE)
        self.assertIn("interaction.guild_id != GUILD_ID", SOURCE)
        self.assertIn("@app_commands.default_permissions(manage_guild=True)", SOURCE)

    def test_preview_is_ephemeral_public_timer_posts_in_channel(self):
        self.assertIn("view=CountdownLayout(config, now=now, preview=True)", SOURCE)
        self.assertIn("ephemeral=True", SOURCE)
        self.assertIn("message = await channel.send(", SOURCE)
        self.assertIn("allowed_mentions=discord.AllowedMentions.none()", SOURCE)
        self.assertIn("await channel.fetch_message(existing", SOURCE)

    def test_worker_updates_existing_message_and_dms_only_owner(self):
        self.assertIn("get_partial_message(row[\"message_id\"]).edit(", SOURCE)
        self.assertIn("await owner.send(", SOURCE)
        self.assertIn("pre_notified_target", SOURCE)
        self.assertIn("due_notified_target", SOURCE)
        self.assertNotIn("await channel.send(content=", SOURCE)

    def test_countdown_is_registered_but_dice_and_lfg_are_untouched(self):
        rollf = (ROOT / "rollf.py").read_text(encoding="utf-8")
        self.assertIn('await bot.load_extension("cogs.countdown")', rollf)
        self.assertIn('await bot.load_extension("cogs.lfg_preview")', rollf)
        self.assertIn('countdown.db', SOURCE)
        self.assertNotIn('DROP TABLE', SOURCE)
        self.assertNotIn('bot.db', SOURCE)
        self.assertNotIn('lfg_board.db', SOURCE)


if __name__ == "__main__":
    unittest.main()
