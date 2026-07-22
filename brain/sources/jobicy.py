"""Jobicy public-API adapter (remote board with salary/geo metadata)."""

from __future__ import annotations

from collections.abc import Iterator

from .http import fetch_json

# count is Jobicy's own page size (max 50); we still hard-cap locally.
JOBICY_API = "https://jobicy.com/api/v2/remote-jobs?count=50"
MAX_API_ENTRIES = 100


def collect_jobicy(_conf) -> Iterator[dict]:
    # Jobs live under the top-level "jobs" array (verified against the live API).
    data = fetch_json(JOBICY_API)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("unexpected response shape")

    for row in data["jobs"][:MAX_API_ENTRIES]:
        if not isinstance(row, dict) or not row.get("jobTitle"):
            continue
        yield {
            "source": "API/Jobicy",
            "title": row.get("jobTitle"),
            "company": row.get("companyName"),
            "location": row.get("jobGeo") or "Remote",
            "url": row.get("url"),
            # jobDescription is full HTML; jobExcerpt is the short summary.
            "description": row.get("jobExcerpt") or row.get("jobDescription"),
            "date": row.get("pubDate"),
        }
