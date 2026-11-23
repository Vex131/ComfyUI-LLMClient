import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["COMFYUI_LLMCLIENT_BACKEND"] = "file"
os.environ["COMFYUI_LLMCLIENT_FILE_DIR"] = tempfile.mkdtemp(prefix="llmclient-credstore-boot-")

from providers import credstore


class CredStoreFileBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="llmclient-credstore-")
        os.environ["COMFYUI_LLMCLIENT_FILE_DIR"] = self.tmp
        self.store = importlib.reload(credstore)
        self.assertEqual(self.store.BACKEND, "file")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_set_get_delete_roundtrip(self):
        self.assertIsNone(self.store.get_secret("llm.http://a/v1"))
        self.store.set_secret("llm.http://a/v1", "secret-value-1")
        self.assertEqual(self.store.get_secret("llm.http://a/v1"), "secret-value-1")
        self.assertTrue(self.store.delete_secret("llm.http://a/v1"))
        self.assertIsNone(self.store.get_secret("llm.http://a/v1"))
        self.assertFalse(self.store.delete_secret("llm.http://a/v1"))

    def test_list_ids_prefix(self):
        self.store.set_secret("llm.http://a/v1", "k1")
        self.store.set_secret("llm.http://b/v1", "k2")
        self.store.set_secret("search.tavily_api_key", "k3")
        self.assertEqual(self.store.list_ids("llm."), ["llm.http://a/v1", "llm.http://b/v1"])
        self.assertEqual(self.store.list_ids("search."), ["search.tavily_api_key"])
        self.assertEqual(self.store.list_ids("missing."), [])

    def test_preview_masking(self):
        self.assertEqual(self.store.preview("llm.unset.example"), "")
        self.store.set_secret("llm.masked.example", "sk-proj-abcdef0123456789ab12")
        masked = self.store.preview("llm.masked.example")
        self.assertEqual(masked, "sk-pr…ab12")
        self.assertNotIn("proj-abcdef", masked)
        self.store.set_secret("llm.short.example", "abc")
        short = self.store.preview("llm.short.example")
        self.assertNotEqual(short, "abc")
        self.assertNotIn("bc", short)

    def test_empty_value_rejected(self):
        with self.assertRaises(ValueError):
            self.store.set_secret("llm.empty.example", "")
        with self.assertRaises(ValueError):
            self.store.set_secret("llm.none.example", None)

    def test_id_validation(self):
        for bad_id in ("", "has space", "semi;colon", "x" * 301, "quote'key", "back\\slash"):
            with self.assertRaises(ValueError):
                self.store.set_secret(bad_id, "value")

    def test_persistence_across_reload(self):
        self.store.set_secret("llm.persist.example", "persisted-value-1")
        reloaded = importlib.reload(credstore)
        self.assertEqual(reloaded.BACKEND, "file")
        self.assertEqual(reloaded.get_secret("llm.persist.example"), "persisted-value-1")
        self.assertTrue(reloaded.delete_secret("llm.persist.example"))
        self.assertIsNone(importlib.reload(credstore).get_secret("llm.persist.example"))


class CorruptStoreBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="llmclient-credstore-corrupt-")
        os.environ["COMFYUI_LLMCLIENT_FILE_DIR"] = self.tmp
        self.store = importlib.reload(credstore)
        self.assertEqual(self.store.BACKEND, "file")
        self.assertEqual(self.store.STORE_NOTICES, [])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_backup_and_notice(self, filename_prefix, garbage):
        self.assertEqual(len(self.store.STORE_NOTICES), 1)
        self.assertIn("unreadable store file preserved", self.store.STORE_NOTICES[0])
        backups = [name for name in os.listdir(self.tmp)
                   if name.startswith(filename_prefix) and ".corrupt" in name]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.tmp, backups[0]), "rb") as f:
            self.assertEqual(f.read(), garbage)
        return backups[0]

    def test_plain_file_corruption_is_backed_up_and_noticed(self):
        self.store.set_secret("llm.plain.example", "keep-me")
        with open(os.path.join(self.tmp, "secrets.json"), "wb") as f:
            f.write(b"not-json-garbage")
        self.assertIsNone(self.store.get_secret("llm.plain.example"))
        backup = self._assert_backup_and_notice("secrets.json.", b"not-json-garbage")
        self.store.set_secret("llm.plain.example", "fresh-value")
        self.assertEqual(self.store.get_secret("llm.plain.example"), "fresh-value")
        with open(os.path.join(self.tmp, backup), "rb") as f:
            self.assertEqual(f.read(), b"not-json-garbage")

    @unittest.skipUnless(os.name == "nt", "DPAPI store is Windows-only")
    def test_dpapi_file_corruption_is_backed_up_and_noticed(self):
        store = self.store._FileStore(self.tmp, "win-dpapi-file", dpapi=True)
        store.start()
        store.set_secret("llm.dpapi.example", "dpapi-secret-1")
        with open(os.path.join(self.tmp, "secrets.bin"), "wb") as f:
            f.write(b"garbage-not-a-dpapi-blob")
        self.assertIsNone(store.get_secret("llm.dpapi.example"))
        backup = self._assert_backup_and_notice("secrets.bin.", b"garbage-not-a-dpapi-blob")
        store.set_secret("llm.dpapi.example", "dpapi-secret-2")
        self.assertEqual(store.get_secret("llm.dpapi.example"), "dpapi-secret-2")
        self.assertEqual([name for name in os.listdir(self.tmp) if name.endswith(".tmp")], [])


@unittest.skipUnless(os.name == "nt" and os.environ.get("COMFYUI_LLMCLIENT_RUN_CREDMAN") == "1",
                     "set COMFYUI_LLMCLIENT_RUN_CREDMAN=1 on Windows to run the credential manager smoke test")
class CredManSmokeTests(unittest.TestCase):
    def test_credman_roundtrip_in_subprocess(self):
        script = (
            "import os;"
            "os.environ['COMFYUI_LLMCLIENT_BACKEND'] = 'credman';"
            "from providers import credstore;"
            "assert credstore.BACKEND == 'win-credential-manager', credstore.BACKEND;"
            "credstore.set_secret('probe.smoke', 'smoke-value');"
            "assert credstore.get_secret('probe.smoke') == 'smoke-value', 'readback';"
            "assert credstore.delete_secret('probe.smoke') is True, 'delete';"
            "assert credstore.get_secret('probe.smoke') is None, 'gone';"
            "print('CREDMAN-OK')"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=_REPO_ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CREDMAN-OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
