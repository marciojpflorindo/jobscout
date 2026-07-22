"""Source registry, streaming adapters, normalization, and failure isolation."""

import contextlib
import io
import types
import unittest
from datetime import date, datetime
from unittest.mock import patch

import pathsetup  # noqa: F401
import config as C
import fetch as F
import sources as SRC
from sources import arbeitnow as ARBEITNOW
from sources import base as BASE
from sources import himalayas as HIMALAYAS
from sources import http as HTTP
from sources import jobicy as JOBICY
from sources import jobspy as JOBSPY
from sources import remoteok as REMOTEOK
from sources import remotive as REMOTIVE
from sources import rss as RSS
from sources import working_nomads as WN
from sources import wwr as WWR
from sources.registry import SOURCE_BY_KEY, SOURCE_KEYS, SOURCES


@contextlib.contextmanager
def quiet():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        yield buffer


def _conf(source_on=lambda _name: True, **fields):
    return types.SimpleNamespace(source_on=source_on, **fields)


class TestNormalizeJob(unittest.TestCase):
    def test_caps_hostile_fields(self):
        row = SRC.normalize_job({
            "source": "s" * 1_000,
            "title": "t" * 1_000,
            "company": "c" * 1_000,
            "location": "l" * 1_000,
            "url": "https://example.com/job/1",
            "description": "d" * 9_999,
            "date": "2" * 1_000,
        })
        self.assertIsNotNone(row)
        self.assertLessEqual(len(row["source"]), BASE.SOURCE_CAP)
        self.assertLessEqual(len(row["title"]), BASE.TITLE_CAP)
        self.assertLessEqual(len(row["company"]), BASE.TITLE_CAP)
        self.assertLessEqual(len(row["location"]), BASE.TITLE_CAP)
        self.assertLessEqual(len(row["description"]), BASE.DESC_CAP)
        self.assertLessEqual(len(row["date"]), BASE.DATE_CAP)

    def test_blank_location_and_source_use_defaults(self):
        row = SRC.normalize_job({
            "title": "Analyst",
            "url": "https://example.com/job/1",
            "location": "",
        }, "Test board")
        self.assertEqual(row["location"], "Unspecified")
        self.assertEqual(row["source"], "Test board")

    def test_dates_are_preserved_as_iso_text(self):
        first = SRC.normalize_job({
            "title": "Analyst", "url": "https://example.com/1",
            "date": date(2026, 7, 10),
        })
        second = SRC.normalize_job({
            "title": "Editor", "url": "https://example.com/2",
            "date": datetime(2026, 7, 10, 9, 30),
        })
        self.assertEqual(first["date"], "2026-07-10")
        self.assertEqual(second["date"], "2026-07-10T09:30:00")

    def test_non_finite_float_fields_are_missing_not_text(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                row = SRC.normalize_job({
                    "title": "Analyst", "company": value,
                    "url": "https://example.com/1",
                })
                self.assertEqual(row["company"], "")

    def test_rejects_malformed_or_unsafe_urls(self):
        invalid_urls = (
            "file:///tmp/x",
            "not a URL",
            "https://[invalid",
            "https://user:pass@example.com/job",
            "https://\ud800.example/job",
        )
        self.assertIsNone(SRC.normalize_job({"url": "https://example.com/1"}))
        for url in invalid_urls:
            with self.subTest(url=repr(url)):
                self.assertIsNone(SRC.normalize_job({"title": "Analyst", "url": url}))

    def test_rejects_oversized_url_instead_of_truncating_it(self):
        url = "https://example.com/" + "a" * BASE.URL_CAP
        self.assertIsNone(SRC.normalize_job({"title": "Analyst", "url": url}))

    def test_mapping_with_raising_getter_fails_only_that_row(self):
        class ExplodingMapping(dict):
            def get(self, _key, _default=None):
                raise RuntimeError("hostile getter")

        self.assertIsNone(SRC.normalize_job(ExplodingMapping()))


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

    def test_distinct_empty_title_company_rows_are_kept(self):
        jobs = [
            {"url": "https://x.com/1", "title": "", "company": ""},
            {"url": "https://x.com/2", "title": "", "company": ""},
        ]
        self.assertEqual(len(SRC.deduplicate(jobs)), 2)

    def test_companyless_same_title_jobs_with_distinct_urls_are_kept(self):
        jobs = [
            {"url": "https://x.com/1", "title": "Software Engineer", "company": ""},
            {"url": "https://x.com/2", "title": "Software Engineer", "company": ""},
        ]
        self.assertEqual(len(SRC.deduplicate(jobs)), 2)


class TestRegistry(unittest.TestCase):
    def _collect_with(self, specs, enabled=lambda _name: True):
        original = SRC.SOURCES
        SRC.SOURCES = specs
        try:
            with quiet():
                return SRC.collect(_conf(source_on=enabled))
        finally:
            SRC.SOURCES = original

    def test_keys_are_unique(self):
        self.assertEqual(len(SOURCES), len(SOURCE_BY_KEY))

    def test_boards_have_independent_keys(self):
        self.assertIn("indeed", SOURCE_BY_KEY)
        self.assertIn("linkedin", SOURCE_BY_KEY)
        self.assertNotIn("jobspy", SOURCE_BY_KEY)

    def test_all_sources_off_touches_no_adapter(self):
        with quiet():
            self.assertEqual(SRC.collect(_conf(source_on=lambda _name: False)), [])

    def test_only_enabled_adapter_runs(self):
        calls: list[str] = []

        def first(_conf):
            calls.append("first")
            return [{"title": "Analyst", "url": "https://example.com/1"}]

        def second(_conf):
            calls.append("second")
            return [{"title": "Editor", "url": "https://example.com/2"}]

        rows = self._collect_with(
            (SRC.SourceSpec("first", "First", first),
             SRC.SourceSpec("second", "Second", second)),
            enabled=lambda name: name == "second",
        )
        self.assertEqual(calls, ["second"])
        self.assertEqual([row["title"] for row in rows], ["Editor"])

    def test_inactive_source_is_skipped_without_health_entry(self):
        # `active` says "not set up for this config" (e.g. no RSS feeds listed):
        # skipped entirely, never flagged as degraded.
        def never_runs(_conf):
            raise AssertionError("inactive source must not be collected")

        rows = self._collect_with((SRC.SourceSpec(
            "rss", "User RSS feeds", never_runs, active=lambda _c: False),))
        self.assertEqual(rows, [])
        self.assertEqual(SRC.HEALTH, [])

    def test_shared_collector_runs_once_for_two_board_keys(self):
        calls: list[str] = []

        def shared(_conf):
            calls.append("shared")
            return [{"title": "Analyst", "url": "https://example.com/1"}]

        rows = self._collect_with((
            SRC.SourceSpec("indeed", "JobSpy", shared),
            SRC.SourceSpec("linkedin", "JobSpy", shared),
        ))
        self.assertEqual(calls, ["shared"])
        self.assertEqual(len(rows), 1)

    def test_broken_stream_does_not_stop_following_source(self):
        def broken(_conf):
            yield {"title": "First", "url": "https://example.com/1"}
            raise RuntimeError("boom")

        def healthy(_conf):
            yield {"title": "Second", "url": "https://example.com/2"}

        rows = self._collect_with((
            SRC.SourceSpec("broken", "Broken", broken),
            SRC.SourceSpec("healthy", "Healthy", healthy),
        ))
        self.assertEqual([row["title"] for row in rows], ["First", "Second"])

    def test_hostile_mapping_is_skipped_and_stream_continues(self):
        class ExplodingMapping(dict):
            def get(self, _key, _default=None):
                raise RuntimeError("hostile getter")

        def collector(_conf):
            yield ExplodingMapping()
            yield {"title": "Analyst", "url": "https://example.com/2"}

        rows = self._collect_with((SRC.SourceSpec("one", "One", collector),))
        self.assertEqual([row["title"] for row in rows], ["Analyst"])

    def test_result_cap_is_applied_during_iteration(self):
        produced: list[int] = []

        def collector(_conf):
            for index in range(5):
                produced.append(index)
                yield {"title": f"Analyst {index}",
                       "url": f"https://example.com/{index}"}

        original_cap = SRC.MAX_RESULTS_PER_SOURCE
        SRC.MAX_RESULTS_PER_SOURCE = 2
        try:
            rows = self._collect_with((SRC.SourceSpec("one", "One", collector),))
        finally:
            SRC.MAX_RESULTS_PER_SOURCE = original_cap
        self.assertEqual(len(rows), 2)
        self.assertEqual(produced, [0, 1])


class TestSourceHealth(unittest.TestCase):
    """A dead board does not raise — it yields less. HEALTH makes that visible:
    a feed serving an HTML page or an API that renamed a field produces a clean
    run that quietly returns nothing."""

    def _collect_with(self, specs):
        original = SRC.SOURCES
        SRC.SOURCES = specs
        try:
            with quiet():
                SRC.collect(_conf())
        finally:
            SRC.SOURCES = original

    def test_a_source_that_returns_nothing_is_flagged_not_silent(self):
        def dead(_conf):
            return iter(())

        def alive(_conf):
            yield {"title": "Analyst", "company": "Acme", "url": "https://example.com/1"}

        self._collect_with((SRC.SourceSpec("dead", "Dead", dead),
                            SRC.SourceSpec("alive", "Alive", alive)))
        health = {h["key"]: h for h in SRC.HEALTH}
        self.assertEqual(health["dead"]["problem"], "produced NO usable rows")
        self.assertEqual(health["alive"]["problem"], "")
        self.assertEqual([h["key"] for h in SRC.degraded()], ["dead"])
        self.assertIn("Dead", SRC.health_summary())

    def test_rows_without_a_company_are_flagged(self):
        # An empty company silently disables company-based dedup and rejection
        # feedback for every row that source produces.
        def nameless(_conf):
            for index in range(4):
                yield {"title": f"Analyst {index}", "url": f"https://example.com/{index}"}

        self._collect_with((SRC.SourceSpec("nameless", "Nameless", nameless),))
        self.assertEqual(SRC.HEALTH[0]["rows"], 4)
        self.assertEqual(SRC.HEALTH[0]["with_company"], 0)
        self.assertIn("no company name", SRC.HEALTH[0]["problem"])

    def test_health_is_reset_per_run_not_accumulated(self):
        def alive(_conf):
            yield {"title": "Analyst", "company": "Acme", "url": "https://example.com/1"}

        spec = (SRC.SourceSpec("alive", "Alive", alive),)
        self._collect_with(spec)
        self._collect_with(spec)
        self.assertEqual(len(SRC.HEALTH), 1)

    def test_a_disabled_source_is_not_reported_as_degraded(self):
        # Only ENABLED sources are health-checked; an off toggle is a choice,
        # not a fault.
        original = SRC.SOURCES
        SRC.SOURCES = (SRC.SourceSpec("off", "Off", lambda _c: iter(())),)
        try:
            with quiet():
                SRC.collect(_conf(source_on=lambda _name: False))
        finally:
            SRC.SOURCES = original
        self.assertEqual(SRC.HEALTH, [])
        self.assertEqual(SRC.degraded(), [])


class TestJobSpyAdapter(unittest.TestCase):
    def test_jobspy_failure_yields_nothing(self):
        # scrape_jobspy wraps everything; force the lazy import to blow up ->
        # caught, nothing yielded.
        import builtins
        real_import = builtins.__import__

        def fail_import(name, *a, **k):
            if name == "jobspy":
                raise RuntimeError("boom\x1b[31m")
            return real_import(name, *a, **k)

        builtins.__import__ = fail_import
        try:
            with quiet() as output:
                rows = list(JOBSPY.scrape_jobspy("eng", "indeed", "Germany", "", False))
        finally:
            builtins.__import__ = real_import
        self.assertEqual(rows, [])
        self.assertNotIn("\x1b", output.getvalue())
        self.assertIn("\\x1b", output.getvalue())

    def test_unsupported_site_yields_nothing(self):
        with quiet():
            self.assertEqual(list(JOBSPY.scrape_jobspy("eng", "glassdoor", "", "", False)), [])

    def test_both_sites_off_never_touches_jobspy(self):
        conf = _conf(source_on=lambda _name: False)
        self.assertEqual(list(JOBSPY.collect_jobspy(conf)), [])

    def test_site_toggle_drops_only_that_site(self):
        captured = []
        conf = _conf(
            source_on=lambda name: name == "linkedin",
            search=types.SimpleNamespace(remote_preference="remote-only",
                                         queries=["eng"], country="Germany", city=""),
            extra_jobspy_locations=[],
        )
        with patch.object(JOBSPY, "scrape_jobspy",
                          lambda q, site, *a, **k: captured.append(site) or []), \
                patch.object(JOBSPY.time, "sleep", lambda *_: None), quiet():
            list(JOBSPY.collect_jobspy(conf))
        self.assertEqual(captured, ["linkedin"])


class TestExtraLocations(unittest.TestCase):
    def _capture(self, locations, queries=("eng",), country="United States"):
        """Run the extra-locations pass with scrape_jobspy + time.sleep stubbed,
        returning the (country, location_override) pairs it would have scraped."""
        captured = []
        conf = _conf(
            search=types.SimpleNamespace(remote_preference="remote-only",
                                         queries=list(queries), country=country,
                                         city=""),
            extra_jobspy_locations=list(locations),
        )
        with patch.object(JOBSPY, "scrape_jobspy",
                          lambda q, site, c, city, is_remote, location_override=None:
                          captured.append((c, location_override)) or []), \
                patch.object(JOBSPY.time, "sleep", lambda *_: None), quiet():
            list(JOBSPY._extra_locations(conf, ["indeed", "linkedin"]))
        return captured

    def test_each_entry_drives_its_own_indeed_country(self):
        # Regression: extras used to inherit the PRIMARY country, so every extra
        # country searched the primary country's Indeed. Each entry must drive its
        # own Indeed domain, while the full entry is kept as the location_override.
        captured = self._capture(["Mexico", "Berlin, Germany"])
        self.assertEqual({c for c, _ in captured}, {"Mexico", "Germany"})
        self.assertNotIn("United States", {c for c, _ in captured})
        self.assertEqual({o for _, o in captured}, {"Mexico", "Berlin, Germany"})

    def test_trailing_comma_and_blanks_never_leak_a_comma_country(self):
        # Hand-edited config can hold "Mexico," or " , "; the derived country must
        # never carry a stray comma.
        captured = self._capture(["Mexico,", " , ", "Berlin, Germany"])
        for c, _ in captured:
            self.assertNotIn(",", c)
        self.assertIn("Mexico", {c for c, _ in captured})

    def test_expands_eu_region_to_member_countries(self):
        # JobSpy rejects "EU"; the alias must expand to its member countries.
        captured = self._capture(["EU"])
        searched = {c for c, _ in captured}
        self.assertEqual(searched, set(JOBSPY._EU_COUNTRIES))
        self.assertNotIn("eu", searched)
        self.assertIn("ireland", searched)

    def test_region_dedups_with_explicit_entry(self):
        # A country also covered by the region must not be searched twice.
        captured = self._capture(["EU", "Germany"])
        self.assertEqual({c for c, _ in captured}, set(JOBSPY._EU_COUNTRIES))

    def test_expand_locations_passthrough_and_dedup(self):
        # Pure helper: unknown entries pass through; case-insensitive dedup;
        # blanks drop.
        self.assertEqual(JOBSPY.expand_locations(["Mexico", "mexico", "  "]), ["Mexico"])

    def test_caps_number_of_extra_locations(self):
        # Each location multiplies into queries×sites scrape calls; a long paste
        # must be bounded to MAX_EXTRA_LOCATIONS.
        with quiet():
            locs = [f"Country{i}" for i in range(JOBSPY.MAX_EXTRA_LOCATIONS + 5)]
            captured = self._capture(locs, queries=("eng",))
        searched = {c for c, _ in captured}
        self.assertEqual(len(searched), JOBSPY.MAX_EXTRA_LOCATIONS)
        self.assertNotIn(f"Country{JOBSPY.MAX_EXTRA_LOCATIONS}", searched)


class TestUserRss(unittest.TestCase):
    def test_rss_unreachable_yields_nothing(self):
        with patch.object(F, "fetch_feed_bytes", return_value=None), quiet():
            rows = list(RSS.scrape_rss("https://example.com/feed.xml"))
        self.assertEqual(rows, [])

    def test_rss_parses_prefetched_bytes(self):
        feed = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
                b'<item><title>Backend Engineer</title>'
                b'<link>https://example.com/jobs/1</link>'
                b'<description>Work on services</description></item>'
                b'</channel></rss>')
        with patch.object(F, "fetch_feed_bytes", return_value=feed), quiet():
            rows = list(RSS.scrape_rss("https://example.com/feed.xml"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Backend Engineer")
        self.assertEqual(rows[0]["url"], "https://example.com/jobs/1")

    def test_rss_zero_entry_feed_warns_and_yields_nothing(self):
        # Valid XML, empty channel — the quiet shape of a dead feed. It must
        # yield nothing AND say so, not just contribute nothing silently.
        feed = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
        with patch.object(F, "fetch_feed_bytes", return_value=feed), quiet() as buffer:
            rows = list(RSS.scrape_rss("https://example.com/feed.xml"))
        self.assertEqual(rows, [])
        self.assertIn("no entries", buffer.getvalue())

    def test_rss_dates_become_iso_for_the_freshness_note(self):
        # Feeds ship RFC-822 dates ("Fri, 18 Jul 2026 …"), which the freshness
        # note's ISO-prefix parse rejects; entry_date prefers feedparser's
        # format-agnostic *_parsed struct and emits ISO.
        feed = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
                b'<item><title>Writer</title>'
                b'<link>https://example.com/jobs/1</link>'
                b'<pubDate>Fri, 18 Jul 2026 09:30:15 +0000</pubDate>'
                b'<description>d</description></item>'
                b'</channel></rss>')
        with patch.object(F, "fetch_feed_bytes", return_value=feed), quiet():
            rows = list(RSS.scrape_rss("https://example.com/feed.xml"))
        self.assertEqual(rows[0]["date"], "2026-07-18")

    def test_rss_external_entity_is_not_expanded(self):
        feed = (b'<?xml version="1.0"?>'
                b'<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/hosts">]>'
                b'<rss version="2.0"><channel><item><title>&xxe;</title>'
                b'<link>https://example.com/jobs/1</link></item></channel></rss>')
        with patch.object(F, "fetch_feed_bytes", return_value=feed), quiet():
            rows = list(RSS.scrape_rss("https://example.com/feed.xml"))
        self.assertIn(rows[0]["title"], ("", "&xxe;"))
        self.assertNotIn("localhost", rows[0]["title"])

    def test_entry_date_falls_back_to_the_raw_string(self):
        # An unparseable date degrades to the old behaviour (string through, so
        # no freshness note downstream) — never to a guessed or wrong day.
        self.assertEqual(RSS.entry_date({"published": "sometime soon"}), "sometime soon")
        self.assertEqual(RSS.entry_date({"updated": "also odd"}), "also odd")
        self.assertEqual(RSS.entry_date({}), "")

    def test_collect_rss_walks_every_configured_feed(self):
        seen = []
        conf = _conf(extra_rss=["https://a.test/f", "https://b.test/f"])
        with patch.object(RSS, "scrape_rss",
                          lambda url: seen.append(url) or iter(())):
            list(RSS.collect_rss(conf))
        self.assertEqual(seen, ["https://a.test/f", "https://b.test/f"])


class TestOtherAdapters(unittest.TestCase):
    def test_remoteok_returns_at_most_exact_limit_with_or_without_metadata(self):
        valid = [{"position": f"Role {index}", "url": f"https://example.com/{index}"}
                 for index in range(REMOTEOK.MAX_API_ENTRIES + 5)]
        for payload in (valid, [{"legal": "metadata"}, *valid]):
            with patch.object(REMOTEOK, "fetch_json", return_value=payload):
                rows = list(REMOTEOK.collect_remoteok(None))
            self.assertEqual(len(rows), REMOTEOK.MAX_API_ENTRIES)

    def test_remoteok_rejects_wrong_envelope(self):
        with patch.object(REMOTEOK, "fetch_json", return_value={"jobs": []}):
            with self.assertRaisesRegex(ValueError, "unexpected response shape"):
                list(REMOTEOK.collect_remoteok(None))

    def test_arbeitnow_maps_fields_and_rejects_wrong_envelope(self):
        payload = {"data": [{
            "title": "Backend Developer", "company_name": "Acme",
            "url": "https://arbeitnow.com/jobs/bd", "location": "Berlin",
            "remote": True, "description": "Build services", "created_at": 1_720_000_000,
        }]}
        with patch.object(ARBEITNOW, "fetch_json", return_value=payload):
            rows = list(ARBEITNOW.collect_arbeitnow(None))
        self.assertEqual(rows[0]["company"], "Acme")
        self.assertEqual(rows[0]["location"], "Berlin — Remote")
        self.assertEqual(rows[0]["date"], "2024-07-03")
        with patch.object(ARBEITNOW, "fetch_json", return_value=[]):
            with self.assertRaisesRegex(ValueError, "unexpected response shape"):
                list(ARBEITNOW.collect_arbeitnow(None))

    def test_arbeitnow_passes_string_date_through(self):
        # If the API ever sends an ISO string instead of epoch seconds, keep it.
        payload = {"data": [{"title": "T", "url": "https://arbeitnow.com/j",
                             "created_at": "2026-07-14"}]}
        with patch.object(ARBEITNOW, "fetch_json", return_value=payload):
            rows = list(ARBEITNOW.collect_arbeitnow(None))
        self.assertEqual(rows[0]["date"], "2026-07-14")

    def test_epoch_milliseconds_fail_soft_instead_of_publishing_a_wrong_year(self):
        milliseconds = 1_720_000_000_000
        self.assertEqual(ARBEITNOW._iso_from_epoch(milliseconds), "")
        self.assertEqual(HIMALAYAS._date(milliseconds), "")

    def test_remotive_maps_fields_and_rejects_wrong_envelope(self):
        payload = {"jobs": [{
            "title": "Data Engineer", "company_name": "Globex",
            "url": "https://remotive.com/j/1",
            "candidate_required_location": "Europe",
            "description": "Pipelines", "publication_date": "2026-07-10",
        }]}
        with patch.object(REMOTIVE, "fetch_json", return_value=payload):
            rows = list(REMOTIVE.collect_remotive(None))
        self.assertEqual(rows[0]["company"], "Globex")
        self.assertEqual(rows[0]["location"], "Europe")
        with patch.object(REMOTIVE, "fetch_json", return_value={"data": []}):
            with self.assertRaisesRegex(ValueError, "unexpected response shape"):
                list(REMOTIVE.collect_remotive(None))

    def test_jobicy_maps_renamed_fields(self):
        payload = {"jobs": [{
            "jobTitle": "Content Designer", "companyName": "Initech",
            "url": "https://jobicy.com/j/2", "jobGeo": "Anywhere",
            "jobExcerpt": "Short", "pubDate": "2026-07-11",
        }]}
        with patch.object(JOBICY, "fetch_json", return_value=payload):
            rows = list(JOBICY.collect_jobicy(None))
        self.assertEqual(rows[0]["title"], "Content Designer")
        self.assertEqual(rows[0]["description"], "Short")

    def test_himalayas_uses_application_link_and_joins_locations(self):
        payload = {"jobs": [{
            "title": "UX Researcher", "companyName": "Hooli",
            "locationRestrictions": ["Ireland", "Spain"],
            "description": "Research", "applicationLink": "https://hooli.com/apply",
            "pubDate": "2026-07-12",
        }]}
        with patch.object(HIMALAYAS, "fetch_json",
                          side_effect=[payload, {"jobs": []}]):
            rows = list(HIMALAYAS.collect_himalayas(None))
        self.assertEqual(rows[0]["url"], "https://hooli.com/apply")
        self.assertEqual(rows[0]["location"], "Ireland, Spain")

    def test_himalayas_recovers_company_from_slug_when_api_sends_placeholder(self):
        # The live API can return the literal string "name" for companyName.
        # Without the slug fallback every such row reaches the judge as
        # "COMPANY: name" and company-based dedup can't match it.
        payload = {"jobs": [
            {"title": "A", "companyName": "name",
             "companySlug": "bright-vision-technologies",
             "applicationLink": "https://himalayas.app/a"},
            {"title": "B", "companyName": "Real Co", "companySlug": "ignored-slug",
             "applicationLink": "https://himalayas.app/b"},
            {"title": "C", "companyName": "", "companySlug": "zoetis",
             "applicationLink": "https://himalayas.app/c"},
        ]}
        with patch.object(HIMALAYAS, "fetch_json", side_effect=[payload, {"jobs": []}]):
            rows = list(HIMALAYAS.collect_himalayas(None))
        self.assertEqual([r["company"] for r in rows],
                         ["Bright Vision Technologies", "Real Co", "Zoetis"])

    def test_himalayas_pages_until_empty_and_caps(self):
        # limit is silently capped at 20 server-side, so one call can never reach
        # MAX_API_ENTRIES; the adapter must page with offset.
        def page(n):
            return {"jobs": [{"title": f"t{n}-{i}", "companyName": "Co",
                              "applicationLink": f"https://himalayas.app/{n}-{i}"}
                             for i in range(HIMALAYAS.PAGE_SIZE)]}

        calls = []

        def fake(url):
            calls.append(url)
            return page(len(calls)) if len(calls) <= 3 else {"jobs": []}

        with patch.object(HIMALAYAS, "fetch_json", side_effect=fake):
            rows = list(HIMALAYAS.collect_himalayas(None))
        self.assertEqual(len(rows), 3 * HIMALAYAS.PAGE_SIZE)
        self.assertIn("offset=0", calls[0])
        # Offset advances by the rows actually returned, not a hardcoded stride.
        self.assertIn(f"offset={HIMALAYAS.PAGE_SIZE}", calls[1])

    def test_himalayas_caps_at_max_entries(self):
        big = {"jobs": [{"title": f"t{i}", "companyName": "Co",
                         "applicationLink": f"https://himalayas.app/{i}"}
                        for i in range(HIMALAYAS.PAGE_SIZE)]}
        with patch.object(HIMALAYAS, "fetch_json", return_value=big):
            rows = list(HIMALAYAS.collect_himalayas(None))
        self.assertEqual(len(rows), HIMALAYAS.MAX_API_ENTRIES)

    def test_himalayas_bad_first_page_raises_but_later_page_keeps_rows(self):
        with patch.object(HIMALAYAS, "fetch_json", return_value={"data": []}):
            with self.assertRaisesRegex(ValueError, "unexpected response shape"):
                list(HIMALAYAS.collect_himalayas(None))
        good = {"jobs": [{"title": "keep", "companyName": "Co",
                          "applicationLink": "https://himalayas.app/1"}]}
        with patch.object(HIMALAYAS, "fetch_json", side_effect=[good, {"oops": 1}]):
            rows = list(HIMALAYAS.collect_himalayas(None))
        self.assertEqual([r["title"] for r in rows], ["keep"])

    def test_working_nomads_maps_elasticsearch_envelope(self):
        payload = {"hits": {"hits": [
            {"_source": {"title": "QA Engineer", "company": "Acme Labs",
                         "slug": "qa-engineer-acme-labs-1740290",
                         "locations": ["Ireland", "Spain"],
                         "description": "<p>Test the platform</p>",
                         "pub_date": "2026-07-20T08:33:12.493250-04:00"}},
        ]}}
        with patch.object(WN, "fetch_json", return_value=payload):
            rows = list(WN.collect_working_nomads(None))
        self.assertEqual(rows[0]["title"], "QA Engineer")
        self.assertEqual(rows[0]["company"], "Acme Labs")
        self.assertEqual(rows[0]["location"], "Ireland, Spain")
        self.assertEqual(
            rows[0]["url"],
            "https://www.workingnomads.com/jobs/qa-engineer-acme-labs-1740290")

    def test_working_nomads_rejects_wrong_envelope(self):
        for bad in ({"jobs": []}, {"hits": []}, {"hits": {"hits": {}}}, []):
            with patch.object(WN, "fetch_json", return_value=bad):
                with self.assertRaisesRegex(ValueError, "unexpected response shape"):
                    list(WN.collect_working_nomads(None))

    def test_working_nomads_skips_rows_without_a_usable_slug(self):
        # No slug -> no URL we can build -> normalize_job drops the row anyway.
        payload = {"hits": {"hits": [
            {"_source": {"title": "No Slug", "company": "X", "slug": ""}},
            {"_source": {"title": "", "company": "X", "slug": "empty-title"}},
            {"not_source": {}},
            {"_source": {"title": "Good", "company": "X", "slug": "good"}},
        ]}}
        with patch.object(WN, "fetch_json", return_value=payload):
            rows = list(WN.collect_working_nomads(None))
        self.assertEqual([r["title"] for r in rows], ["No Slug", "Good"])
        self.assertEqual(rows[0]["url"], "")
        self.assertIsNone(SRC.normalize_job(rows[0], "WN"))

    def test_working_nomads_quotes_a_hostile_slug_into_one_path_segment(self):
        payload = {"hits": {"hits": [
            {"_source": {"title": "Evil", "company": "X",
                         "slug": "../../admin?x=1#f"}},
        ]}}
        with patch.object(WN, "fetch_json", return_value=payload):
            rows = list(WN.collect_working_nomads(None))
        url = rows[0]["url"]
        self.assertTrue(url.startswith("https://www.workingnomads.com/jobs/"))
        for char in ("?", "#", "/../"):
            self.assertNotIn(char, url[len("https://www.workingnomads.com/jobs/"):])

    def test_working_nomads_falls_back_to_company_slug(self):
        payload = {"hits": {"hits": [
            {"_source": {"title": "T", "company": "",
                         "company_slug": "bright-vision-technologies",
                         "slug": "t-1"}},
        ]}}
        with patch.object(WN, "fetch_json", return_value=payload):
            rows = list(WN.collect_working_nomads(None))
        self.assertEqual(rows[0]["company"], "Bright Vision Technologies")

    def test_wwr_splits_company_from_title(self):
        feed = (b'<?xml version="1.0"?><rss><channel>'
                b'<item><title>Acme Corp: Senior Data Analyst</title>'
                b'<link>https://weworkremotely.com/j/1</link>'
                b'<pubDate>Sat, 19 Jul 2026 08:00:00 +0000</pubDate>'
                b'<description>Write docs</description></item>'
                b'<item><title>No Colon Role</title>'
                b'<link>https://weworkremotely.com/j/2</link>'
                b'<description>x</description></item>'
                # Multi-colon: split on the FIRST colon only.
                b'<item><title>Globex: Senior Engineer: Platform Team</title>'
                b'<link>https://weworkremotely.com/j/3</link>'
                b'<description>y</description></item></channel></rss>')
        with patch.object(F, "fetch_feed_bytes", return_value=feed), quiet():
            rows = list(WWR.collect_wwr(None))
        self.assertEqual(rows[0]["company"], "Acme Corp")
        self.assertEqual(rows[0]["title"], "Senior Data Analyst")
        self.assertEqual(rows[0]["date"], "2026-07-19")  # RFC-822 → ISO
        self.assertEqual(rows[1]["company"], "")
        self.assertEqual(rows[1]["title"], "No Colon Role")
        self.assertEqual(rows[2]["company"], "Globex")
        self.assertEqual(rows[2]["title"], "Senior Engineer: Platform Team")

    def test_json_helper_fails_closed_on_invalid_json(self):
        response = F.SafeResponse(200, {"Content-Type": "application/json"}, b"not json")
        with patch.object(F, "fetch_url_safe", return_value=response):
            with self.assertRaises(HTTP.SourceFetchError):
                HTTP.fetch_json("https://example.com/api")


class TestConfigSources(unittest.TestCase):
    """The optional `sources` block: boards default ON; the block only carries
    explicit overrides; mistakes fail loudly instead of being ignored."""

    def test_absent_block_means_everything_on(self):
        self.assertEqual(C._source_toggles(None), {})
        conf = C.Config(model="m", ollama_base="http://o", search=C.Search(),
                        cv_path=None, dashboard_port=1, extra_rss=[],
                        extra_jobspy_locations=[], profile_text="p")
        for key in SOURCE_KEYS:
            self.assertTrue(conf.source_on(key))

    def test_overrides_are_respected_and_partial(self):
        toggles = C._source_toggles({"remoteok": False, "wwr": True})
        self.assertEqual(toggles, {"remoteok": False, "wwr": True})
        conf = C.Config(model="m", ollama_base="http://o", search=C.Search(),
                        cv_path=None, dashboard_port=1, extra_rss=[],
                        extra_jobspy_locations=[], profile_text="p",
                        sources=toggles)
        self.assertFalse(conf.source_on("remoteok"))
        self.assertTrue(conf.source_on("himalayas"))  # unlisted -> default on

    def test_unknown_key_fails_loudly(self):
        with self.assertRaisesRegex(C.ConfigError, "unknown key"):
            C._source_toggles({"remotok": True})  # typo must not be ignored

    def test_non_boolean_value_fails_loudly(self):
        with self.assertRaisesRegex(C.ConfigError, "true or false"):
            C._source_toggles({"remoteok": "false"})

    def test_non_object_block_fails_loudly(self):
        with self.assertRaisesRegex(C.ConfigError, "object of boolean toggles"):
            C._source_toggles(["remoteok"])


class TestConfigDefaults(unittest.TestCase):
    """config.load is exercised indirectly elsewhere; here just the pure helper."""
    def test_str_list_filters_and_coerces(self):
        self.assertEqual(C._str_list(["a", " b ", "", 3, None, "c"]), ["a", "b", "3", "c"])
        self.assertEqual(C._str_list("not a list"), [])


if __name__ == "__main__":
    unittest.main()
