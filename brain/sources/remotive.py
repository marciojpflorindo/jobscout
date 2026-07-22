"""Remotive public-API adapter (large general remote board)."""

from __future__ import annotations

from collections.abc import Iterator

from .http import fetch_json

# limit is Remotive's own cap; we still hard-cap locally with MAX_API_ENTRIES.
REMOTIVE_API = "https://remotive.com/api/remote-jobs?limit=100"
MAX_API_ENTRIES = 100


def collect_remotive(_conf) -> Iterator[dict]:
    # Jobs live under the top-level "jobs" array (verified against the live API).
    data = fetch_json(REMOTIVE_API)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("unexpected response shape")

    for row in data["jobs"][:MAX_API_ENTRIES]:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        yield {
            "source": "API/Remotive",
            "title": row.get("title"),
            "company": row.get("company_name"),
            "location": row.get("candidate_required_location") or "Remote",
            "url": row.get("url"),
            "description": row.get("description"),
            "date": row.get("publication_date"),
        }
