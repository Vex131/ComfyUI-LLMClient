import unittest

from providers import extract


class ParsePatternsTests(unittest.TestCase):
    def test_basic(self):
        pairs = extract.parse_patterns("positive = a\nnegative=b\nseed")
        self.assertEqual(pairs, [("positive", "a"), ("negative", "b"), ("seed", "")])

    def test_comments_and_blanks(self):
        pairs = extract.parse_patterns("# note\n\n   \na = 1\n")
        self.assertEqual(pairs, [("a", "1")])

    def test_first_equals_split(self):
        pairs = extract.parse_patterns("url = https://x.com/?a=1")
        self.assertEqual(pairs, [("url", "https://x.com/?a=1")])

    def test_empty_label_skipped(self):
        pairs = extract.parse_patterns("= orphan\nreal = 1")
        self.assertEqual(pairs, [("real", "1")])


class JsonExtractTests(unittest.TestCase):
    def test_plain_object(self):
        values = extract.extract_all('{"positive": "cat", "seed": 42}', "json",
                                     [("positive", ""), ("seed", ""), ("missing", "")])
        self.assertEqual(values, ["cat", "42", ""])

    def test_fenced(self):
        text = "Sure:\n```json\n{\"positive\": \"cat\"}\n```\n"
        self.assertEqual(extract.extract_all(text, "json", [("positive", "")]), ["cat"])

    def test_nested_path(self):
        text = '{"sampler": {"name": "euler", "cfg": 7.5}}'
        values = extract.extract_all(text, "json", [("cfg", "sampler.cfg"), ("name", "sampler.name")])
        self.assertEqual(values, ["7.5", "euler"])

    def test_bool_and_list(self):
        text = '{"flag": true, "list": [1, "a"]}'
        self.assertEqual(extract.extract_all(text, "json", [("flag", ""), ("list", "")]),
                         ["true", '[1, "a"]'])

    def test_invalid_json(self):
        with self.assertRaises(extract.ExtractError):
            extract.extract_all("not json", "json", [("a", "")])

    def test_non_object(self):
        with self.assertRaises(extract.ExtractError):
            extract.extract_all("[1,2]", "json", [("a", "")])


class RegexExtractTests(unittest.TestCase):
    def test_named_group(self):
        text = "POSITIVE: a cat\nNEGATIVE: ugly"
        values = extract.extract_all(text, "regex",
                                     [("pos", r"POSITIVE:\s*(?P<value>.*)")])
        self.assertEqual(values, ["a cat"])

    def test_group_one(self):
        values = extract.extract_all("seed = 123", "regex", [("seed", r"seed\s*=\s*(\d+)")])
        self.assertEqual(values, ["123"])

    def test_whole_match(self):
        values = extract.extract_all("steps: 30", "regex", [("s", r"\d+")])
        self.assertEqual(values, ["30"])

    def test_multiline_flag(self):
        text = "### START\na\nb\n### END"
        values = extract.extract_all(text, "regex",
                                     [("body", r"(?s)### START\n(?P<value>.*)\n### END")])
        self.assertEqual(values, ["a\nb"])

    def test_dot_not_greedy_across_lines(self):
        text = "POSITIVE: a cat\nNEGATIVE: ugly"
        values = extract.extract_all(text, "regex",
                                     [("pos", r"POSITIVE:\s*(?P<value>.*)")])
        self.assertEqual(values, ["a cat"])

    def test_no_match_empty(self):
        self.assertEqual(extract.extract_all("x", "regex", [("a", r"nope(\d)")]), [""])

    def test_missing_spec(self):
        with self.assertRaises(extract.ExtractError):
            extract.extract_all("x", "regex", [("a", "")])

    def test_invalid_regex(self):
        with self.assertRaises(extract.ExtractError):
            extract.extract_all("x", "regex", [("a", "(unclosed")])


if __name__ == "__main__":
    unittest.main()
