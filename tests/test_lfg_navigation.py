"""Offline wizard navigation checks: Back, Next and multi-select retention."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SRC = Path(__file__).resolve().parents[1] / "cogs" / "lfg_preview.py"
TREE = ast.parse(SRC.read_text(encoding="utf-8"))
FUNCTIONS = {"next_step", "previous_step", "planned_options"}
NODES = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
CONTEXT = {
    "RACES": ("Orc", "Troll"),
    "RACE_CLASSES": {"Orc": ("Warrior", "Mage"), "Troll": ("Mage",)},
    "CLASS_ROLES": {"Warrior": ("Tank", "DPS"), "Mage": ("DPS",)},
    "START_TIMES": ("Launch 23:00 UK", "04:00 UK", "Later"),
    "FINISH_TIMES": ("01:00 UK", "05:00 UK", "Long session..."),
    "LONG_SESSION_TIMES": ("24 hours", "30 hours"),
    "INTENTIONS": ("Open / Flexible", "Solo Questing", "Group Questing (LFM)"),
    "selectable_playing_days": lambda start: ("Thu 5 Nov", "Fri 6 Nov"),
}
exec(compile(ast.Module(body=NODES, type_ignores=[]), str(SRC), "exec"), CONTEXT)


def state(cls="Warrior", roles="Tank, DPS", finish="05:00 UK"):
    return SimpleNamespace(
        start_time="04:00 UK",
        finish_time=finish,
        draft=SimpleNamespace(
            race="Orc",
            class_name=cls,
            role=roles,
            days_off="Thu 5 Nov, Fri 6 Nov",
            activity="Open / Flexible, Group Questing (LFM)",
        ),
    )


class WizardNavigationTests(unittest.TestCase):
    def test_steps_are_reversible_for_multirole_class(self):
        st = state()
        stages = ["race", "class", "roles", "start", "finish", "days", "intentions"]
        for before, after in zip(stages, stages[1:]):
            self.assertEqual(CONTEXT["next_step"](st, before), after)
            self.assertEqual(CONTEXT["previous_step"](st, after), before)

    def test_dps_class_skips_roles_both_directions(self):
        st = state(cls="Mage", roles="DPS")
        self.assertEqual(CONTEXT["next_step"](st, "class"), "start")
        self.assertEqual(CONTEXT["previous_step"](st, "start"), "class")

    def test_long_session_is_reversible(self):
        st = state(finish="Long session...")
        self.assertEqual(CONTEXT["next_step"](st, "finish"), "long")
        st.finish_time = "24 hours"
        self.assertEqual(CONTEXT["previous_step"](st, "days"), "long")
        self.assertEqual(CONTEXT["next_step"](st, "long"), "days")

    def test_multiselect_options_remain_preselected(self):
        st = state()
        options, selected = CONTEXT["planned_options"](st, "roles")
        self.assertEqual(set(selected), {"Tank", "DPS"})
        options, selected = CONTEXT["planned_options"](st, "days")
        self.assertEqual(set(selected), {"Thu 5 Nov", "Fri 6 Nov"})
        options, selected = CONTEXT["planned_options"](st, "intentions")
        self.assertEqual(set(selected), {"Open / Flexible", "Group Questing (LFM)"})


if __name__ == "__main__":
    unittest.main()
