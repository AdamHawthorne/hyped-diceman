import sqlite3
import unittest
from types import SimpleNamespace
from cogs.roll_reactions import RollReactions


class Message:
    def __init__(self, user):
        self.id = user
        self.author = SimpleNamespace(id=999)
        self.content = f'<@{user}> rolled **50** <:ogredice:1566126699043880992>'
        self.reactions = []

    async def add_reaction(self, emoji):
        self.reactions.append(SimpleNamespace(emoji=emoji, me=True))

    async def remove_reaction(self, emoji, user):
        self.reactions = [r for r in self.reactions if not (r.emoji == emoji and r.me)]

    async def edit(self, *, content):
        self.content = content


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
        m.id = len(self.messages) + 1
        self.messages.append(m)
        return m

    def has(self, message, emoji):
        return any(r.emoji == emoji and r.me for r in message.reactions)

    async def test_transfer_ties_restart_and_preserve_member_reactions(self):
        a = self.add(1, 50)
        await self.tracker.sync()
        self.assertTrue(self.has(a, '<:ogrecoin:1564336079430099035>'))
        self.assertFalse(self.has(a, '💩'))
        b = self.add(2, 90, 2)
        a.reactions.append(SimpleNamespace(emoji='👑', me=False))
        await self.tracker.sync()
        self.assertFalse(self.has(a, '<:ogrecoin:1564336079430099035>'))
        self.assertTrue(self.has(a, '💩'))
        self.assertTrue(self.has(b, '<:ogrecoin:1564336079430099035>'))
        self.assertTrue(any(not r.me for r in a.reactions))
        c = self.add(3, 90, 3)
        d = self.add(4, 10, 4)
        await self.tracker.sync()
        self.assertTrue(self.has(b, '<:ogrecoin:1564336079430099035>'))
        self.assertFalse(self.has(c, '<:ogrecoin:1564336079430099035>'))
        self.assertFalse(self.has(a, '💩'))
        self.assertTrue(self.has(d, '💩'))
        await self.tracker.sync()
        self.assertEqual(len(d.reactions), 1)
        self.assertEqual(self.errors, [])

    async def test_gamble_higher_moves_highest_icon_to_latest_message(self):
        base = self.add(1, 30, 1)
        contender = self.add(2, 80, 2)
        await self.tracker.sync()
        self.assertTrue(self.has(contender, "<:ogrecoin:1564336079430099035>"))
        self.con.execute(
            "INSERT INTO rolls VALUES (1, 96, 'gamble', 3)")
        extra = Message(1)
        extra.id = 3
        self.messages.append(extra)
        await self.tracker.sync()
        self.assertTrue(self.has(extra, "<:ogrecoin:1564336079430099035>"))
        self.assertFalse(self.has(base, "<:ogrecoin:1564336079430099035>"))
        self.assertFalse(self.has(contender, "<:ogrecoin:1564336079430099035>"))

    async def test_gamble_lower_does_not_replace_original_best(self):
        top = self.add(1, 93, 1)
        self.add(2, 80, 2)
        self.con.execute(
            "INSERT INTO rolls VALUES (1, 10, 'gamble', 3)")
        extra = Message(1)
        extra.id = 3
        self.messages.append(extra)
        await self.tracker.sync()
        self.assertFalse(self.has(top, "<:ogrecoin:1564336079430099035>"))
        self.assertTrue(self.has(extra, "<:ogrecoin:1564336079430099035>"))

    async def test_gamble_keeps_lowest_individual_roll_on_original_post(self):
        kaja = self.add(1, 15, 1)
        tom_free = self.add(2, 3, 2)
        await self.tracker.sync()
        self.assertTrue(self.has(tom_free, '💩'))
        self.assertFalse(self.has(kaja, '💩'))
        self.con.execute("INSERT INTO rolls VALUES (2, 35, 'gamble', 3)")
        tom_gamble = Message(2)
        tom_gamble.id = 3
        self.messages.append(tom_gamble)
        await self.tracker.sync()
        self.assertTrue(self.has(tom_free, '💩'))
        self.assertFalse(self.has(tom_gamble, '💩'))
        self.assertFalse(self.has(kaja, '💩'))
        self.assertEqual(self.errors, [])

    async def test_low_gamble_is_tagged_on_its_own_message(self):
        base = self.add(1, 90, 1)
        other = self.add(2, 30, 2)
        self.con.execute("INSERT INTO rolls VALUES (1, 2, 'gamble', 3)")
        extra = Message(1)
        extra.id = 3
        self.messages.append(extra)
        await self.tracker.sync()
        self.assertTrue(self.has(extra, '💩'))
        self.assertFalse(self.has(base, '💩'))
        self.assertFalse(self.has(other, '💩'))

    async def test_ignore_old_rolls_and_other_channels(self):
        a = self.add(1, 50)
        self.con.execute("INSERT INTO rolls VALUES (2, 100, 'user', 2)")
        self.add(3, 1, -1)
        await self.tracker.sync()
        self.assertTrue(self.has(a, '<:ogrecoin:1564336079430099035>'))
        self.assertFalse(self.has(a, '💩'))

    async def test_ogre_lowest_removes_player_poop(self):
        low = self.add(1, 20)
        high = self.add(2, 80, 2)
        await self.tracker.sync()
        self.assertTrue(self.has(low, '💩'))
        self.con.execute("INSERT INTO rolls VALUES (0, 14, 'bot', 3)")
        await self.tracker.sync()
        self.assertFalse(self.has(low, '💩'))
        self.assertFalse(self.has(high, '💩'))

    async def test_ogre_high_roll_moves_poop_to_lowest_player(self):
        sole = self.add(1, 50)
        await self.tracker.sync()
        self.assertFalse(self.has(sole, '💩'))
        self.con.execute("INSERT INTO rolls VALUES (0, 90, 'bot', 2)")
        await self.tracker.sync()
        self.assertTrue(self.has(sole, '💩'))

    async def test_ogre_ties_lowest_no_poop(self):
        low = self.add(1, 14)
        self.add(2, 80, 2)
        self.con.execute("INSERT INTO rolls VALUES (0, 14, 'bot', 3)")
        await self.tracker.sync()
        self.assertFalse(self.has(low, '💩'))

    async def test_legacy_unicode_dice_remains_eligible(self):
        old = self.add(1, 11, 1)
        old.content = '<@1> rolled **11** 🎲'
        higher = self.add(2, 92, 2)
        await self.tracker.sync()
        self.assertTrue(self.has(old, '💩'))
        self.assertTrue(self.has(higher, '<:ogrecoin:1564336079430099035>'))
        self.assertIn('<:ogredice:1566126699043880992>', old.content)
        self.assertNotIn('🎲', old.content)
        await self.tracker.sync()
        self.assertEqual(self.errors, [])

    async def test_permission_failure_is_contained(self):
        self.add(1, 50)
        async def fail(**kwargs):
            raise PermissionError('no history access')
            yield
        self.history = fail
        await self.tracker.sync()
        self.assertEqual(len(self.errors), 1)
