import sqlite3
import unittest
from types import SimpleNamespace
from cogs.roll_reactions import RollReactions


class Message:
    def __init__(self, user):
        self.id = user
        self.author = SimpleNamespace(id=999)
        self.content = f'<@{user}> rolled **50** 🎲'
        self.reactions = []

    async def add_reaction(self, emoji):
        self.reactions.append(SimpleNamespace(emoji=emoji, me=True))

    async def remove_reaction(self, emoji, user):
        self.reactions = [r for r in self.reactions if not (r.emoji == emoji and r.me)]


class ReactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.execute('CREATE TABLE rolls (user_id, value, actor_type, rolled_at)')
        self.messages = []
        self.errors = []
        bot = SimpleNamespace(user=SimpleNamespace(id=999), get_channel=lambda _: self)
        self.tracker = RollReactions(bot, 1, lambda: self.con, lambda: (0, 100), self.errors.append)

    def tearDown(self):
        self.con.close()

    async def history(self, **kwargs):
        for m in self.messages:
            yield m

    def add(self, user, score, stamp=1):
        self.con.execute('INSERT INTO rolls VALUES (?, ?, ?, ?)', (user, score, 'user', stamp))
        m = Message(user)
        self.messages.append(m)
        return m

    def has(self, message, emoji):
        return any(r.emoji == emoji and r.me for r in message.reactions)

    async def test_transfer_ties_restart_and_preserve_member_reactions(self):
        a = self.add(1, 50)
        await self.tracker.sync()
        self.assertTrue(self.has(a, '👑'))
        self.assertFalse(self.has(a, '💩'))
        b = self.add(2, 90, 2)
        a.reactions.append(SimpleNamespace(emoji='👑', me=False))
        await self.tracker.sync()
        self.assertFalse(self.has(a, '👑'))
        self.assertTrue(self.has(a, '💩'))
        self.assertTrue(self.has(b, '👑'))
        self.assertTrue(any(not r.me for r in a.reactions))
        c = self.add(3, 90, 3)
        d = self.add(4, 10, 4)
        await self.tracker.sync()
        self.assertTrue(self.has(b, '👑'))
        self.assertFalse(self.has(c, '👑'))
        self.assertFalse(self.has(a, '💩'))
        self.assertTrue(self.has(d, '💩'))
        await self.tracker.sync()
        self.assertEqual(len(d.reactions), 1)
        self.assertEqual(self.errors, [])

    async def test_ignore_old_rolls_and_other_channels(self):
        a = self.add(1, 50)
        self.con.execute("INSERT INTO rolls VALUES (2, 100, 'user', 2)")
        self.add(3, 1, -1)
        await self.tracker.sync()
        self.assertTrue(self.has(a, '👑'))
        self.assertFalse(self.has(a, '💩'))

    async def test_permission_failure_is_contained(self):
        self.add(1, 50)
        async def fail(**kwargs):
            raise PermissionError('no history access')
            yield
        self.history = fail
        await self.tracker.sync()
        self.assertEqual(len(self.errors), 1)
