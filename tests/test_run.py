"""Durable publish: the brain's outbox survives a failed/missing dashboard.

These cover the fix for "a failed publish wastes the whole run": results are
persisted to brain/state/pending.json before the network call, kept on failure
(merged with any prior unpublished results), and cleared only on success.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pathsetup  # noqa: F401
import run
import state as ST


class TestMerge(unittest.TestCase):
    def test_dedups_by_key_keeping_first(self):
        old = [{"Job link": "a", "x": 1}]
        new = [{"Job link": "a", "x": 2}, {"Job link": "b"}]
        out = run._merge(old, new, "Job link")
        self.assertEqual([o["Job link"] for o in out], ["a", "b"])
        self.assertEqual(out[0]["x"], 1)  # first occurrence wins

    def test_keeps_all_empty_keys(self):
        out = run._merge([{"Job link": ""}], [{"Job link": ""}], "Job link")
        self.assertEqual(len(out), 2)  # can't dedup blanks — keep both

    def test_skips_non_dicts(self):
        out = run._merge([{"link": "a"}, "junk", 7], [None], "link")
        self.assertEqual(out, [{"link": "a"}])


class TestPublishDurable(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._orig = (ST.STATE_DIR, ST.SCORED_FILE, ST.PENDING_FILE)
        ST.STATE_DIR = self.dir
        ST.SCORED_FILE = self.dir / "scored.json"
        ST.PENDING_FILE = self.dir / "pending.json"
        self._ingest, self._reject = run.dash.ingest, run.dash.reject

    def tearDown(self):
        ST.STATE_DIR, ST.SCORED_FILE, ST.PENDING_FILE = self._orig
        run.dash.ingest, run.dash.reject = self._ingest, self._reject

    def _ok(self):
        run.dash.ingest = lambda base, rows: {"added": len(rows), "skipped": 0}
        run.dash.reject = lambda base, items: {"added": len(items)}

    def _ingest_fails(self):
        def boom(base, rows):
            raise run.dash.DashboardError("HTTP Error 501: Unsupported method ('POST')")
        run.dash.ingest = boom
        run.dash.reject = lambda base, items: {"added": len(items)}

    def test_success_clears_outbox(self):
        self._ok()
        added = run._publish_durable("http://x", [{"Job link": "u1"}], [{"link": "u2"}])
        self.assertEqual(added, 1)
        self.assertFalse(ST.PENDING_FILE.exists())

    def test_failure_keeps_outbox(self):
        self._ingest_fails()
        added = run._publish_durable("http://x", [{"Job link": "u1"}], [])
        self.assertEqual(added, 0)
        self.assertTrue(ST.PENDING_FILE.exists())
        self.assertEqual(ST.load_pending()["survivors"], [{"Job link": "u1"}])

    def test_failed_results_merge_and_retry_next_run(self):
        # Run 1: publish fails -> survivor u1 held.
        self._ingest_fails()
        run._publish_durable("http://x", [{"Job link": "u1"}], [])
        # Run 2: a new survivor u2, still failing -> outbox now holds BOTH.
        run._publish_durable("http://x", [{"Job link": "u2"}], [])
        held = {s["Job link"] for s in ST.load_pending()["survivors"]}
        self.assertEqual(held, {"u1", "u2"})
        # Run 3: dashboard back -> flush-at-start sends everything, clears outbox.
        self._ok()
        added = run._publish_durable("http://x", [], [])
        self.assertEqual(added, 2)
        self.assertFalse(ST.PENDING_FILE.exists())

    def test_held_survivor_not_duplicated_if_rejudged(self):
        self._ingest_fails()
        run._publish_durable("http://x", [{"Job link": "u1"}], [])
        # Same link comes back (e.g. re-scraped) — must not pile up.
        run._publish_durable("http://x", [{"Job link": "u1"}], [])
        self.assertEqual(len(ST.load_pending()["survivors"]), 1)

    def test_nothing_to_send_returns_none(self):
        self._ok()
        self.assertIsNone(run._publish_durable("http://x", [], []))

    def test_oversized_outbox_does_not_deadlock(self):
        # Mimic the server: reject any batch over its 500 cap, accept otherwise.
        # Pre-chunking this wedged the outbox forever (H1); chunking clears it.
        def ingest(base, rows):
            if len(rows) > 500:
                raise run.dash.DashboardError("too many jobs (max 500)")
            return {"added": len(rows), "skipped": 0}
        run.dash.ingest = ingest
        run.dash.reject = lambda base, items: {"added": len(items)}
        survivors = [{"Job link": f"u{i}"} for i in range(1200)]
        added = run._publish_durable("http://x", survivors, [])
        self.assertEqual(added, 1200)
        self.assertFalse(ST.PENDING_FILE.exists())  # fully drained, not wedged


class TestSendChunking(unittest.TestCase):
    def setUp(self):
        self._ingest, self._reject = run.dash.ingest, run.dash.reject
        self.calls = []

    def tearDown(self):
        run.dash.ingest, run.dash.reject = self._ingest, self._reject

    def test_chunks_capped_at_publish_batch(self):
        def ingest(base, rows):
            self.calls.append(len(rows))
            return {"added": len(rows), "skipped": 0}
        run.dash.ingest = ingest
        survivors = [{"Job link": f"u{i}"} for i in range(1200)]
        ok, added = run._send("http://x", survivors, [])
        self.assertTrue(ok)
        self.assertEqual(self.calls, [run.PUBLISH_BATCH, run.PUBLISH_BATCH, 200])
        self.assertTrue(all(c <= run.PUBLISH_BATCH for c in self.calls))
        self.assertEqual(added, 1200)

    def test_stops_at_first_failed_chunk(self):
        def ingest(base, rows):
            self.calls.append(len(rows))
            if len(self.calls) == 2:
                raise run.dash.DashboardError("400 too many")
            return {"added": len(rows), "skipped": 0}
        run.dash.ingest = ingest
        survivors = [{"Job link": f"u{i}"} for i in range(1200)]
        ok, added = run._send("http://x", survivors, [])
        self.assertFalse(ok)
        self.assertEqual(self.calls, [run.PUBLISH_BATCH, run.PUBLISH_BATCH])  # stopped
        self.assertEqual(added, run.PUBLISH_BATCH)  # only the first chunk counted


class _FakeJudge:
    def __init__(self, *a, **k):
        pass

    def judge(self, job):
        return {"verdict": "match", "score": 90, "why": "good fit"}


class TestPipelineOrdering(unittest.TestCase):
    """The durable-publish invariant (H2): the outbox is persisted BEFORE the run
    is recorded as scored, so a posting is never marked 'already judged' (and thus
    skipped forever) unless it's also safely queued."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._state = (ST.STATE_DIR, ST.SCORED_FILE, ST.PENDING_FILE,
                       ST.save_pending, ST.save_scored)
        ST.STATE_DIR = self.dir
        ST.SCORED_FILE = self.dir / "scored.json"
        ST.PENDING_FILE = self.dir / "pending.json"

        # Record the order of the two durability writes, calling through so the
        # pipeline still behaves for real.
        self.order = []
        real_sp, real_ss = ST.save_pending, ST.save_scored
        ST.save_pending = lambda s, r: (self.order.append("pending"), real_sp(s, r))[1]
        ST.save_scored = lambda sc: (self.order.append("scored"), real_ss(sc))[1]

        # Stub the I/O-bound stages (network, model) — keep the real merge/persist.
        self._mods = {
            (run.sources, "collect"): run.sources.collect,
            (run.sources, "deduplicate"): run.sources.deduplicate,
            (run.dash, "exclusion"): run.dash.exclusion,
            (run.dash, "ingest"): run.dash.ingest,
            (run.dash, "reject"): run.dash.reject,
            (run.heuristic, "rank"): run.heuristic.rank,
            (run.fetch, "fetch_posting_text_cached"): run.fetch.fetch_posting_text_cached,
            (run, "Judge"): run.Judge,
        }
        job = {"url": "https://x.com/1", "title": "T", "company": "C", "description": "d"}
        run.sources.collect = lambda *a, **k: [dict(job)]
        run.sources.deduplicate = lambda raw: raw
        run.dash.exclusion = lambda base: run.dash.Exclusion(set(), [])
        run.dash.ingest = lambda base, rows: {"added": len(rows), "skipped": 0}
        run.dash.reject = lambda base, items: {"added": len(items)}
        run.heuristic.rank = lambda jobs, queries, top: jobs
        run.fetch.fetch_posting_text_cached = lambda url: "posting text"
        run.Judge = _FakeJudge

    def tearDown(self):
        (ST.STATE_DIR, ST.SCORED_FILE, ST.PENDING_FILE,
         ST.save_pending, ST.save_scored) = self._state
        for (mod, name), orig in self._mods.items():
            setattr(mod, name, orig)

    def _conf(self):
        return SimpleNamespace(
            model="m", dashboard_base="http://x", ollama_base="http://o",
            search=SimpleNamespace(queries=["q"]), extra_rss=[],
            extra_jobspy_locations=[], profile_text="p", cv_path=None, ntfy=None)

    def test_outbox_persisted_before_scored(self):
        args = SimpleNamespace(top=30, dry_run=False, publish_only=False)
        rc, outcome = run._run_pipeline(self._conf(), args)
        self.assertEqual(rc, 0)
        self.assertEqual(outcome, "new")
        self.assertEqual(self.order, ["pending", "scored"])  # never scored-first

    def test_dry_run_records_nothing(self):
        args = SimpleNamespace(top=30, dry_run=True, publish_only=False)
        rc, outcome = run._run_pipeline(self._conf(), args)
        self.assertEqual(rc, 0)
        self.assertIsNone(outcome)
        self.assertEqual(self.order, [])  # no publish, no scored pollution
        self.assertFalse(ST.SCORED_FILE.exists())


if __name__ == "__main__":
    unittest.main()
