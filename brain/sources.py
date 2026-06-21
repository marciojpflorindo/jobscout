"""Job sourcing — JobSpy (Indeed + LinkedIn) + RemoteOK, driven by the profile.

Every source is wrapped so one bad source (a board change, a timeout, an
unsupported country) logs a warning and is skipped — a run never crashes on a
source. Scraped rows are semi-trusted: every field is coerced to str and capped
here before anything downstream uses it. Job dicts share one shape:
  {source, title, company, location, url, description, date}
"""

from __future__ import annotations

import re
import sys
import time

import requests

import fetch  # SSRF-guarded fetching, reused for user-supplied RSS feeds

JOBSPY_SITES = ("indeed", "linkedin")
RESULTS_WANTED = 15
HOURS_OLD = 168                  # one week
JOBSPY_PAUSE = 2                 # politeness between JobSpy calls
DESC_CAP = 2000                  # scraped teaser cap (full text fetched later)
TITLE_CAP = 300                  # a sane title bound; junk/hostile feeds can emit huge ones

REMOTEOK_API = "https://remoteok.com/api"
API_HEADERS = {"User-Agent": "Mozilla/5.0 (JobScout; personal use)"}
REQUEST_TIMEOUT = 15
MAX_API_ENTRIES = 100
MAX_RSS_ENTRIES = 100            # cap entries taken from any one user feed
MAX_EXTRA_LOCATIONS = 40         # each adds queries×sites scrape calls — bound the run
                                 # (high enough to hold an expanded region like "EU")

# Region aliases: a user can type "EU" instead of listing the member countries.
# Names match JobSpy's accepted country strings exactly (it rejects "EU" itself).
_EU_COUNTRIES = (
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia", "denmark",
    "estonia", "finland", "france", "germany", "greece", "hungary", "ireland",
    "italy", "latvia", "lithuania", "luxembourg", "malta", "netherlands", "poland",
    "portugal", "romania", "slovakia", "slovenia", "spain", "sweden",
)
REGION_ALIASES = {
    "eu": _EU_COUNTRIES,
    "e.u.": _EU_COUNTRIES,
    "european union": _EU_COUNTRIES,
    # "Europe" goes a bit wider than the political EU (still JobSpy-valid names).
    "europe": _EU_COUNTRIES + ("uk", "switzerland", "norway"),
}


def _warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr, flush=True)


def _row(source, title, company, location, url, description, date) -> dict:
    return {
        "source": source,
        "title": str(title or "").strip()[:TITLE_CAP],
        "company": str(company or "").strip()[:TITLE_CAP],
        "location": str(location or "").strip()[:TITLE_CAP] or "Unspecified",
        "url": str(url or "").strip(),
        "description": str(description or "")[:DESC_CAP],
        "date": str(date or "").strip(),
    }


def scrape_jobspy(search_term: str, site: str, country: str, city: str,
                  is_remote: bool, location_override: str | None = None) -> list[dict]:
    """One JobSpy query against one site. Returns [] on any error (logged)."""
    out: list[dict] = []
    location = location_override or (f"{city}, {country}" if city else country)
    tag = location_override or (location or "anywhere")
    try:
        from jobspy import scrape_jobs  # imported lazily so non-brain code needs no dep
        print(f"  JobSpy [{site}] \"{search_term}\" ({tag})...", flush=True)
        kwargs: dict = {
            "site_name": [site],
            "search_term": search_term,
            "results_wanted": RESULTS_WANTED,
            "hours_old": HOURS_OLD,
            "description_format": "markdown",
        }
        if is_remote:
            kwargs["is_remote"] = True
        if location:
            kwargs["location"] = location
        # Indeed needs a country to pick its national domain; LinkedIn ignores it.
        if site == "indeed" and country:
            kwargs["country_indeed"] = country
        df = scrape_jobs(**kwargs)
        for _, r in df.iterrows():
            loc = str(r.get("location", "") or "").strip()
            out.append(_row(
                f"JobSpy/{site}", r.get("title"), r.get("company"),
                loc or ("Remote" if is_remote else location),
                r.get("job_url"), r.get("description"), r.get("date_posted")))
    except Exception as e:  # noqa: BLE001 — any source failure is non-fatal
        _warn(f"JobSpy [{site}] failed for \"{search_term}\" ({tag}): {e}")
    return out


def scrape_remoteok() -> list[dict]:
    """RemoteOK public feed (fixed trusted host). Global-remote, tech-skewed —
    we keep everything and let the judge filter. The first array element is a
    metadata object and is skipped."""
    out: list[dict] = []
    try:
        print("  API: RemoteOK...", flush=True)
        resp = requests.get(REMOTEOK_API, headers=API_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            _warn("RemoteOK: unexpected response shape")
            return out
        for row in data[:MAX_API_ENTRIES + 1]:
            if not isinstance(row, dict) or not row.get("position"):
                continue  # skips leading metadata object + malformed rows
            loc = str(row.get("location") or "").strip()
            out.append(_row(
                "API/RemoteOK", row.get("position"), row.get("company"),
                loc or "Remote", row.get("url") or row.get("apply_url"),
                row.get("description"), row.get("date")))
    except requests.exceptions.Timeout:
        _warn("RemoteOK API timeout")
    except Exception as e:  # noqa: BLE001
        _warn(f"RemoteOK API error: {e}")
    return out


def expand_locations(locations: list[str]) -> list[str]:
    """Expand region aliases ("EU" → the member countries JobSpy supports) and
    de-duplicate case-insensitively, preserving order. Unknown entries pass
    through unchanged — JobSpy validates them and skips any it doesn't recognise."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in locations:
        e = entry.strip()
        if not e:
            continue
        expanded = REGION_ALIASES.get(e.lower())
        if expanded is not None:
            _warn(f"'{e}' expands to {len(expanded)} countries — this makes the "
                  f"run longer.")
        for loc in (expanded or (e,)):
            key = loc.lower()
            if key not in seen:
                seen.add(key)
                out.append(loc)
    return out


def scrape_extra_locations(search, locations: list[str]) -> list[dict]:
    """Run the profile's queries against the extra locations the user listed —
    additional countries or a region alias from onboarding (e.g. "Mexico", "EU")
    or free-form places from advanced config (e.g. "Berlin, Germany").

    Each entry drives its OWN Indeed national domain (the part after the last
    comma, so "Berlin, Germany" → Germany; a bare entry is the country itself) —
    NOT the primary country. Passing the primary country was a bug: every extra
    country then searched the primary country's Indeed instead of its own. Each
    query×site×location is its own JobSpy call (try/except inside `scrape_jobspy`),
    so one bad location just logs a warning and is skipped."""
    is_remote = search.remote_preference == "remote-only"
    locations = expand_locations(locations)
    if len(locations) > MAX_EXTRA_LOCATIONS:
        _warn(f"{len(locations)} extra locations given; searching only the first "
              f"{MAX_EXTRA_LOCATIONS} (raise MAX_EXTRA_LOCATIONS to change).")
        locations = locations[:MAX_EXTRA_LOCATIONS]
    out: list[dict] = []
    for loc in locations:
        # Indeed's national domain comes from the entry itself, not the primary
        # country. Take the last non-empty comma segment ("Berlin, Germany" →
        # Germany; "Mexico," → Mexico; a bare entry is the country); never let a
        # stray comma leak back in as the country.
        parts = [p.strip() for p in loc.split(",") if p.strip()]
        country = parts[-1] if parts else ""
        for query in search.queries:
            for site in JOBSPY_SITES:
                out += scrape_jobspy(query, site, country, "",
                                     is_remote, location_override=loc)
                time.sleep(JOBSPY_PAUSE)
    return out


def scrape_rss(feed_url: str) -> list[dict]:
    """One user-supplied RSS/Atom feed.

    Trust: the URL is user-written (trusted enough to attempt) but the host and
    the feed body are HOSTILE. So the fetch goes through `fetch.fetch_feed_bytes`
    (the SSRF + timeout + size-cap choke point), the parser is handed the
    already-fetched bytes (never the URL — feedparser would otherwise fetch it
    itself, unguarded), and every extracted field is coerced + capped via `_row`.
    Returns [] on any error (logged); a broken feed never crashes a run.
    """
    out: list[dict] = []
    try:
        import feedparser  # lazy: only the RSS path pulls this dependency
        print(f"  RSS: {feed_url}...", flush=True)
        raw = fetch.fetch_feed_bytes(feed_url)
        if not raw:
            _warn(f"RSS feed unreachable or blocked: {feed_url}")
            return out
        feed = feedparser.parse(raw)
        for entry in feed.entries[:MAX_RSS_ENTRIES]:
            summary = entry.get("summary") or entry.get("description") or ""
            out.append(_row(
                "RSS", entry.get("title"), entry.get("author"), "",
                entry.get("link"), summary,
                entry.get("published") or entry.get("updated")))
    except Exception as e:  # noqa: BLE001 — a bad feed is non-fatal
        _warn(f"RSS feed failed ({feed_url}): {e}")
    return out


def collect(search, extra_rss: list[str] | None = None,
            extra_jobspy_locations: list[str] | None = None) -> list[dict]:
    """Run every source for the profile's search settings. `search` is a
    config.Search; `extra_rss`/`extra_jobspy_locations` come from the advanced
    config block. Resilient: a failing source contributes [] and is skipped."""
    is_remote = search.remote_preference == "remote-only"
    jobs: list[dict] = []

    print("=== JobSpy (Indeed + LinkedIn) ===", flush=True)
    for query in search.queries:
        for site in JOBSPY_SITES:
            jobs += scrape_jobspy(query, site, search.country, search.city, is_remote)
            time.sleep(JOBSPY_PAUSE)

    print("=== RemoteOK ===", flush=True)
    jobs += scrape_remoteok()

    if extra_jobspy_locations:
        print("=== Extra JobSpy locations ===", flush=True)
        jobs += scrape_extra_locations(search, extra_jobspy_locations)

    if extra_rss:
        print("=== Extra RSS feeds ===", flush=True)
        for feed_url in extra_rss:
            jobs += scrape_rss(feed_url)

    return jobs


def deduplicate(jobs: list[dict]) -> list[dict]:
    """Drop duplicate postings by URL, then by title|company.

    A posting with neither title nor company yields the empty key "|", which
    can't identify anything — skip the key check for those so distinct URL-only
    jobs aren't all silently collapsed into the first empty-keyed one.
    """
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    out: list[dict] = []
    for j in jobs:
        url = (j.get("url") or "").strip().rstrip("/")
        key = f"{(j.get('title') or '').lower().strip()}|{(j.get('company') or '').lower().strip()}"
        if url and url in seen_urls:
            continue
        if key != "|" and key in seen_keys:
            continue
        if url:
            seen_urls.add(url)
        if key != "|":
            seen_keys.add(key)
        out.append(j)
    return out
