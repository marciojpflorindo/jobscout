"""Himalayas public-API adapter (large remote aggregator, no auth).

Two API behaviours this works around, both measured live:

  * `limit` is silently capped at 20. Asking for 100 returns 20 rows and echoes
    `"limit": 20` in the envelope, against a `totalCount` in the tens of
    thousands — a single-call adapter would ingest 20 rows per run and
    MAX_API_ENTRIES would never bind. We page with `offset` instead, advancing
    by what the server actually returned so a future cap change needs no edit.
  * `companyName` can be the literal placeholder "name" (measured: every row of
    the newest page). That is not cosmetic: the judge would be handed
    "COMPANY: name", and company-based dedup/feedback can't match those rows
    against real history. `companySlug` is correct on exactly those rows, so we
    fall back to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from .http import fetch_json

HIMALAYAS_API = "https://himalayas.app/jobs/api"
MAX_API_ENTRIES = 100
PAGE_SIZE = 20               # the server's real cap; asking for more is ignored
MAX_PAGES = 10               # runaway guard: a server that never empties can't loop forever

# The placeholder the API returns instead of a company name (see the module docstring).
# Matched exactly rather than heuristically — it is a literal field name leaking into
# the payload, not a class of bad values, and a real employer called "name" would get
# the same slug fallback anyway.
PLACEHOLDER_COMPANY = "name"


def _date(value: object) -> object:
    """pubDate is unix seconds; render as an ISO date, else pass through as-is."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return value


def _location(value: object) -> str:
    """locationRestrictions is an array of region strings; join, else "Remote"."""
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
        if parts:
            return ", ".join(parts)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "Remote"


def _company(row: dict) -> str:
    """The employer name, falling back to companySlug when the API sends its
    "name" placeholder. The slug is hyphenated ("bright-vision-technologies"),
    so hyphens become spaces before title-casing."""
    name = row.get("companyName")
    name = name.strip() if isinstance(name, str) else ""
    if name and name.lower() != PLACEHOLDER_COMPANY:
        return name
    slug = row.get("companySlug")
    slug = slug.strip() if isinstance(slug, str) else ""
    return slug.replace("-", " ").title() if slug else ""


def collect_himalayas(_conf) -> Iterator[dict]:
    """Page through the feed until MAX_API_ENTRIES, a short page, or MAX_PAGES.

    A malformed FIRST page raises (the caller reports the board as failed); a
    malformed later page just ends the walk, so a mid-walk hiccup keeps the rows
    already collected instead of losing the whole board.
    """
    sent = 0
    offset = 0
    for page in range(MAX_PAGES):
        data = fetch_json(f"{HIMALAYAS_API}?limit={PAGE_SIZE}&offset={offset}")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            if page == 0:
                raise ValueError("unexpected response shape")
            return
        rows = data["jobs"]
        if not rows:
            return
        for row in rows:
            if sent >= MAX_API_ENTRIES:
                return
            if not isinstance(row, dict) or not row.get("title"):
                continue
            sent += 1
            yield {
                "source": "API/Himalayas",
                "title": row.get("title"),
                "company": _company(row),
                "location": _location(row.get("locationRestrictions")),
                # No plain url field: the posting link is applicationLink, then guid.
                "url": row.get("applicationLink") or row.get("guid"),
                "description": row.get("description") or row.get("excerpt"),
                "date": _date(row.get("pubDate")),
            }
        # Advance by what the server actually returned, not by PAGE_SIZE: if the cap
        # is ever raised, this keeps paging correctly with no edit here.
        offset += len(rows)
