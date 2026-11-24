import importlib.util
import math
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


def load_node():
    def field(name=None, **kwargs):
        return SimpleNamespace(name=name, **kwargs)

    io = SimpleNamespace(
        ComfyNode=object,
        Schema=lambda **kwargs: SimpleNamespace(**kwargs),
        NodeOutput=lambda *values: values,
        MatchType=SimpleNamespace(Template=lambda name: name, Input=field, Output=field),
        Combo=SimpleNamespace(Input=field),
        String=SimpleNamespace(Output=field),
    )
    comfy = ModuleType("comfy")
    management = ModuleType("comfy.model_management")
    management.unload_all_models = MagicMock()
    management.soft_empty_cache = MagicMock()
    comfy.model_management = management
    api = ModuleType("comfy_api")
    latest = ModuleType("comfy_api.latest")
    latest.io = io
    spec = importlib.util.spec_from_file_location(
        "free_models_test", Path(__file__).resolve().parents[1] / "nodes" / "free_models.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"comfy": comfy, "comfy.model_management": management,
                                 "comfy_api": api, "comfy_api.latest": latest}):
        spec.loader.exec_module(module)
    return module.ComfyFreeModels, management, module


class FreeModelsTests(unittest.TestCase):
    def setUp(self):
        self.node, self.management, self.module = load_node()

    def test_schema_matches_input_and_output_type(self):
        schema = self.node.define_schema()
        self.assertEqual(schema.inputs[0].name, "anything")
        self.assertEqual(schema.inputs[0].template, schema.outputs[0].template)
        self.assertEqual(schema.outputs[0].display_name, "anything")
        self.assertEqual(schema.inputs[1].default, "free_vram")
        self.assertEqual(schema.inputs[1].options, ["free_vram", "free_vram_and_ram"])
        self.assertEqual(schema.outputs[1].name, "status")

    def test_unloads_before_returning_unchanged_data(self):
        data = {"image": object()}
        events = []
        self.management.unload_all_models.side_effect = lambda: events.append("unload")
        self.management.soft_empty_cache.side_effect = lambda: events.append("cache")
        result, status = self.node.execute(data)
        self.assertIs(result, data)
        self.assertEqual(events, ["unload", "cache"])
        self.assertIn("external providers were not affected", status)

    def test_errors_are_not_reported_as_success(self):
        self.management.unload_all_models.side_effect = RuntimeError("unload failed")
        with self.assertRaisesRegex(RuntimeError, "unload failed"):
            self.node.execute("text")
        self.management.soft_empty_cache.assert_not_called()

    def test_each_run_invalidates_cache(self):
        self.assertTrue(math.isnan(self.node.fingerprint_inputs()))

    def test_ram_mode_collects_after_unload_before_cache(self):
        events = []
        self.management.unload_all_models.side_effect = lambda: events.append("unload")
        self.management.soft_empty_cache.side_effect = lambda: events.append("cache")
        data = object()
        with patch.object(self.module.gc, "collect", side_effect=lambda: events.append("collect")):
            result, status = self.node.execute(data, "free_vram_and_ram")
        self.assertIs(result, data)
        self.assertEqual(events, ["unload", "collect", "cache"])
        self.assertIn("may remain in RAM", status)

    def test_old_ram_mode_still_runs(self):
        with patch.object(self.module.gc, "collect") as collect:
            self.node.execute("text", "free_vram_and_ram_experimental")
        collect.assert_called_once()

    def test_default_mode_does_not_collect_or_clear_workflow_cache(self):
        with patch.object(self.module.gc, "collect") as collect:
            self.node.execute("text")
        collect.assert_not_called()

    def test_invalid_mode_has_no_side_effects(self):
        with self.assertRaisesRegex(ValueError, "Unknown cleanup"):
            self.node.execute("text", "unknown")
        self.management.unload_all_models.assert_not_called()
