"""Idempotency ledger: stable job ids, corrupt-file recovery, round-trip."""

import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401
import state as ST


class TestJobId(unittest.TestCase):
    def test_url_strips_trailing_slash(self):
        self.assertEqual(ST._job_id({"url": "https://x.com/1/"}), "https://x.com/1")

    def test_falls_back_to_title_company(self):
        jid = ST._job_id({"url": "", "title": "Backend", "company": "Acme"})
        self.assertEqual(jid, "backend|acme")

    def test_same_job_same_id(self):
        a = ST._job_id({"url": "https://x.com/1"})
        b = ST._job_id({"url": "https://x.com/1/"})
        self.assertEqual(a, b)


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._orig_dir = ST.STATE_DIR
        self._orig_file = ST.SCORED_FILE
        ST.STATE_DIR = self.dir
        ST.SCORED_FILE = self.dir / "scored.json"

    def tearDown(self):
        ST.STATE_DIR = self._orig_dir
        ST.SCORED_FILE = self._orig_file

    def test_missing_file_is_empty(self):
        self.assertEqual(ST.load_scored(), {})

    def test_corrupt_file_recovers_empty(self):
        ST.SCORED_FILE.write_text("{bad json", encoding="utf-8")
        self.assertEqual(ST.load_scored(), {})

    def test_non_dict_recovers_empty(self):
        ST.SCORED_FILE.write_text("[1,2]", encoding="utf-8")
        self.assertEqual(ST.load_scored(), {})

    def test_record_save_load_roundtrip(self):
        scored = {}
        job = {"url": "https://x.com/1"}
        ST.record(job, "match", 90, scored)
        self.assertTrue(ST.already_scored(job, scored))
        ST.save_scored(scored)
        reloaded = ST.load_scored()
        self.assertIn("https://x.com/1", reloaded)
        self.assertEqual(reloaded["https://x.com/1"]["verdict"], "match")
        self.assertEqual(reloaded["https://x.com/1"]["score"], 90)


if __name__ == "__main__":
    unittest.main()
