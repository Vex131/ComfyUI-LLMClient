import unittest

from providers.context import Compactor, checkpoint_view, estimate_tokens, parse_buffer, usage_anchor, validate_budget


class ContextBudgetTests(unittest.TestCase):
    def test_buffer_percent_and_tokens(self):
        self.assertEqual(parse_buffer("15", 1000), 150)
        self.assertEqual(parse_buffer("15%", 1000), 150)
        self.assertEqual(parse_buffer("0.1%", 1001), 2)
        self.assertEqual(parse_buffer("4096", 10000), 4096)

    def test_invalid_buffer(self):
        for value in ("0", "100", "100%", "-1", "101%", "101.5", "nan", "", "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_buffer(value, 1000)

    def test_budget(self):
        self.assertEqual(validate_budget(1000, 200, "15%"), 650)
        with self.assertRaises(ValueError):
            validate_budget(1000, 900, "15%")
        with self.assertRaises(ValueError):
            validate_budget(-1, 200, "15%")

    def test_checkpoint_preserves_transcript_and_stable_prefix(self):
        history = [{"role": "user", "content": "old"},
                   {"role": "assistant", "content": "answer"},
                   {"role": "user", "content": "recent"}]
        checkpoint = {"end": 2, "summary": "Older exchange"}
        before = checkpoint_view(history, checkpoint)
        history.append({"role": "assistant", "content": "new"})
        after = checkpoint_view(history, checkpoint)
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["content"], "old")

    def test_invalid_checkpoint_fails_closed(self):
        for checkpoint in ({"end": 2, "summary": "x"}, {"end": 1, "summary": ""},
                           {"end": True, "summary": "x"}):
            with self.subTest(checkpoint=checkpoint), self.assertRaises(ValueError):
                checkpoint_view([{"role": "user", "content": "x"}], checkpoint)


class CompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_usage_avoids_recompacting_stable_prefix(self):
        messages = [{"role": "system", "content": "Be concise."}]
        for i in range(3):
            messages.append({"role": "user", "content": str(i) + "purple bicycle garden apple. " * 16})
            if i < 2:
                messages.append({"role": "assistant", "content": "Noted."})
        anchor = usage_anchor(messages, None, ["server", "model"], 324)
        extended = messages + [{"role": "assistant", "content": "Noted."},
                               {"role": "user", "content": "3" + "purple bicycle garden apple. " * 16}]
        compactor = Compactor({"buffer": "30%", "keep_recent_tokens": 500},
                              {"max_context": 3200, "max_output_tokens": 128},
                              anchor=anchor, model_key=["server", "model"])
        called = []

        async def summarize(*args):
            called.append(True)
            return "summary"

        self.assertGreater(estimate_tokens(extended), compactor.budget)
        self.assertLess(compactor.estimate(extended), compactor.budget)
        self.assertEqual(await compactor.prepare(extended, summarize), extended)
        self.assertEqual(called, [])

    async def test_calibrated_context_still_compacts_when_budget_is_reached(self):
        previous = [{"role": "system", "content": "rules"},
                    {"role": "user", "content": "x" * 600}]
        messages = previous + [{"role": "assistant", "content": "y" * 500},
                               {"role": "user", "content": "z" * 900}]
        anchor = usage_anchor(previous, None, "model", 600)
        compactor = Compactor({"buffer": "30%", "keep_recent_tokens": 1200},
                              {"max_context": 3200, "max_output_tokens": 128},
                              anchor=anchor, model_key="model")
        calls = []

        async def summarize(view, allowance, tools):
            calls.append(allowance)
            return "Summary of the previous exchange."

        view = await compactor.prepare(messages, summarize)
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0], 1)
        self.assertIsNotNone(compactor.checkpoint)
        self.assertEqual(view[-1], messages[-1])

    def test_anchor_not_used_for_changed_prefix_model_or_tools(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "x" * 500}]
        anchor = usage_anchor(messages, [], "model-a", 10)
        compactor = Compactor({"buffer": "15%"}, {"max_context": 3000, "max_output_tokens": 100},
                              anchor=anchor, model_key="model-a")
        self.assertLess(compactor.estimate(messages), estimate_tokens(messages))
        self.assertEqual(compactor.estimate([messages[0], {"role": "user", "content": "y" * 500}]),
                         estimate_tokens(messages))
        compactor.model_key = "model-b"
        self.assertEqual(compactor.estimate(messages), estimate_tokens(messages))
        compactor.model_key = "model-a"
        self.assertGreater(compactor.estimate(messages, [{"name": "tool"}]), estimate_tokens(messages))

    async def test_stable_view_and_full_history(self):
        messages = [{"role": "system", "content": "instructions"}]
        for i in range(4):
            messages += [{"role": "user", "content": "question " + str(i) + "x" * 300},
                         {"role": "assistant", "content": "answer " + str(i) + "y" * 300}]
        messages.append({"role": "user", "content": "current"})
        compactor = Compactor({"buffer": "15%", "keep_recent_tokens": 500},
                              {"max_context": 3500, "max_output_tokens": 500})
        calls = []

        async def summarize(view, allowance, tools):
            calls.append((view, allowance))
            return "Earlier conversation summary."

        original = list(messages)
        view = await compactor.prepare(messages, summarize)
        self.assertEqual(messages, original)
        self.assertEqual(len(calls), 1)
        self.assertTrue(compactor.checkpoint)
        messages.append({"role": "assistant", "content": "reply"})
        next_view = await compactor.prepare(messages, summarize)
        self.assertEqual(next_view[:len(view)], view)
        self.assertEqual(len(calls), 1)
        self.assertLess(estimate_tokens(next_view), compactor.budget)

    async def test_no_retry_when_current_input_alone_is_large(self):
        compactor = Compactor({"buffer": "15%", "keep_recent_tokens": 100},
                              {"max_context": 1000, "max_output_tokens": 200})
        called = []

        async def summarize(*args):
            called.append(True)
            return "summary"

        with self.assertRaisesRegex(ValueError, "no complete older turn"):
            await compactor.prepare([{"role": "system", "content": "rules"},
                                     {"role": "user", "content": "x" * 1000}], summarize)
        self.assertEqual(called, [])

    async def test_agent_keeps_user_request_and_complete_tool_exchange(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "find information"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "first", "type": "function", "function": {"name": "search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "first", "content": "x" * 1300},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "second", "type": "function", "function": {"name": "search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "second", "content": "important"},
        ]
        compactor = Compactor({"buffer": "20%", "keep_recent_tokens": 500},
                              {"max_context": 2500, "max_output_tokens": 300})

        async def summarize(*args):
            return "First search found relevant information."

        view = await compactor.prepare(messages, summarize)
        self.assertIn(messages[1], view)
        self.assertIn(messages[4], view)
        self.assertIn(messages[5], view)
        self.assertNotIn(messages[2], view)
        self.assertNotIn(messages[3], view)
        self.assertEqual(len(messages), 6)

    async def test_react_tool_exchange_can_be_compacted(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "find information"},
            {"role": "assistant", "content": '{"tool":"search","args":{"query":"first"}}'},
            {"role": "user", "content": "TOOL_RESULT(search): " + "x" * 1600},
            {"role": "assistant", "content": '{"tool":"search","args":{}}'},
            {"role": "user", "content": "TOOL_RESULT(search): important"},
        ]
        compactor = Compactor({"buffer": "20%", "keep_recent_tokens": 500},
                              {"max_context": 2500, "max_output_tokens": 300})

        async def summarize(*args):
            return "First search found relevant information."

        view = await compactor.prepare(messages, summarize)
        self.assertIn(messages[1], view)
        self.assertIn(messages[4], view)
        self.assertIn(messages[5], view)
        self.assertNotIn(messages[2], view)
        self.assertNotIn(messages[3], view)
