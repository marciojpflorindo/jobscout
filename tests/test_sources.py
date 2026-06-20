"""Sourcing: dedup, field coercion/caps, and graceful per-source skip (a broken
source contributes [] and never crashes a run)."""

import contextlib
import io
import unittest

import pathsetup  # noqa: F401
import config as C
import fetch as F
import sources as SRC


@contextlib.contextmanager
def quiet():
    """Swallow the sources modules' progress/warning prints during a test."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield


class TestRow(unittest.TestCase):
    def test_caps_long_fields(self):
        row = SRC._row("s", "t" * 1000, "c" * 1000, "l" * 1000, "u", "d" * 9999, "2026")
        self.assertLessEqual(len(row["title"]), SRC.TITLE_CAP)
        self.assertLessEqual(len(row["company"]), SRC.TITLE_CAP)
        self.assertLessEqual(len(row["location"]), SRC.TITLE_CAP)
        self.assertLessEqual(len(row["description"]), SRC.DESC_CAP)

    def test_blank_location_defaults(self):
        self.assertEqual(SRC._row("s", "t", "c", "", "u", "d", "")["location"], "Unspecified")

    def test_coerces_non_strings(self):
        row = SRC._row("s", 123, None, 0, None, None, None)
        self.assertEqual(row["title"], "123")
        self.assertEqual(row["company"], "")


class TestDeduplicate(unittest.TestCase):
    def test_dedup_by_url_ignoring_trailing_slash(self):
        jobs = [
            {"url": "https://x.com/job/1", "title": "A", "company": "C"},
            {"url": "https://x.com/job/1/", "title": "A2", "company": "C2"},
        ]
        self.assertEqual(len(SRC.deduplicate(jobs)), 1)

    def test_dedup_by_title_company_when_no_url(self):
        jobs = [
            {"url": "", "title": "Backend Eng", "company": "Acme"},
            {"url": "", "title": "backend eng", "company": "ACME"},
        ]
        self.assertEqual(len(SRC.deduplicate(jobs)), 1)

    def test_distinct_kept(self):
        jobs = [
            {"url": "https://x.com/1", "title": "A", "company": "C"},
            {"url": "https://x.com/2", "title": "B", "company": "C"},
        ]
        self.assertEqual(len(SRC.deduplicate(jobs)), 2)

    def test_empty_title_company_not_collapsed(self):
        # Distinct URLs with no title/company share the empty "|" key; they must
        # NOT collapse into the first one (that was a silent data-loss bug).
        jobs = [
            {"url": "https://x.com/1", "title": "", "company": ""},
            {"url": "https://x.com/2", "title": "", "company": ""},
        ]
        self.assertEqual(len(SRC.deduplicate(jobs)), 2)


class TestGracefulSkip(unittest.TestCase):
    def test_rss_unreachable_returns_empty(self):
        orig = F.fetch_feed_bytes
        F.fetch_feed_bytes = lambda url: None
        try:
            with quiet():
                self.assertEqual(SRC.scrape_rss("https://example.com/feed.xml"), [])
        finally:
            F.fetch_feed_bytes = orig

    def test_rss_parses_prefetched_bytes(self):
        feed = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
                b'<item><title>Backend Engineer</title>'
                b'<link>https://example.com/jobs/1</link>'
                b'<description>Work on services</description></item>'
                b'</channel></rss>')
        orig = F.fetch_feed_bytes
        F.fetch_feed_bytes = lambda url: feed
        try:
            with quiet():
                rows = SRC.scrape_rss("https://example.com/feed.xml")
        finally:
            F.fetch_feed_bytes = orig
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Backend Engineer")
        self.assertEqual(rows[0]["url"], "https://example.com/jobs/1")

    def test_jobspy_failure_returns_empty(self):
        # scrape_jobspy wraps everything; force the lazy import path to blow up by
        # pointing the call at an impossible site -> caught, [] returned.
        import builtins
        real_import = builtins.__import__

        def fail_import(name, *a, **k):
            if name == "jobspy":
                raise RuntimeError("boom")
            return real_import(name, *a, **k)

        builtins.__import__ = fail_import
        try:
            with quiet():
                self.assertEqual(
                    SRC.scrape_jobspy("eng", "indeed", "Germany", "", False), [])
        finally:
            builtins.__import__ = real_import


class TestConfigDefaults(unittest.TestCase):
    """config.load is exercised indirectly elsewhere; here just the pure helper."""
    def test_str_list_filters_and_coerces(self):
        self.assertEqual(C._str_list(["a", " b ", "", 3, None, "c"]), ["a", "b", "3", "c"])
        self.assertEqual(C._str_list("not a list"), [])


if __name__ == "__main__":
    unittest.main()
