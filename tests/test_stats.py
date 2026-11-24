import unittest
from unittest.mock import patch

from providers.stats import RunStats


class RunStatsTests(unittest.TestCase):
    def test_streamed_text_estimates_missing_breakdown(self):
        stats = RunStats()
        request = stats.start({"messages": []})
        stats.chunk(request, {"choices": [{"delta": {"content": "Hello ", "reasoning": "Think"}}]})
        stats.chunk(request, {"choices": [{"delta": {"content": "world!", "reasoning_content": " again"}}]})
        stats.chunk(request, {"usage": {"prompt_tokens": 10, "completion_tokens": 20}})
        stats.finish(request)
        result = stats.render()
        self.assertIn("Output:     ~3", result)
        self.assertIn("Reasoning:  ~3", result)
        self.assertIn("Total:      30", result)

    def test_output_estimate_excludes_tool_call_tokens(self):
        stats = RunStats()
        request = stats.start({"messages": []})
        stats.chunk(request, {"choices": [{"delta": {"content": "text", "tool_calls": [{"index": 0}]}}]})
        stats.finish(request)
        self.assertIn("Output:     ~1", stats.render())

    def test_tool_only_turn_does_not_hide_later_output(self):
        stats = RunStats()
        first = stats.start({"messages": []})
        stats.chunk(first, {"choices": [{"delta": {"tool_calls": [{"index": 0}]}}]})
        stats.finish(first)
        second = stats.start({"messages": []})
        stats.chunk(second, {"choices": [{"delta": {"content": "answer"}}]})
        stats.finish(second)
        self.assertIn("Output:     ~2", stats.render())

    def test_mixed_exact_and_estimated_counts_are_marked(self):
        stats = RunStats()
        first = stats.start({"messages": []})
        stats.chunk(first, {"usage": {"completion_tokens": 10,
                                     "completion_tokens_details": {"reasoning_tokens": 4}}})
        stats.finish(first)
        second = stats.start({"messages": []})
        stats.chunk(second, {"choices": [{"delta": {"content": "abcd", "reasoning": "abcdefgh"}}]})
        stats.finish(second)
        self.assertIn("Output:     ~7", stats.render())
        self.assertIn("Reasoning:  ~6", stats.render())

    def test_anchor_skips_requests_without_completed_usage(self):
        stats = RunStats(model_key="model")
        first = stats.start({"messages": [{"role": "user", "content": "first"}]})
        stats.chunk(first, {"usage": {"prompt_tokens": 42}})
        stats.finish(first)
        expected = stats.last_anchor()
        second = stats.start({"messages": [{"role": "user", "content": "second"}]})
        stats.finish(second)
        unfinished = stats.start({"messages": [{"role": "user", "content": "third"}]})
        stats.chunk(unfinished, {"usage": {"prompt_tokens": 100}})
        self.assertEqual(stats.last_anchor(), expected)

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
