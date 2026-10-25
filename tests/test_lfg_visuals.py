"""Test the conditional visual decorations without Discord or a bot token."""

import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "cogs" / "lfg_preview.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
HELPERS = {"show_lfm_emoji", "visible_board_entries", "format_roles", "guild_lfg_emoji"}
NODES = [
    node for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name in HELPERS
]
class FakeUtils:
    @staticmethod
    def get(items, **kwargs):
        return next((item for item in items if all(getattr(item, k) == v for k, v in kwargs.items())), None)


class FakeDiscord:
    utils = FakeUtils


CONTEXT = {
    "ROLE_EMOJIS": {"Tank": "🛡️", "Healer": "✚", "DPS": "⚔️"},
    "LFG_EMOJI_NAME": "LFG",
    "discord": FakeDiscord,
}
exec(compile(ast.Module(body=NODES, type_ignores=[]), str(SOURCE), "exec"), CONTEXT)


class LFGVisualTests(unittest.TestCase):
    def test_show_image_for_open_or_lfm_only(self):
        visible = (
            "Open / Flexible",
            "Flexible",  # Existing listings made before rename
            "Group Questing (LFM)",
            "Group Questing + Dungeons (LFM)",
            "Open / Flexible, Group Questing (LFM)",
        )
        hidden = (
            "Solo Questing",
            "Solo Questing + Dungeons (LFG/ Pugging)",
            "Full Group / Sorted",
            "Grinding (Dungeons, grind spots etc)",
            "Open / Flexible, Full Group / Sorted",
            "Open / Flexible, Solo Questing",
            "Group Questing (LFM), Solo Questing",
        )
        for plan in visible:
            with self.subTest(plan=plan):
                self.assertTrue(CONTEXT["show_lfm_emoji"](plan))
        for plan in hidden:
            with self.subTest(plan=plan):
                self.assertFalse(CONTEXT["show_lfm_emoji"](plan))

    def test_role_emojis_are_only_used_when_requested(self):
        self.assertEqual(CONTEXT["format_roles"]("Tank, Healer, DPS"), "Tank / Healer / DPS")
        decorated = CONTEXT["format_roles"]("Tank, Healer, DPS", icons=True)
        self.assertIn("🛡️ Tank", decorated)
        self.assertIn("✚ Healer", decorated)
        self.assertIn("⚔️ DPS", decorated)

    def test_hyped_lfg_emoji_is_looked_up_by_name(self):
        class Emoji:
            name = "LFG"

            def __str__(self):
                return "<:LFG:123456789>"

        class Guild:
            emojis = [Emoji()]

        self.assertEqual(CONTEXT["guild_lfg_emoji"](Guild()), "<:LFG:123456789>")
        self.assertEqual(CONTEXT["guild_lfg_emoji"](None), "")

    def test_inline_emoji_does_not_reduce_board_capacity(self):
        open_rows = [{"activity": "Open / Flexible"} for _ in range(8)]
        solo_rows = [{"activity": "Solo Questing"} for _ in range(8)]
        self.assertEqual(len(CONTEXT["visible_board_entries"](open_rows)), 7)
        self.assertEqual(len(CONTEXT["visible_board_entries"](solo_rows)), 7)
        self.assertEqual(len(CONTEXT["visible_board_entries"](open_rows[:6])), 6)
        self.assertEqual(len(CONTEXT["visible_board_entries"](open_rows[:1])), 1)


if __name__ == "__main__":
    unittest.main()
