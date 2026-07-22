"""Working Nomads adapter (large general remote board, no auth).

WHY THE ODD ENDPOINT — the obvious one is a trap. `api/exposed_jobs/` looks like
the public API but returns only ~33 rows (mostly Development), and it ignores
both `?limit=` and `?category=`: asking for 500 returns the same 33.

The board's own search runs off an Elasticsearch index instead. The jobs page
sets `elasticRootUrl` + `elasticIndex = 'jobsapi'` inline, so the browser
queries `/jobsapi/_search` directly — it is the site's read path, not a private
endpoint. It carries thousands of live jobs with real company names and the full
ad text.

Constraints this shape is built around:
  * The SSRF-guarded fetcher is GET-only, so this uses Elasticsearch's URI-search
    form — query, sort and paging all as URL params; no JSON request body.
  * `_source_includes` trims the response to the fields used here: `description`
    alone dwarfs every other field, and pulling unused fields would walk the
    reply toward the byte cap.
  * `q=expired:false` is a real filter, not decoration (expired:true matches 0).
  * Sorting newest-first is the point of the call: the index holds thousands of
    rows and we only take MAX_API_ENTRIES, so unsorted would sample arbitrarily.

Being an internal endpoint, it is unversioned and may change without notice —
the same standing risk as any RSS feed. A shape change raises ValueError, which
the central collector reports as a failed board without stopping the run.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import quote

from .http import fetch_json

WORKING_NOMADS_SEARCH = "https://www.workingnomads.com/jobsapi/_search"
# The canonical posting page. Verified: /jobs/<slug> is 200, while /job/<slug>
# and /remote-jobs/<slug> both 404 — the pattern is not interchangeable.
JOB_URL_BASE = "https://www.workingnomads.com/jobs/"
MAX_API_ENTRIES = 100
# Only the fields mapped below, to keep the response well clear of the byte cap.
SOURCE_FIELDS = "title,company,company_slug,slug,locations,pub_date"
QUERY = (f"?q=expired:false&sort=pub_date:desc&size={MAX_API_ENTRIES}"
         f"&_source_includes={SOURCE_FIELDS},description")


def _location(value: object) -> str:
    """`locations` is an array of country strings; join, else "Remote"."""
    if isinstance(value, list):
        parts = [str(item).strip() for item in value
                 if isinstance(item, str) and item.strip()]
        if parts:
            return ", ".join(parts)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "Remote"


def _url(slug: object) -> str:
    """The posting page for a slug, or "" when the row has none (normalize_job
    then drops the row). The slug is untrusted feed data, so it is percent-quoted
    with no safe characters: that keeps it a single path segment and stops a
    hostile value adding a query, fragment, or path traversal to our URL."""
    if not isinstance(slug, str) or not slug.strip():
        return ""
    return JOB_URL_BASE + quote(slug.strip(), safe="")


def _company_from_slug(slug: object) -> str:
    """Hyphenated slug -> a display name ("bright-vision-technologies" ->
    "Bright Vision Technologies")."""
    if not isinstance(slug, str) or not slug.strip():
        return ""
    return slug.strip().replace("-", " ").title()


def collect_working_nomads(_conf) -> Iterator[dict]:
    # Elasticsearch envelope: {"hits": {"hits": [{"_source": {...}}, ...]}}
    data = fetch_json(WORKING_NOMADS_SEARCH + QUERY)
    if not isinstance(data, dict):
        raise ValueError("unexpected response shape")
    hits = data.get("hits")
    if not isinstance(hits, dict) or not isinstance(hits.get("hits"), list):
        raise ValueError("unexpected response shape")

    for hit in hits["hits"][:MAX_API_ENTRIES]:
        if not isinstance(hit, dict):
            continue
        row = hit.get("_source")
        if not isinstance(row, dict) or not row.get("title"):
            continue
        yield {
            "source": "API/Working Nomads",
            "title": row.get("title"),
            # company_slug is the fallback for the same reason Himalayas needs
            # one: a feed-sourced row can arrive without a display name.
            "company": row.get("company") or _company_from_slug(row.get("company_slug")),
            "location": _location(row.get("locations")),
            "url": _url(row.get("slug")),
            "description": row.get("description"),
            "date": row.get("pub_date"),
        }
