"""Optional onboarding assistance: generic cleanup and hostile LLM output parsing."""

import json
import unittest

import pathsetup  # noqa: F401
import assist as A


class TestGenericCleanup(unittest.TestCase):
    def test_splits_compact_camel_case_without_role_taxonomy(self):
        self.assertEqual(A.normalize_phrase("TechWriter"), "tech writer")
        self.assertEqual(A.normalize_phrase("APIWriter"), "api writer")

    def test_seed_search_terms_uses_only_target_paths(self):
        terms = A.seed_search_terms(["TechWriter", "TechWriter", "Docs/Code"])
        self.assertEqual(terms, ["tech writer", "docs code"])

    def test_search_term_guidance_warns_about_query_shape(self):
        notes = A.search_term_guidance(["writer"])
        self.assertTrue(any("Single-word" in n for n in notes))

        many = [f"term {i}" for i in range(A.MAX_SEARCH_TERMS + 1)]
        notes = A.search_term_guidance(many)
        self.assertTrue(any("is a lot" in n for n in notes))


class TestParseLlmSuggestions(unittest.TestCase):
    def test_valid_suggestions_are_cleaned_and_capped(self):
        raw = json.dumps({
            "target_paths": [{"text": "TechWriter", "reason": "matches your wording"}],
            "search_terms": [
                {"text": "APIWriter", "reason": "common board phrase"},
                {"text": "api writer", "reason": "duplicate"},
            ],
            "profile_notes": [{"text": "Prefer docs-as-code", "reason": "stated"}],
        })
        result = A.parse_llm_suggestions(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.target_paths[0].text, "Tech Writer")
        self.assertEqual(result.search_terms[0].text, "api writer")
        self.assertEqual(len(result.search_terms), 1)
        self.assertEqual(result.profile_notes[0].text, "Prefer docs-as-code")

    def test_bad_json_fails_closed(self):
        self.assertIsNone(A.parse_llm_suggestions("not json"))
        self.assertIsNone(A.parse_llm_suggestions("[]"))

    def test_empty_shape_fails_closed(self):
        self.assertIsNone(A.parse_llm_suggestions(json.dumps({"search_terms": []})))

    def test_long_lists_are_capped(self):
        raw = json.dumps({"search_terms": [f"term {i}" for i in range(100)]})
        result = A.parse_llm_suggestions(raw)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.search_terms), A.MAX_SEARCH_TERMS)


class TestOllamaSafety(unittest.TestCase):
    def test_refuses_non_local_base_without_network(self):
        ready, reason = A.ollama_model_ready("model", "https://example.com")
        self.assertFalse(ready)
        self.assertIn("not a local", reason)

    def test_llm_suggest_malformed_message_fails_closed(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"message":"not an object"}'

        orig = A.urllib.request.urlopen
        A.urllib.request.urlopen = lambda *_args, **_kw: FakeResponse()
        try:
            result, reason = A.llm_suggest(A.Answers(self_description="x"), "model")
        finally:
            A.urllib.request.urlopen = orig
        self.assertIsNone(result)
        self.assertIn("usable JSON", reason)


if __name__ == "__main__":
    unittest.main()
