import asyncio
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers import memory


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = patch.object(memory, "session_directory", return_value=Path(self.tmp.name))
        self.directory.start()
        self.addCleanup(self.directory.stop)
        self.session = memory.new_session()

    async def test_persistence_and_isolation(self):
        async with memory.conversation("chat", self.session) as entry:
            memory.append(entry, [{"role": "user", "content": "hello"},
                                  {"role": "assistant", "content": "answer"}])
        async with memory.conversation("chat", self.session) as entry:
            self.assertEqual(memory.messages(entry)[0]["content"], "hello")
        async with memory.conversation("agent", self.session) as entry:
            self.assertEqual(memory.messages(entry), [])
        self.assertEqual(len(memory.list_sessions("chat", self.session)), 1)
        self.assertEqual(len(json.loads(memory._session_path("chat", self.session).read_text())["messages"]), 2)

    async def test_failed_run_does_not_save(self):
        with self.assertRaisesRegex(RuntimeError, "failure"):
            async with memory.conversation("chat", self.session) as entry:
                memory.append(entry, [{"role": "user", "content": "unsaved"}])
                raise RuntimeError("failure")
        self.assertFalse(memory._session_path("chat", self.session).exists())

    async def test_legacy_session_is_preserved(self):
        path = memory._session_path("chat", self.session)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"clear_version": 5, "messages": [{"role": "user", "content": "keep"}]}))
        async with memory.conversation("chat", self.session) as entry:
            self.assertEqual(memory.messages(entry)[0]["content"], "keep")
        stored = json.loads(path.read_text())
        self.assertEqual(stored["messages"][0]["content"], "keep")
        self.assertIn("created_at", stored)

    async def test_serializes_same_session(self):
        entered, release = asyncio.Event(), asyncio.Event()
        observed = []

        async def first():
            async with memory.conversation("chat", self.session) as entry:
                entered.set()
                await release.wait()
                memory.append(entry, [{"role": "user", "content": "first"}])

        async def second():
            await entered.wait()
            async with memory.conversation("chat", self.session) as entry:
                observed.extend(memory.messages(entry))

        a, b = asyncio.create_task(first()), asyncio.create_task(second())
        await entered.wait()
        await asyncio.sleep(0.1)
        self.assertEqual(observed, [])
        release.set()
        await asyncio.gather(a, b)
        self.assertEqual(observed[0]["content"], "first")

    async def test_fixed_seed_uses_normal_comfy_cache(self):
        before = memory.fingerprint(True, 7)
        async with memory.conversation("chat", self.session) as entry:
            memory.append(entry, [{"role": "user", "content": "first"}])
        self.assertEqual(before, memory.fingerprint(True, 7))
        self.assertTrue(math.isnan(memory.fingerprint(True, -1)))
        self.assertIs(memory.fingerprint(False), False)

    async def test_session_path_rejects_traversal(self):
        with self.assertRaises(ValueError):
            async with memory.conversation("chat", "../../outside"):
                pass

    async def test_session_listing_is_scoped_to_owner(self):
        owner = self.session.split(":")[0]
        other = memory.new_session()
        async with memory.conversation("chat", self.session):
            pass
        async with memory.conversation("chat", other):
            pass
        result = memory.list_sessions("chat", self.session)
        self.assertEqual([item["id"] for item in result], [self.session])
        self.assertIsInstance(result[0]["created_at"], float)
        self.assertIsInstance(result[0]["updated_at"], float)
        self.assertTrue(all(item["id"].startswith(owner + ":") for item in result))

    async def test_sessions_listed_by_last_activity(self):
        owner = self.session.split(":")[0]
        other = owner + ":" + memory.new_session().split(":")[1]
        async with memory.conversation("chat", self.session):
            pass
        async with memory.conversation("chat", other):
            pass
        os.utime(memory._session_path("chat", self.session), (10, 10))
        os.utime(memory._session_path("chat", other), (20, 20))
        self.assertEqual([item["id"] for item in memory.list_sessions("chat", self.session)],
                         [other, self.session])

    async def test_delete_removes_only_target_and_prevents_resurrection(self):
        owner = self.session.split(":")[0]
        other = owner + ":" + memory.new_session().split(":")[1]
        async with memory.conversation("chat", self.session) as entry:
            memory.append(entry, [{"role": "user", "content": "private"}])
        async with memory.conversation("chat", other) as entry:
            memory.append(entry, [{"role": "user", "content": "keep"}])
        await memory.delete_session("chat", self.session, self.session)
        self.assertFalse(memory._session_path("chat", self.session).exists())
        self.assertEqual([item["id"] for item in memory.list_sessions("chat", other)], [other])
        with self.assertRaisesRegex(ValueError, "deleted"):
            async with memory.conversation("chat", self.session):
                pass
        async with memory.conversation("chat", other) as entry:
            self.assertEqual(entry["messages"][0]["content"], "keep")
        await memory.delete_session("chat", other, self.session)

    async def test_delete_rejects_other_owners_and_unknown_sessions(self):
        other = memory.new_session()
        async with memory.conversation("chat", other):
            pass
        with self.assertRaisesRegex(ValueError, "different node"):
            await memory.delete_session("chat", self.session, other)
        self.assertTrue(memory._session_path("chat", other).exists())
        with self.assertRaises(ValueError):
            await memory.delete_session("chat", self.session, "../../outside")
        with self.assertRaises(FileNotFoundError):
            await memory.delete_session("chat", self.session, self.session)

    async def test_delete_waits_for_active_run(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def run():
            async with memory.conversation("agent", self.session) as entry:
                entered.set()
                await release.wait()
                memory.append(entry, [{"role": "user", "content": "running"}])

        task = asyncio.create_task(run())
        await entered.wait()
        deleting = asyncio.create_task(memory.delete_session("agent", self.session, self.session))
        await asyncio.sleep(0.1)
        self.assertFalse(deleting.done())
        release.set()
        await asyncio.gather(task, deleting)
        self.assertFalse(memory._session_path("agent", self.session).exists())
        with self.assertRaisesRegex(ValueError, "deleted"):
            async with memory.conversation("agent", self.session):
                pass

    async def test_failed_delete_leaves_session_available(self):
        async with memory.conversation("chat", self.session) as entry:
            memory.append(entry, [{"role": "user", "content": "keep"}])
        path = memory._session_path("chat", self.session)
        original_unlink = Path.unlink

        def fail_target(item, *args, **kwargs):
            if item == path:
                raise PermissionError("denied")
            return original_unlink(item, *args, **kwargs)

        with patch.object(Path, "unlink", fail_target):
            with self.assertRaises(PermissionError):
                await memory.delete_session("chat", self.session, self.session)
        self.assertTrue(path.exists())
        self.assertFalse(path.with_suffix(".deleted").exists())
        self.assertEqual([item["id"] for item in memory.list_sessions("chat", self.session)],
                         [self.session])
