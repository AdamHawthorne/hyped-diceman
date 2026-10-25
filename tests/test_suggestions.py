"""Suggestions feature safety tests."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "cogs" / "suggestions.py").read_text(encoding="utf-8")


class SuggestionsSafetyTests(unittest.TestCase):
    def test_destination_channel_is_correct(self):
        self.assertIn('"1554621739017707591"', SOURCE)
        self.assertIn("SUGGESTIONS_DEST_CHANNEL_ID", SOURCE)

    def test_named_and_anonymous_buttons_are_persistent(self):
        self.assertIn('custom_id="hyped_suggestions_named_v1"', SOURCE)
        self.assertIn('custom_id="hyped_suggestions_anonymous_v1"', SOURCE)
        self.assertIn("self.bot.add_view(SuggestionsPanel(self))", SOURCE)

    def test_anonymous_submission_does_not_store_or_log_identity(self):
        self.assertNotIn("sqlite3", SOURCE)
        self.assertNotIn("DB_PATH", SOURCE)
        self.assertIn('print("ANONYMOUS SUGGESTION SUBMITTED"', SOURCE)
        self.assertIn("user_id=None if self.anonymous", SOURCE)
        self.assertIn("Leadership won't see who submitted it.", SOURCE)

    def test_named_submission_identifies_submitter_without_ping(self):
        self.assertIn('source = f"-# Submitted by <@{int(user_id)}>"', SOURCE)
        self.assertIn("allowed_mentions=discord.AllowedMentions.none()", SOURCE)

    def test_only_owner_can_post_panel(self):
        self.assertIn("interaction.user.id != OWNER_ID", SOURCE)
        self.assertIn("interaction.guild_id != GUILD_ID", SOURCE)
        self.assertIn("@app_commands.default_permissions(manage_guild=True)", SOURCE)
        commands = [
            line for line in SOURCE.splitlines()
            if "@app_commands.command" in line
        ]
        self.assertEqual(len(commands), 2)
        self.assertIn('name="preview"', SOURCE)
        self.assertIn('name="setup"', SOURCE)

    def test_preview_is_ephemeral_and_uses_real_panel(self):
        self.assertIn("Preview only — submissions still go to the private leadership channel.", SOURCE)
        self.assertIn("view=SuggestionsPanel(self)", SOURCE)
        self.assertIn("ephemeral=True", SOURCE)

    def test_feature_does_not_touch_roll_database(self):
        self.assertNotIn("bot.db", SOURCE)
        self.assertNotIn("rolls", SOURCE.lower())
        rollf = (ROOT / "rollf.py").read_text(encoding="utf-8")
        self.assertIn('await bot.load_extension("cogs.suggestions")', rollf)


if __name__ == "__main__":
    unittest.main()
