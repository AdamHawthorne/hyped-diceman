"""Regression tests for atomic writes to the canonical roll database."""
import ast
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "rollf.py").read_text(encoding="utf-8")
UK = ZoneInfo("Europe/London")
FIXED_TS = int(datetime(2026, 10, 29, 12, 0, tzinfo=UK).timestamp())


def load_insert_roll(namespace):
    tree = ast.parse(SOURCE)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "insert_roll"
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "rollf.py", "exec"), namespace)
    return namespace["insert_roll"]


class RollTransactionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.executescript("""
            CREATE TABLE rolls (
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                value INTEGER NOT NULL,
                rolled_at INTEGER NOT NULL,
                actor_type TEXT NOT NULL,
                roll_date TEXT
            );
            CREATE UNIQUE INDEX idx_one_roll_per_day
            ON rolls(user_id, roll_date) WHERE actor_type='user';
            CREATE TABLE user_scores (
                user_id INTEGER PRIMARY KEY,
                score INTEGER NOT NULL DEFAULT 0,
                rolls INTEGER NOT NULL DEFAULT 0,
                best INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE daily_scores (
                user_id INTEGER,
                roll_date TEXT,
                score INTEGER NOT NULL,
                rolls INTEGER NOT NULL,
                PRIMARY KEY(user_id, roll_date)
            );
        """)
        namespace = {
            "sqlite3": sqlite3,
            "time": SimpleNamespace(time=lambda: FIXED_TS),
            "datetime": datetime,
            "timedelta": timedelta,
            "TZ": UK,
            "db": lambda: self.con,
        }
        self.insert_roll = load_insert_roll(namespace)

    def tearDown(self):
        self.con.close()

    def counts(self):
        return tuple(
            self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("rolls", "user_scores", "daily_scores")
        )

    def test_success_commits_canonical_and_derived_rows_together(self):
        self.assertTrue(self.insert_roll(10, "roller", 87, "user"))
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(
            self.con.execute(
                "SELECT score, rolls, best FROM user_scores WHERE user_id=10"
            ).fetchone(),
            (87, 1, 87),
        )

    def test_duplicate_user_roll_changes_nothing(self):
        self.assertTrue(self.insert_roll(10, "roller", 87, "user"))
        before = (
            self.con.execute("SELECT * FROM rolls").fetchall(),
            self.con.execute("SELECT * FROM user_scores").fetchall(),
            self.con.execute("SELECT * FROM daily_scores").fetchall(),
        )
        self.assertFalse(self.insert_roll(10, "roller", 99, "user"))
        after = (
            self.con.execute("SELECT * FROM rolls").fetchall(),
            self.con.execute("SELECT * FROM user_scores").fetchall(),
            self.con.execute("SELECT * FROM daily_scores").fetchall(),
        )
        self.assertEqual(after, before)

    def test_downstream_failure_rolls_back_the_new_roll(self):
        self.con.execute("""
            CREATE TRIGGER fail_daily_score
            BEFORE INSERT ON daily_scores
            BEGIN
                SELECT RAISE(ABORT, 'simulated aggregate failure');
            END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_roll(10, "roller", 87, "user")
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_bot_roll_is_still_one_per_uk_day(self):
        self.assertTrue(self.insert_roll(0, "Ogre", 77, "bot"))
        self.assertFalse(self.insert_roll(0, "Ogre", 99, "bot"))
        self.assertEqual(self.counts(), (1, 0, 0))


if __name__ == "__main__":
    unittest.main()
