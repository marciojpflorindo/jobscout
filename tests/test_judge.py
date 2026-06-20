"""Model-output validation for the judge: hostile LLM output must fail closed."""

import unittest

import pathsetup  # noqa: F401
import judge as J


class TestParseVerdict(unittest.TestCase):
    def test_valid(self):
        v = J.parse_verdict('{"verdict":"match","score":92,"disqualified":false,"why":"good"}')
        self.assertEqual(v["verdict"], "match")
        self.assertEqual(v["score"], 92)
        self.assertEqual(v["why"], "good")

    def test_bad_json_is_none(self):
        self.assertIsNone(J.parse_verdict("not json at all"))

    def test_non_dict_is_none(self):
        self.assertIsNone(J.parse_verdict("[1,2,3]"))
        self.assertIsNone(J.parse_verdict('"a string"'))

    def test_unknown_verdict_is_none(self):
        self.assertIsNone(J.parse_verdict('{"verdict":"definitely","score":50}'))

    def test_score_clamped(self):
        self.assertEqual(J.parse_verdict('{"verdict":"maybe","score":999}')["score"], 100)
        self.assertEqual(J.parse_verdict('{"verdict":"maybe","score":-5}')["score"], 0)

    def test_non_numeric_score_defaults_zero(self):
        self.assertEqual(J.parse_verdict('{"verdict":"maybe","score":"high"}')["score"], 0)

    def test_disqualified_forces_no(self):
        v = J.parse_verdict('{"verdict":"match","score":95,"disqualified":true}')
        self.assertEqual(v["verdict"], "no")
        self.assertTrue(v["disqualified"])

    def test_why_capped(self):
        v = J.parse_verdict('{"verdict":"no","score":0,"why":"%s"}' % ("x" * 500))
        self.assertLessEqual(len(v["why"]), 300)

    def test_verdict_case_insensitive(self):
        self.assertEqual(J.parse_verdict('{"verdict":"MATCH","score":80}')["verdict"], "match")


class TestRejectionBlock(unittest.TestCase):
    def test_empty_when_no_reasons(self):
        self.assertEqual(J._rejection_block([]), "")
        self.assertEqual(J._rejection_block(["", "  "]), "")

    def test_lists_reasons(self):
        block = J._rejection_block(["on-site only", "adtech"])
        self.assertIn("on-site only", block)
        self.assertIn("USER-REJECTED PATTERNS", block)

    def test_caps_reason_count(self):
        block = J._rejection_block([f"r{i}" for i in range(100)])
        self.assertEqual(block.count("\n- "), J.MAX_REJECTION_REASONS)


class TestJudgePromptComposition(unittest.TestCase):
    def test_system_prompt_includes_profile_and_contract(self):
        j = J.Judge("m", "http://127.0.0.1:11434", "MY PROFILE", ["adtech"])
        self.assertIn("MY PROFILE", j.system)
        self.assertIn("JSON object", j.system)
        self.assertIn("adtech", j.system)


if __name__ == "__main__":
    unittest.main()
