import unittest
from unittest.mock import patch

from providers.stats import RunStats


class RunStatsTests(unittest.TestCase):
    def test_normalizes_cumulative_usage_and_separates_compaction(self):
        with patch("providers.stats.time.monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6, 7]):
            stats = RunStats(1000)
            main = stats.start({"messages": [{"role": "user", "content": "hello"}]})
            stats.chunk(main, {"choices": [{"delta": {"content": "ok"}}]})
            stats.chunk(main, {"usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                         "prompt_tokens_details": {"cached_tokens": 70},
                                         "completion_tokens_details": {"reasoning_tokens": 8}},
                               "timings": {"prompt_n": 30, "prompt_ms": 1000}})
            stats.finish(main)
            compact = stats.start({"messages": []}, compaction=True)
            stats.chunk(compact, {"choices": [{"delta": {"content": "summary"}}]})
            stats.chunk(compact, {"usage": {"prompt_tokens": 50, "completion_tokens": 10,
                                            "prompt_tokens_details": {"cached_tokens": 40},
                                            "completion_tokens_details": {"reasoning_tokens": 2}},
                                  "timings": {"prompt_n": 10, "prompt_ms": 500}})
            stats.finish(compact)
            result = stats.render()
        self.assertIn("Context:    100 / 1,000 (10.0%)", result)
        self.assertIn("Steps:      1", result)
        self.assertIn("Input:      40", result)
        self.assertIn("Output:     20", result)
        self.assertIn("Reasoning:  10", result)
        self.assertIn("Cache Read: 110", result)
        self.assertIn("Cache Rate: 73.3%", result)
        self.assertIn("Total:      180", result)
        self.assertIn("PP:         26.7 tok/s", result)

    def test_unknown_breakdowns_do_not_become_zero(self):
        stats = RunStats(-1)
        request = stats.start({"messages": [{"role": "user", "content": "hi"}]})
        stats.chunk(request, {"usage": {"prompt_tokens": 100, "completion_tokens": 20}})
        stats.finish(request)
        result = stats.render()
        self.assertIn("Context:    100 / N/A", result)
        self.assertIn("Input:      N/A", result)
        self.assertIn("Cache Read: N/A", result)
        self.assertIn("Reasoning:  N/A", result)
        self.assertIn("Output:     N/A", result)
        self.assertIn("Total:      120", result)
        self.assertIn("PP:         N/A", result)
