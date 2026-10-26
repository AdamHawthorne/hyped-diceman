"""Forever role V2 leadership preview regression tests."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "cogs" / "forever_role.py").read_text(encoding="utf-8")
ROLLF = (ROOT / "rollf.py").read_text(encoding="utf-8")


class ForeverLeadershipPreviewTests(unittest.TestCase):
    def test_postroles_sends_v2_without_legacy_embed(self):
        command = next(
            node for node in ast.walk(ast.parse(ROLLF))
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "postroles_admin"
        )
        sends = [
            node for node in ast.walk(command)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "send_message"
            and any(kw.arg == "view" for kw in node.keywords)
        ]
        self.assertEqual(len(sends), 1)
        keywords = {kw.arg: kw.value for kw in sends[0].keywords}
        self.assertNotIn("embed", keywords)
        self.assertNotIn("ephemeral", keywords)
        self.assertEqual(keywords["view"].func.id, "ForeverRoleV2Panel")

    def test_v2_handler_registered_on_startup(self):
        startup = next(
            node for node in ast.walk(ast.parse(ROLLF))
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_ready"
        )
        registrations = {
            node.args[0].func.id
            for node in ast.walk(startup)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_view"
            and node.args and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Name)
        }
        self.assertIn("ForeverRoleV2Panel", registrations)
        self.assertIn("ForeverRoleView", registrations)

    def test_existing_live_panel_remains_available(self):
        self.assertIn("class ForeverRoleView", SOURCE)
        self.assertIn("def build_forever_role_embed", SOURCE)
        self.assertIn('name="postroles"', ROLLF)

    def test_v2_preview_uses_same_images_and_copy(self):
        self.assertIn('FOREVER_THUMBNAIL = "https://i.vgy.me/asI88C.png"', SOURCE)
        self.assertIn('FOREVER_IMAGE = "https://i.vgy.me/WGNGdr.png"', SOURCE)
        self.assertIn("## <Hyped> for WoW: Forever.", SOURCE)
        self.assertIn("Interested in helping out with Hyped?", SOURCE)
        self.assertIn("Send us a short leadership application below.", SOURCE)

    def test_buttons_and_interest_options_match_spec(self):
        self.assertIn('label="Forever Role"', SOURCE)
        self.assertIn('label="Leadership Application"', SOURCE)
        for option in (
            "Leading",
            "Prep",
            "Logs",
            "Recruitment",
            "Loot",
            "Boosting",
            "Coding",
            "Mentoring",
            "Admin",
            "Anything",
            "Other",
        ):
            self.assertIn(f'"{option}"', SOURCE)
        self.assertNotIn('"Officer"', SOURCE)

    def test_application_has_no_name_field(self):
        self.assertNotIn('label="Name"', SOURCE)
        self.assertIn('label="Experience"', SOURCE)
        self.assertIn('label="Anything to add?"', SOURCE)
        self.assertIn('label="What are you interested in?"', SOURCE)
        self.assertIn("required=False", SOURCE)

    def test_landing_plan_copy_is_present(self):
        for text in (
            "**The plan:**",
            "**Horde PvP**",
            "**Main raid:** Sunday, 8pm - 11pm UK, adding Wednesday for the first two weeks of new releases",
        ):
            self.assertIn(text, SOURCE)

    def test_landing_info_uses_small_text_bullets(self):
        self.assertIn('"**Click the button below** to get access to the Forever channels.\\n"', SOURCE)
        self.assertNotIn('-# • **Click the button below** to get access to the Forever channels.', SOURCE)
        self.assertIn('-# • Click it again at any time to remove the role.', SOURCE)
        self.assertNotIn('**First Wednesday:**', SOURCE)
        self.assertNotIn('**Second Wednesday:**', SOURCE)
        self.assertNotIn('**New content:**', SOURCE)
        self.assertNotIn('**Focus:**', SOURCE)

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

