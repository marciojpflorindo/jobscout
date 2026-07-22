"""Indeed and LinkedIn adapters backed by python-jobspy.

One adapter serves two registry keys ("indeed", "linkedin") so each board can be
toggled independently while keeping the query-major call order. Every JobSpy call
is individually wrapped: one bad query/site/location logs a warning and is
skipped — a run never crashes on a board.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator

JOBSPY_SITES = ("indeed", "linkedin")
RESULTS_WANTED = 15
HOURS_OLD = 168                  # one week
JOBSPY_PAUSE = 2                 # politeness between JobSpy calls
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


def scrape_jobspy(search_term: str, site: str, country: str, city: str,
                  is_remote: bool, location_override: str | None = None) -> Iterator[dict]:
    """One JobSpy query against one site. Yields nothing on any error (logged)."""
    if site not in JOBSPY_SITES:
        _warn(f"Unsupported JobSpy site: {site}")
        return
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
            yield {
                "source": f"JobSpy/{site}",
                "title": r.get("title"),
                "company": r.get("company"),
                "location": loc or ("Remote" if is_remote else location),
                "url": r.get("job_url"),
                "description": r.get("description"),
                "date": r.get("date_posted"),
            }
    except Exception as e:  # noqa: BLE001 — any source failure is non-fatal
        _warn(f"JobSpy [{site}] failed for {search_term!r} ({tag!r}): {e!r}")


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


def _extra_locations(conf, sites: list[str]) -> Iterator[dict]:
    """The profile's queries against the extra locations the user listed —
    additional countries or a region alias from onboarding (e.g. "Mexico", "EU")
    or free-form places from advanced config (e.g. "Berlin, Germany").

    Each entry drives its OWN Indeed national domain (the part after the last
    comma, so "Berlin, Germany" → Germany; a bare entry is the country itself) —
    NOT the primary country. Passing the primary country was a bug: every extra
    country then searched the primary country's Indeed instead of its own."""
    search = conf.search
    is_remote = search.remote_preference == "remote-only"
    locations = expand_locations(conf.extra_jobspy_locations)
    if len(locations) > MAX_EXTRA_LOCATIONS:
        _warn(f"{len(locations)} extra locations given; searching only the first "
              f"{MAX_EXTRA_LOCATIONS} (raise MAX_EXTRA_LOCATIONS to change).")
        locations = locations[:MAX_EXTRA_LOCATIONS]
    total_calls = len(locations) * len(search.queries) * len(sites)
    if total_calls:
        print(f"  Extra market plan: {len(locations)} location(s) × "
              f"{len(search.queries)} search term(s) × {len(sites)} boards "
              f"= {total_calls} board calls.", flush=True)
    for loc_i, loc in enumerate(locations, 1):
        print(f"  Extra market {loc_i}/{len(locations)}: {loc}", flush=True)
        # Indeed's national domain comes from the entry itself: the last non-empty
        # comma segment ("Berlin, Germany" → Germany; "Mexico," → Mexico); never
        # let a stray comma leak back in as the country.
        parts = [p.strip() for p in loc.split(",") if p.strip()]
        country = parts[-1] if parts else ""
        for query in search.queries:
            for site in sites:
                yield from scrape_jobspy(query, site, country, "",
                                         is_remote, location_override=loc)
                time.sleep(JOBSPY_PAUSE)


def collect_jobspy(conf) -> Iterator[dict]:
    """Primary-market pass, then any extra markets — query-major, per-site toggles."""
    sites = [site for site in JOBSPY_SITES if conf.source_on(site)]
    if not sites:
        return
    search = conf.search
    is_remote = search.remote_preference == "remote-only"

    base_calls = len(search.queries) * len(sites)
    print(f"  Search plan: {len(search.queries)} search term(s) × "
          f"{len(sites)} board(s) = {base_calls} board calls.", flush=True)
    if base_calls:
        print(f"  Each board call asks for up to {RESULTS_WANTED} recent jobs; "
              f"JobScout waits {JOBSPY_PAUSE}s between calls.", flush=True)
    for query in search.queries:
        for site in sites:
            yield from scrape_jobspy(query, site, search.country, search.city, is_remote)
            time.sleep(JOBSPY_PAUSE)

    if conf.extra_jobspy_locations:
        yield from _extra_locations(conf, sites)
