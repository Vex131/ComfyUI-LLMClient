import tempfile
import unittest
import sys
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from providers import rag


class FakeModel:
    def encode(self, texts, **kwargs):
        return [[1.0, 0.0] if "apple" in text.lower() else [0.0, 1.0] for text in texts]


class DocumentSearchTests(unittest.TestCase):
    def test_search_ranks_sources_and_handles_empty_query(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "one.txt").write_text("Apple trees grow fruit.", encoding="utf-8")
            Path(directory, "two.json").write_text('{"topic": "bananas"}', encoding="utf-8")
            index = rag.DocumentIndex(directory, "", model=FakeModel())
            result = index.search("apple", 1)
            self.assertIn("one.txt", result)
            self.assertIn("Apple trees", result)
            self.assertNotIn("two.json", result)
            self.assertIn("non-empty", index.search(""))

    def test_rejects_empty_path_and_unsupported_file(self):
        with self.assertRaisesRegex(ValueError, "document file"):
            rag.DocumentIndex("", "", model=FakeModel())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "test.doc")
            path.write_text("legacy")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                rag.DocumentIndex(str(path), "", model=FakeModel())

    def test_chunk_overlap_and_empty_collection(self):
        chunks = list(rag._chunks("a" * 1600))
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0][-150:], chunks[1][:150])
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "empty.txt").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No extractable text"):
                rag.DocumentIndex(directory, "", model=FakeModel())

    def test_missing_model_path(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "doc.txt").write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "local directory"):
                rag.DocumentIndex(directory, str(Path(directory, "missing")))

    def test_model_discovery_and_safe_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "llm_embeddings")
            existing = Path(directory, "embeddings", "retriever")
            existing.mkdir(parents=True)
            (existing / "config.json").write_text("{}", encoding="utf-8")
            with patch.object(rag, "model_root", return_value=root):
                self.assertIn("Installed: embeddings/retriever", rag.model_options())
                self.assertEqual(rag.resolve_model("Installed: embeddings/retriever", ""), str(existing))
                self.assertEqual(rag.resolve_model(rag.DIRECT_MODEL, "local"), "local")
                with self.assertRaises(ValueError):
                    rag.resolve_model("Installed: embeddings/../secret", "")

    def test_downloaded_preset_marked_only_when_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "llm_embeddings")
            label, repo = next(iter(rag.PRESET_MODELS.items()))
            model = root / repo.split("/")[-1]
            model.mkdir(parents=True)
            with patch.object(rag, "model_root", return_value=root):
                (model / "config.json").write_text("{}")
                self.assertNotIn(label + rag.DOWNLOADED_SUFFIX, rag.model_options())
                for name in ("modules.json", "model.safetensors", "tokenizer_config.json"):
                    (model / name).write_text("test")
                self.assertIn(label + rag.DOWNLOADED_SUFFIX, rag.model_options())
                self.assertNotIn(label, rag.model_options())
                old_installed = "Installed: llm_embeddings/" + model.name
                self.assertNotIn(old_installed, rag.model_options())
                self.assertEqual(rag.model_aliases()[old_installed], label + rag.DOWNLOADED_SUFFIX)
                self.assertEqual(rag.model_aliases()[label], label + rag.DOWNLOADED_SUFFIX)
                self.assertEqual(rag.model_aliases()[rag.DOWNLOADED_PREFIX + label], label + rag.DOWNLOADED_SUFFIX)
                self.assertEqual(rag.resolve_model(label + rag.DOWNLOADED_SUFFIX, ""), str(model))
                self.assertEqual(rag.resolve_model(rag.DOWNLOADED_PREFIX + label, ""), str(model))

    def test_fingerprint_changes_with_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "doc.txt")
            path.write_text("first", encoding="utf-8")
            before = rag.document_fingerprint(str(path))
            path.write_text("different content", encoding="utf-8")
            self.assertNotEqual(before, rag.document_fingerprint(str(path)))

    def test_uploaded_path_is_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            folder_paths = SimpleNamespace(get_input_directory=lambda: directory)
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                self.assertEqual(rag.resolve_document_path("input:docs/a.txt"),
                                 str(Path(directory, "docs", "a.txt").resolve()))
                with self.assertRaises(ValueError):
                    rag.resolve_document_path("input:../outside.txt")

    def test_index_reused_and_rebuilt_when_content_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory, "documents")
            docs.mkdir()
            file = docs / "first.txt"
            file.write_text("alpha", encoding="utf-8")
            model = Path(directory, "model")
            model.mkdir()
            (model / "config.json").write_text("{}", encoding="utf-8")
            rag._index_cache.clear()
            with patch.object(rag, "DocumentIndex", side_effect=lambda *args: object()) as build:
                first = rag.load_index(str(docs), str(model))
                self.assertIs(first, rag.load_index(str(docs), str(model)))
                self.assertEqual(build.call_count, 1)
                stat = file.stat()
                file.write_text("bravo", encoding="utf-8")
                os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                second = rag.load_index(str(docs), str(model))
                self.assertIsNot(first, second)
                (docs / "new.json").write_text("{}", encoding="utf-8")
                third = rag.load_index(str(docs), str(model))
                self.assertIsNot(second, third)
                (docs / "new.json").unlink()
                self.assertIs(rag.load_index(str(docs), str(model)), second)
                (model / "config.json").write_text('{"changed":true}', encoding="utf-8")
                self.assertIsNot(rag.load_index(str(docs), str(model)), second)
                self.assertEqual(build.call_count, 4)


if __name__ == "__main__":
    unittest.main()
