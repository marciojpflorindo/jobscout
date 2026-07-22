"""Arbeitnow public-API adapter (Europe-focused remote board)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from .http import fetch_json

ARBEITNOW_API = "https://www.arbeitnow.com/api/job-board-api"
MAX_API_ENTRIES = 100


def _iso_from_epoch(value: object) -> object:
    """created_at is unix seconds; render as an ISO date. A string (should the
    API ever switch to ISO) passes through as-is; anything else becomes ""."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return value if isinstance(value, str) else ""


def collect_arbeitnow(_conf) -> Iterator[dict]:
    # Jobs live under the top-level "data" array (verified against the live API).
    data = fetch_json(ARBEITNOW_API)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("unexpected response shape")

    for row in data["data"][:MAX_API_ENTRIES]:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        location = str(row.get("location") or "").strip()
        if row.get("remote"):
            location = f"{location} — Remote" if location else "Remote"
        yield {
            "source": "API/Arbeitnow",
            "title": row.get("title"),
            "company": row.get("company_name"),
            "location": location or "Remote",
            "url": row.get("url"),
            "description": row.get("description"),
            "date": _iso_from_epoch(row.get("created_at")),
        }
