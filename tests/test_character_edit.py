"""Offline checks for partial LFG character updates."""
import ast
from pathlib import Path
import sqlite3
import unittest


class CharacterEditTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        path = root / 'cogs/lfg_preview.py'
        if not path.exists():
            path = Path(__file__).with_name('lfg_preview.py')
        tree = ast.parse(path.read_text())
        wanted = {'RACES', 'RACE_CLASSES', 'CLASS_ROLES', 'STARTING_ZONES'}
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.FunctionDef) and n.name == 'save_character_edit') or
                 (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                  and n.targets[0].id in wanted)]
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE lfg_listings (channel_id, user_id, race, starting_zone, class_name, role, notes, activity)')
        self.db.execute("INSERT INTO lfg_listings VALUES (1, 2, 'Orc', 'Durotar', 'Warrior', 'Tank', 'original', 'questing')")
        self.original = dict(self.row())
        from cogs.forever_races import RACE_CLASSES, STARTING_ZONES
        scope = {'connect': lambda: self.db, 'RACE_CLASSES': RACE_CLASSES,
                 'STARTING_ZONES': STARTING_ZONES}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
        self.save = scope['save_character_edit']

    def tearDown(self):
        self.db.close()

    def row(self):
        return self.db.execute('SELECT * FROM lfg_listings').fetchone()

    def test_preserves_unrelated_concurrent_changes(self):
        self.db.execute("UPDATE lfg_listings SET notes='new note'")
        self.assertTrue(self.save(1, 2, self.original, 'Tauren', 'Warrior', 'Tank'))
        self.assertEqual(self.row()['starting_zone'], 'Mulgore')
        self.assertEqual(self.row()['notes'], 'new note')
        self.assertEqual(self.row()['activity'], 'questing')

    def test_rejects_invalid_class_and_role(self):
        for race, cls, role in [('Tauren', 'Mage', 'DPS'), ('Orc', 'Mage', 'Tank')]:
            with self.assertRaises(ValueError):
                self.save(1, 2, self.original, race, cls, role)
        self.assertEqual(dict(self.row()), self.original)

    def test_rejects_stale_edit(self):
        self.assertTrue(self.save(1, 2, self.original, 'Orc', 'Mage', 'DPS'))
        self.assertFalse(self.save(1, 2, self.original, 'Tauren', 'Warrior', 'Tank'))
        self.assertEqual(self.row()['class_name'], 'Mage')

    def test_cannot_change_another_user_or_restore_removed_entry(self):
        self.assertFalse(self.save(1, 3, self.original, 'Orc', 'Mage', 'DPS'))
        self.db.execute('DELETE FROM lfg_listings')
        self.assertFalse(self.save(1, 2, self.original, 'Orc', 'Mage', 'DPS'))
        self.assertIsNone(self.row())
