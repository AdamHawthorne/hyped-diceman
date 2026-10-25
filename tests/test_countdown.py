"""Countdown/cooldown tests requiring neither Discord nor a bot token."""
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

    def test_human_countdown_rounds_seconds_to_next_minute(self):
        self.assertEqual(upcoming_string(610, 0), "11m")
        self.assertEqual(upcoming_string(7200, 0), "2h 00m")
        self.assertEqual(upcoming_string(90061, 0), "1d 01h 02m")
        self.assertEqual(upcoming_string(100, 100), "00m · Ready")

    def test_compact_total_hours_remaining_format(self):
        self.assertEqual(remaining_hours_string(90061, 0), "25h 02m remaining")
        self.assertEqual(remaining_hours_string(7200, 0), "2h 00m remaining")
        self.assertEqual(remaining_hours_string(3599, 0), "1h 00m remaining")
        self.assertEqual(remaining_hours_string(100, 100), "Ready!")

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
            scope = {
                "connect": connect,
                "COUNTDOWN_ICON_URL": "https://i.vgy.me/MC2gD0.png",
            }
            exec(compile(ast.Module(body=[fn], type_ignores=[]),
                         str(filename), "exec"), scope)
            scope["init_db"]()
            scope["init_db"]()
            with connect() as db:
                columns = {row[1] for row in
                           db.execute("PRAGMA table_info(countdowns)")}
                self.assertTrue({
                    "message_id", "owner_id", "description", "target_ts", "repeat_seconds",
                    "pre_notified_target", "due_notified_target",
                }.issubset(columns))
                tables = {r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                self.assertEqual(tables, {"countdowns", "countdown_subscribers"})
                sub_columns = {row[1] for row in
                               db.execute("PRAGMA table_info(countdown_subscribers)")}
                self.assertTrue({
                    "channel_id", "user_id", "delivery", "last_notified_target"
                }.issubset(sub_columns))

    def test_owner_check_is_not_just_discord_default_permissions(self):
        self.assertIn("interaction.user.id != OWNER_ID", SOURCE)
        self.assertIn("interaction.guild_id != GUILD_ID", SOURCE)
        self.assertIn("@app_commands.default_permissions(manage_guild=True)", SOURCE)

    def test_compact_card_uses_fixed_stopwatch_and_event_as_title(self):
        self.assertIn('COUNTDOWN_ICON_URL = "https://i.vgy.me/MC2gD0.png"', SOURCE)
        self.assertIn('f"## {title}\\n"', SOURCE)
        self.assertIn('f"-# {subtitle}\\n"', SOURCE)
        self.assertIn('f"**{remaining}**"', SOURCE)
        self.assertIn("upcoming_string(target, now)", SOURCE)
        self.assertIn("discord.ui.Thumbnail(", SOURCE)
        self.assertIn("COUNTDOWN_ICON_URL,", SOURCE)
        self.assertNotIn("## <Hyped> · Countdown / Cooldown", SOURCE)
        self.assertIn('image_url="Optional HTTPS artwork shown at the bottom"', SOURCE)
        self.assertIn("gallery.add_item(", SOURCE)
        self.assertIn("media=image_url", SOURCE)

    def test_public_card_hides_owner_reminder_and_uses_day_hour_minute_format(self):
        self.assertIn("upcoming_string(target, now)", SOURCE)
        self.assertNotIn("Owner reminder {config['remind_minutes']}m before", SOURCE)

    def test_optional_description_and_spacing_are_in_public_layout(self):
        self.assertIn("description TEXT NOT NULL DEFAULT ''", SOURCE)
        self.assertIn('ADD COLUMN description TEXT NOT NULL DEFAULT \'\'', SOURCE)
        self.assertIn('label="Subtitle / description (optional)"', SOURCE)
        self.assertIn('config.get("description", "")', SOURCE)
        self.assertIn('+ "\\n"\n                + f"**{remaining}**"', SOURCE)

    def test_friendly_setup_wizard_uses_dropdowns_not_raw_datetime_only(self):
        self.assertIn('@app_commands.command(name="setup"', SOURCE)
        self.assertIn("class SetupDetailsModal", SOURCE)
        self.assertIn("class SetupDateSelect", SOURCE)
        self.assertIn("class SetupHourSelect", SOURCE)
        self.assertIn("class SetupMinuteSelect", SOURCE)
        self.assertIn("class SetupReminderSelect", SOURCE)
        self.assertIn("class SetupRepeatSelect", SOURCE)
        self.assertIn("class SetupReviewLayout", SOURCE)
        self.assertIn("SetupPublishButton", SOURCE)

    def test_public_dm_and_ping_buttons_are_persistent_and_five_minutes(self):
        self.assertIn('custom_id=f"hyped_countdown_remind_{mode}_v1"', SOURCE)
        self.assertIn('label="DM" if mode == "dm" else "Ping"', SOURCE)
        self.assertIn('ReminderButton(channel_id or 0, "dm"', SOURCE)
        self.assertIn('ReminderButton(channel_id or 0, "ping"', SOURCE)
        self.assertNotIn("Remind me = Ogre DM 5 minutes before", SOURCE)
        self.assertIn("SUBSCRIBER_LEAD_SECONDS = 5 * 60", SOURCE)
        self.assertIn("Ogre bonk clock! Five minutes!", SOURCE)
        self.assertIn("countdown_subscribers", SOURCE)
        self.assertIn("last_notified_target", SOURCE)
        self.assertIn("delivery TEXT NOT NULL DEFAULT 'dm'", SOURCE)
        self.assertIn("self.bot.add_view(", SOURCE)

    def test_ping_reminder_mentions_then_self_deletes(self):
        self.assertIn('subscriber["delivery"] == "ping"', SOURCE)
        self.assertIn("allowed_mentions=discord.AllowedMentions(", SOURCE)
        self.assertIn("users=True, roles=False, everyone=False", SOURCE)
        self.assertIn("delete_after=60", SOURCE)

    def test_only_setup_is_exposed_and_private_review_keeps_admin_tools(self):
        commands = [
            line for line in SOURCE.splitlines()
            if "@app_commands.command" in line
        ]
        self.assertEqual(len(commands), 1)
        self.assertIn('name="setup"', commands[0])
        self.assertNotIn('name="preview"', SOURCE)
        self.assertNotIn('name="post"', SOURCE)
        self.assertNotIn('name="testdm"', SOURCE)
        self.assertNotIn('name="status"', SOURCE)
        self.assertNotIn('name="stop"', SOURCE)
        self.assertNotIn('name="remove"', SOURCE)
        self.assertIn("class SetupTestDMButton", SOURCE)
        self.assertIn("class SetupStopButton", SOURCE)
        self.assertIn("class SetupRemoveButton", SOURCE)
        self.assertIn("await interaction.user.send(", SOURCE)
        self.assertIn("Dev test from Hyped Ogre", SOURCE)

    def test_legacy_stopwatch_is_not_reused_as_bottom_artwork(self):
        self.assertIn("UPDATE countdowns SET image_url='' WHERE image_url=?", SOURCE)
        self.assertIn("UPDATE countdowns SET image_url='' WHERE image_url=?", SOURCE)

    def test_setup_preview_is_private_and_publish_posts_public_timer(self):
        self.assertIn("SetupReviewLayout", SOURCE)
        self.assertIn("preview=True", SOURCE)
        self.assertIn("disabled = preview or not active", SOURCE)
        self.assertIn("ephemeral=True", SOURCE)
        self.assertIn("message = await channel.send(", SOURCE)
        self.assertIn("allowed_mentions=discord.AllowedMentions.none()", SOURCE)
        self.assertIn("await channel.fetch_message(existing", SOURCE)


    def test_worker_updates_existing_message_and_dms_only_owner(self):
        self.assertIn("get_partial_message(row[\"message_id\"]).edit(", SOURCE)
        self.assertIn("await owner.send(", SOURCE)
        self.assertIn("await user.send(", SOURCE)
        self.assertIn("await channel.send(", SOURCE)
        self.assertIn("delivery", SOURCE)
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
