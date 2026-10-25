"""Offline interaction regression tests for public /postlfglfm recovery.

The real command methods are extracted from the source AST; Discord is faked,
so CI needs neither a bot token nor access to Discord or Fly.io.
"""

import ast
import copy
from pathlib import Path
import sqlite3
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock


SOURCE = Path(__file__).resolve().parents[1] / "cogs" / "lfg_preview.py"


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class HTTPException(Exception):
    pass


class TextChannel:
    def __init__(self, channel_id=101):
        self.id = channel_id
        self.messages = {}
        self.sent = []
        self.next_id = 7000
        self.lookup_error = None
        self.send_error = None

    async def send(self, *, view):
        if self.send_error:
            raise self.send_error
        message = SimpleNamespace(
            id=self.next_id, jump_url=f"https://discord.com/channels/77/101/{self.next_id}"
        )
        self.next_id += 1
        self.sent.append({"message": message, "view": view})
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id):
        if self.lookup_error:
            raise self.lookup_error
        try:
            return self.messages[message_id]
        except KeyError:
            raise NotFound("Previous message was deleted")


class InteractionResponse:
    def __init__(self):
        self.deferred = []

    async def defer(self, **kwargs):
        self.deferred.append(kwargs)


class Followup:
    def __init__(self):
        self.sent = []

    async def send(self, text, **kwargs):
        self.sent.append((text, kwargs))


class PublicBoardRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE lfg_boards (channel_id INTEGER PRIMARY KEY, guild_id INTEGER, "
            "message_id INTEGER, board_day TEXT, page_index INTEGER)"
        )
        self.db.execute(
            "CREATE TABLE lfg_listings (channel_id INTEGER, user_id INTEGER, notes TEXT)"
        )
        self.db.execute("INSERT INTO lfg_listings VALUES (101, 222, 'keep me')")
        self.channel = TextChannel()
        self.interaction = SimpleNamespace(
            guild_id=77,
            guild=object(),
            channel=self.channel,
            response=InteractionResponse(),
            followup=Followup(),
        )
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "LFGPreview")
        functions = [copy.deepcopy(n) for n in cls.body
                     if isinstance(n, ast.AsyncFunctionDef)
                     and n.name in {"post_new_board", "post_lfg_lfm"}]
        for fn in functions:
            fn.decorator_list = []
        scope = {
            "discord": SimpleNamespace(
                Interaction=object, TextChannel=TextChannel,
                NotFound=NotFound, Forbidden=Forbidden,
                HTTPException=HTTPException,
            ),
            "os": SimpleNamespace(environ={"HYPED_GUILD_ID": "77"}),
            "connect": lambda: self.db,
            "get_board": lambda channel_id: self.db.execute(
                "SELECT * FROM lfg_boards WHERE channel_id=?", (channel_id,)
            ).fetchone(),
            "BoardLayout": lambda *args, **kwargs: SimpleNamespace(
                is_public_board=True, page_index=kwargs.get("page_index", 0)
            ),
            "print": lambda *args, **kwargs: None,
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]),
                     str(SOURCE), "exec"), scope)
        self.cog = SimpleNamespace(refresh=AsyncMock())
        self.cog.post_new_board = MethodType(scope["post_new_board"], self.cog)
        self.cog.post_lfg_lfm = MethodType(scope["post_lfg_lfm"], self.cog)

    def tearDown(self):
        self.db.close()

    def stored(self):
        return self.db.execute("SELECT * FROM lfg_boards WHERE channel_id=101").fetchone()

    def saved_plan(self):
        return self.db.execute(
            "SELECT notes FROM lfg_listings WHERE channel_id=101 AND user_id=222"
        ).fetchone()[0]

    async def test_deleted_old_message_reposts_real_public_board_and_keeps_plans(self):
        self.db.execute("INSERT INTO lfg_boards VALUES (101,77,1234,'launch',2)")
        await self.cog.post_lfg_lfm(self.interaction)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(self.stored()["message_id"], 7000)
        self.assertEqual(self.stored()["page_index"], 2)
        self.assertTrue(self.channel.sent[0]["view"].is_public_board)
        self.assertEqual(self.channel.sent[0]["view"].page_index, 2)
        self.assertEqual(self.saved_plan(), "keep me")
        self.cog.refresh.assert_awaited_once_with(101)
        self.assertEqual(self.interaction.response.deferred,
                         [{"thinking": True, "ephemeral": True}])
        self.assertIn("Public LFG-LFM board ready", self.interaction.followup.sent[0][0])

    async def test_no_prior_board_posts_one_public_message(self):
        await self.cog.post_lfg_lfm(self.interaction)
        self.assertEqual(self.stored()["message_id"], 7000)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(self.saved_plan(), "keep me")

    async def test_existing_public_board_is_refreshed_not_duplicated(self):
        message = SimpleNamespace(id=4321, jump_url="https://discord.com/4321")
        self.channel.messages[4321] = message
        self.db.execute("INSERT INTO lfg_boards VALUES (101,77,4321,'launch',0)")
        await self.cog.post_lfg_lfm(self.interaction)
        self.assertEqual(len(self.channel.sent), 0)
        self.assertEqual(self.stored()["message_id"], 4321)
        self.cog.refresh.assert_awaited_once_with(101)
        self.assertIn("refreshed", self.interaction.followup.sent[0][0])

    async def test_temporary_fetch_error_does_not_duplicate_board(self):
        self.db.execute("INSERT INTO lfg_boards VALUES (101,77,4321,'launch',0)")
        self.channel.lookup_error = HTTPException("temporary API error")
        await self.cog.post_lfg_lfm(self.interaction)
        self.assertEqual(len(self.channel.sent), 0)
        self.assertEqual(self.stored()["message_id"], 4321)
        self.assertEqual(self.saved_plan(), "keep me")

    async def test_missing_during_refresh_is_recovered(self):
        message = SimpleNamespace(id=4321, jump_url="https://discord.com/4321")
        self.channel.messages[4321] = message
        self.db.execute("INSERT INTO lfg_boards VALUES (101,77,4321,'launch',1)")
        self.cog.refresh.side_effect = [NotFound("deleted mid-refresh"), None]
        await self.cog.post_lfg_lfm(self.interaction)
        self.assertEqual(self.stored()["message_id"], 7000)
        self.assertEqual(self.stored()["page_index"], 1)
        self.assertEqual(len(self.channel.sent), 1)
        self.assertEqual(self.saved_plan(), "keep me")

    def test_player_container_has_no_bottom_divider(self):
        text = SOURCE.read_text(encoding="utf-8")
        region = text[text.index("class BoardLayout("):text.index("class LFGPreview(")]
        self.assertNotIn("discord.ui.Separator()", region)
        self.assertIn("self.add_item(container)", region)

    def test_preview_remains_ephemeral_and_public_board_is_channel_send(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("msg = await channel.send(", text)
        self.assertIn("view=BoardLayout(self, interaction.channel_id,", text)
        self.assertIn("preview=True", text)
        self.assertIn("ephemeral=True", text)


if __name__ == "__main__":
    unittest.main()
