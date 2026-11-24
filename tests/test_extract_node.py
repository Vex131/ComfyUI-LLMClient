import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from providers import extract


class ExecutionBlocker:
    def __init__(self, message):
        self.message = message


def load_node():
    io = types.SimpleNamespace(ComfyNode=object, Schema=object,
                               NodeOutput=lambda *values: values)
    modules = {
        "extract_test": types.ModuleType("extract_test"),
        "extract_test.nodes": types.ModuleType("extract_test.nodes"),
        "extract_test.providers": types.ModuleType("extract_test.providers"),
        "comfy_api": types.ModuleType("comfy_api"),
        "comfy_api.latest": types.ModuleType("comfy_api.latest"),
        "comfy_execution": types.ModuleType("comfy_execution"),
        "comfy_execution.graph_utils": types.ModuleType("comfy_execution.graph_utils"),
    }
    modules["extract_test.providers"].extract = extract
    modules["comfy_api.latest"].io = io
    modules["comfy_execution.graph_utils"].ExecutionBlocker = ExecutionBlocker
    spec = importlib.util.spec_from_file_location(
        "extract_test.nodes.extract", Path(__file__).parents[1] / "nodes" / "extract.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module.LLMPatternExtract


class ExtractNodeTests(unittest.TestCase):
    def test_invalid_json_continues_or_blocks(self):
        node = load_node()
        self.assertEqual(node.execute("not json", "json", "positive =")[0], "")
        result = node.execute("not json", "json", "positive =", "block")
        self.assertTrue(all(isinstance(value, ExecutionBlocker) for value in result))

    def test_partial_missing_blocks_and_valid_values_continue(self):
        node = load_node()
        result = node.execute('{"positive":"cat"}', "json", "positive =\nnegative =", "block")
        self.assertTrue(all(isinstance(value, ExecutionBlocker) for value in result))
        result = node.execute('{"positive":"cat","negative":"bad"}', "json",
                              "positive =\nnegative =", "block")
        self.assertEqual(result[:2], ("cat", "bad"))

    def test_bad_regex_remains_configuration_error(self):
        node = load_node()
        with self.assertRaises(ValueError):
            node.execute("text", "regex", "positive = (", "continue")

    def test_connected_text_overrides_text_box_including_empty_input(self):
        node = load_node()
        result = node.execute('{"positive":"box"}', "json", "positive =",
                              text_input='{"positive":"socket"}')
        self.assertEqual(result[0], "socket")
        result = node.execute('{"positive":"box"}', "json", "positive =", text_input="")
        self.assertEqual(result[0], "")


if __name__ == "__main__":
    unittest.main()
