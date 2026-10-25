"""Forever role V2 leadership preview regression tests."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "cogs" / "forever_role.py").read_text(encoding="utf-8")
ROLLF = (ROOT / "rollf.py").read_text(encoding="utf-8")


class ForeverLeadershipPreviewTests(unittest.TestCase):
    def test_existing_live_panel_remains_available(self):
        self.assertIn("class ForeverRoleView", SOURCE)
        self.assertIn("def build_forever_role_embed", SOURCE)
        self.assertIn('name="postroles"', ROLLF)

    def test_v2_preview_uses_same_images_and_copy(self):
        self.assertIn('FOREVER_THUMBNAIL = "https://i.vgy.me/asI88C.png"', SOURCE)
        self.assertIn('FOREVER_IMAGE = "https://i.vgy.me/pJaxaB.png"', SOURCE)
        self.assertIn("## <Hyped> for WoW: Forever.", SOURCE)
        self.assertIn("Interested in helping out with Hyped?", SOURCE)
        self.assertIn("Send us a short leadership application below.", SOURCE)

    def test_buttons_and_interest_options_match_spec(self):
        self.assertIn('label="Forever Role"', SOURCE)
        self.assertIn('label="Leadership Application"', SOURCE)
        for option in (
            "Raid Leader",
            "Role Leader / Class Leader",
            "Recruitment",
            "Community",
            "Anything",
            "Other",
        ):
            self.assertIn(f'"{option}"', SOURCE)
        self.assertNotIn('"Officer"', SOURCE)

    def test_application_has_no_name_field(self):
        self.assertNotIn('label="Name"', SOURCE)
        self.assertIn('label="Experience"', SOURCE)
        self.assertIn('label="How could you help Hyped?"', SOURCE)
        self.assertIn('label="What are you interested in?"', SOURCE)

    def test_interest_dropdown_has_no_duplicate_prompt_line(self):
        self.assertNotIn('"What are you interested in?",\n                view=LeadershipInterestView', SOURCE)

    def test_application_destination_and_sender(self):
        self.assertIn("LEADERSHIP_APPLICATIONS_CHANNEL_ID = 1225020051418255370", SOURCE)
        self.assertIn('f"-# Sent by <@{int(applicant_id)}>"', SOURCE)
        self.assertIn("allowed_mentions=discord.AllowedMentions.none()", SOURCE)

    def test_preview_command_is_owner_only_and_ephemeral(self):
        self.assertIn('name="previewroles"', ROLLF)
        self.assertIn("interaction.user.id != OWNER_ID", ROLLF)
        self.assertIn("ForeverRoleV2Panel(", ROLLF)
        self.assertIn("ephemeral=True", ROLLF)

    def test_no_roll_database_changes(self):
        self.assertNotIn("sqlite3", SOURCE)
        self.assertNotIn("bot.db", SOURCE)
        self.assertNotIn("rolls", SOURCE.lower())


if __name__ == "__main__":
    unittest.main()
