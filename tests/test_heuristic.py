"""Heuristic prefilter: keyword scoring and threshold-based ranking."""

import unittest

import pathsetup  # noqa: F401
import heuristic as H


class TestKeywords(unittest.TestCase):
    def test_drops_stopwords_and_dedups(self):
        kws = H._keywords(["senior backend engineer", "backend platform"])
        self.assertIn("backend", kws)
        self.assertIn("platform", kws)
        self.assertNotIn("senior", kws)      # stopword
        self.assertNotIn("engineer", kws)    # stopword
        self.assertEqual(len(kws), len(set(kws)))  # no dupes

    def test_keeps_whole_phrase(self):
        self.assertIn("backend platform", H._keywords(["backend platform"]))


class TestScore(unittest.TestCase):
    def test_title_weighs_more_than_description(self):
        kws = ["backend"]
        self.assertEqual(H.score("Backend role", "", kws), H.TITLE_HIT)
        self.assertEqual(H.score("", "needs backend", kws), H.DESC_HIT)
        self.assertGreater(H.TITLE_HIT, H.DESC_HIT)

    def test_no_match_zero(self):
        self.assertEqual(H.score("Sales rep", "cold calling", ["backend"]), 0)


class TestRank(unittest.TestCase):
    def test_drops_below_threshold_and_orders(self):
        jobs = [
            {"title": "Backend platform engineer", "description": "backend platform"},
            {"title": "Backend role", "description": ""},
            {"title": "Barista", "description": "coffee"},
        ]
        ranked = H.rank(jobs, ["backend platform"], top_n=10)
        self.assertEqual(len(ranked), 2)  # barista dropped
        self.assertGreaterEqual(ranked[0]["_score"], ranked[1]["_score"])

    def test_respects_top_n(self):
        jobs = [{"title": "backend", "description": ""} for _ in range(5)]
        self.assertEqual(len(H.rank(jobs, ["backend"], top_n=2)), 2)


if __name__ == "__main__":
    unittest.main()
