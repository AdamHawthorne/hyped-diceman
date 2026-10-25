"""Offline checks for start-time-derived playing dates."""
import ast
from pathlib import Path
import unittest


class PlayingDaysTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'cogs/lfg_preview.py'
        if not path.exists():
            path = Path(__file__).with_name('lfg_preview.py')
        tree = ast.parse(path.read_text())
        functions = {'automatic_start_days', 'selectable_playing_days', 'merge_playing_days'}
        names = {'START_TIMES', 'LAUNCH_DAYS'}
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.FunctionDef) and n.name in functions) or
                 (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                  and n.targets[0].id in names)]
        cls.scope = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), cls.scope)

    def test_launch_includes_fourth_without_selecting_it(self):
        s = self.scope
        self.assertEqual(s['merge_playing_days']('Launch 23:00 UK', []), s['LAUNCH_DAYS'][0])
        self.assertNotIn(s['LAUNCH_DAYS'][0], s['selectable_playing_days']('Launch 23:00 UK'))
        self.assertIn(s['LAUNCH_DAYS'][1], s['selectable_playing_days']('Launch 23:00 UK'))

    def test_all_clock_starts_include_fifth_and_exclude_fourth(self):
        s = self.scope
        for start in s['START_TIMES'][1:-1]:
            with self.subTest(start=start):
                self.assertEqual(s['merge_playing_days'](start, [s['LAUNCH_DAYS'][0]]), s['LAUNCH_DAYS'][1])
                self.assertNotIn(s['LAUNCH_DAYS'][0], s['selectable_playing_days'](start))
                self.assertIn(s['LAUNCH_DAYS'][1], s['selectable_playing_days'](start))

    def test_additional_days_are_sorted_and_deduplicated(self):
        s = self.scope
        days = s['LAUNCH_DAYS']
        result = s['merge_playing_days']('00:00 UK', [days[4], days[2], days[4]])
        self.assertEqual(result, ', '.join([days[1], days[2], days[4]]))

    def test_later_requires_explicit_days_and_does_not_force_fifth(self):
        s = self.scope
        self.assertEqual(s['automatic_start_days']('Later'), ())
        self.assertEqual(s['merge_playing_days']('Later', [s['LAUNCH_DAYS'][3]]), s['LAUNCH_DAYS'][3])

    def test_changing_start_removes_stale_launch_day(self):
        s = self.scope
        days = s['LAUNCH_DAYS']
        self.assertEqual(s['merge_playing_days']('12:00 UK', [days[0], days[3]]), ', '.join([days[1], days[3]]))

    def test_thursday_only_can_be_submitted(self):
        s = self.scope
        thursday = s['LAUNCH_DAYS'][1]
        self.assertEqual(s['merge_playing_days']('12:00 UK', [thursday]), thursday)
        self.assertEqual(s['merge_playing_days']('Launch 23:00 UK', [thursday]),
                         ', '.join(s['LAUNCH_DAYS'][:2]))

    def test_all_start_options_offer_thursday_through_sunday(self):
        s = self.scope
        for start in s['START_TIMES']:
            self.assertEqual(s['selectable_playing_days'](start), tuple(s['LAUNCH_DAYS'][1:]))
