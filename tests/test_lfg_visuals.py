"""Test the conditional visual decorations without Discord or a bot token."""

import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "cogs" / "lfg_preview.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
HELPERS = {"show_lfm_emoji", "format_roles"}
NODES = [
    node for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name in HELPERS
]
from cogs.lfg_sort import is_open_lfm
from cogs.emojis import markup as guild_emoji, combat_role

CONTEXT = {
    "ROLE_EMOJIS": {"Tank": "🛡️", "Healer": "✚", "DPS": "⚔️"},
    "is_open_lfm": is_open_lfm,
    "guild_emoji": guild_emoji,
    "combat_role": combat_role,
}
exec(compile(ast.Module(body=NODES, type_ignores=[]), str(SOURCE), "exec"), CONTEXT)


class LFGVisualTests(unittest.TestCase):
    def test_show_open_marker_for_open_or_lfm_only(self):
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
        self.assertIn("Tank <:Tank:1565477012322132099>", decorated)
        self.assertIn("Healer <:Healer:1565477112951996456>", decorated)
        self.assertIn("DPS <:Melee:1565477048305197207>", decorated)

    def test_open_marker_after_plan_not_starting_zone(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('OPEN_LFM_MARKER = guild_emoji("MoveTo")', source)
        self.assertEqual(guild_emoji("MoveTo"), "<:MoveTo:1565477356326617183>")
        self.assertIn(
            'f"{part.strip()} {guild_emoji(combat_role(part.strip(), row[\'class_name\']))}"',
            source,
        )
        self.assertIn('f"{OPEN_LFM_MARKER} " if show_lfm_emoji(row["activity"]) else ""', source)
        self.assertIn('f"-# **Plan:** {open_marker}{safe(row[\'activity\'])}"', source)
        self.assertNotIn('f"-# **Starting Zone:** {zone}"', source)
        self.assertNotIn("guild_lfg_emoji", source)
        self.assertNotIn("LFG_EMOJI_NAME", source)

    def test_original_bottom_divider_asset(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('BOTTOM_IMAGE = "https://i.vgy.me/WGNGdr.png"', source)

    def test_open_marker_is_inside_same_v2_member_container(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("class BoardLayout(discord.ui.LayoutView)", source)
        self.assertIn("for row in visible:", source)
        self.assertIn("container = discord.ui.Container(accent_color=ACCENT)", source)
        self.assertNotIn("container.add_item(discord.ui.Separator())", source)
        self.assertNotIn("not_lfm_embed", source)
        self.assertNotIn("class EntryLayout", source)


if __name__ == "__main__":
    unittest.main()
