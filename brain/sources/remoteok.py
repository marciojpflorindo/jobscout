"""RemoteOK public-API adapter (global remote feed, tech-skewed).

The first array element is a metadata/legal object, not a job — the
`row.get("position")` check skips it along with any malformed rows.
"""

from __future__ import annotations

from collections.abc import Iterator

from .http import fetch_json

REMOTEOK_API = "https://remoteok.com/api"
MAX_API_ENTRIES = 100


def collect_remoteok(_conf) -> Iterator[dict]:
    data = fetch_json(REMOTEOK_API)
    if not isinstance(data, list):
        raise ValueError("unexpected response shape")

    accepted = 0
    for row in data:
        if not isinstance(row, dict) or not row.get("position"):
            continue
        accepted += 1
        yield {
            "source": "API/RemoteOK",
            "title": row.get("position"),
            "company": row.get("company"),
            "location": row.get("location") or "Remote",
            "url": row.get("url") or row.get("apply_url"),
            "description": row.get("description"),
            "date": row.get("date"),
        }
        if accepted >= MAX_API_ENTRIES:
            break
