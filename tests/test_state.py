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

    def test_urlless_titleless_jobs_get_distinct_ids(self):
        # Two distinct junk jobs (no url, no title/company) must NOT share the
        # empty "|" id, or the second would look already-scored and be skipped.
        a = ST._job_id({"url": "", "title": "", "company": "", "description": "foo"})
        b = ST._job_id({"url": "", "title": "", "company": "", "description": "bar"})
        self.assertNotEqual(a, b)
        # ...but the same junk job is still stable across runs (idempotent).
        self.assertEqual(a, ST._job_id({"description": "foo", "title": "", "company": "", "url": ""}))


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._orig_dir = ST.STATE_DIR
        self._orig_file = ST.SCORED_FILE
        self._orig_pending = ST.PENDING_FILE
        ST.STATE_DIR = self.dir
        ST.SCORED_FILE = self.dir / "scored.json"
        ST.PENDING_FILE = self.dir / "pending.json"

    def tearDown(self):
        ST.STATE_DIR = self._orig_dir
        ST.SCORED_FILE = self._orig_file
        ST.PENDING_FILE = self._orig_pending

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

    # --- pending outbox ---
    def test_pending_missing_is_empty(self):
        self.assertEqual(ST.load_pending(), {"survivors": [], "rejects": []})

    def test_pending_roundtrip(self):
        survivors = [{"Company": "Acme", "Job link": "https://x.com/1"}]
        rejects = [{"link": "https://x.com/2", "reason": "on-site"}]
        ST.save_pending(survivors, rejects)
        loaded = ST.load_pending()
        self.assertEqual(loaded["survivors"], survivors)
        self.assertEqual(loaded["rejects"], rejects)

    def test_pending_corrupt_recovers_empty(self):
        ST.PENDING_FILE.write_text("{bad json", encoding="utf-8")
        self.assertEqual(ST.load_pending(), {"survivors": [], "rejects": []})

    def test_pending_non_dict_recovers_empty(self):
        ST.PENDING_FILE.write_text("[1,2]", encoding="utf-8")
        self.assertEqual(ST.load_pending(), {"survivors": [], "rejects": []})

    def test_pending_filters_non_dict_entries(self):
        ST.PENDING_FILE.write_text(
            '{"survivors": [{"Company": "A"}, "junk", 3], "rejects": "nope"}',
            encoding="utf-8")
        loaded = ST.load_pending()
        self.assertEqual(loaded["survivors"], [{"Company": "A"}])
        self.assertEqual(loaded["rejects"], [])

    def test_clear_pending_is_idempotent(self):
        ST.save_pending([{"Job link": "https://x.com/1"}], [])
        self.assertTrue(ST.PENDING_FILE.exists())
        ST.clear_pending()
        self.assertFalse(ST.PENDING_FILE.exists())
        ST.clear_pending()  # second clear must not raise


if __name__ == "__main__":
    unittest.main()
