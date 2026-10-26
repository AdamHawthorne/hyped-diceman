"""Forever role V2 leadership preview regression tests."""
from pathlib import Path
import ast
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from cogs.forever_role import (
        LEADERSHIP_INTERESTS, LeadershipInterestView, LeadershipApplicationModal,
    )
except ImportError:
    discord = None

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


@unittest.skipIf(discord is None, "discord.py runtime required")
class LeadershipInterestFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_interests_stay_selected_until_next(self):
        view = LeadershipInterestView(123)
        select = next(item for item in view.children if isinstance(item, discord.ui.Select))
        self.assertEqual(select.max_values, len(LEADERSHIP_INTERESTS))
        select._values = list(LEADERSHIP_INTERESTS)
        interaction = SimpleNamespace(response=AsyncMock())
        await select.callback(interaction)
        self.assertEqual(view.interests, LEADERSHIP_INTERESTS)
        self.assertTrue(all(option.default for option in select.options))
        interaction.response.defer.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()
        interaction.response.send_modal.assert_not_awaited()
        await view.next_button.callback(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertEqual(modal.interests, LEADERSHIP_INTERESTS)
        self.assertIsNotNone(modal.other_interest)

    async def test_next_requires_a_selection_and_owner(self):
        view = LeadershipInterestView(123)
        interaction = SimpleNamespace(response=AsyncMock(), user=SimpleNamespace(id=456))
        self.assertFalse(await view.interaction_check(interaction))
        await view.next_button.callback(interaction)
        interaction.response.send_modal.assert_not_awaited()

    async def test_submission_keeps_named_and_other_interests(self):
        modal = LeadershipApplicationModal(interest=("Leading", "Coding", "Other"))
        modal.other_interest._value = "Events"
        modal.experience._value = "Guild experience"
        modal.extra._value = ""
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        interaction = SimpleNamespace(
            response=AsyncMock(), user=SimpleNamespace(id=123),
            client=SimpleNamespace(get_channel=lambda channel_id: channel),
        )
        await modal.on_submit(interaction)
        post = channel.send.call_args.kwargs["view"]
        content = "\n".join(
            item.content for item in post.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("Leading, Coding, Other: Events", content)


if __name__ == "__main__":
    unittest.main()

