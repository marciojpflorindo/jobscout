"""Thin HTTP client for the local dashboard's /api endpoints (stdlib only).

The dashboard is localhost, single-user, no auth — so this is plain JSON over
http://127.0.0.1:<port>. Three calls the brain needs:
  exclusion()       GET  /api/links   -> known links to skip + past reject reasons
  ingest(rows)      POST /api/ingest  -> publish survivors as "Potential" rows
  reject(items)     POST /api/reject  -> append model 'no' verdicts to the ledger
The server is the single source of truth for "already considered"; exclusion is
best-effort (a down dashboard warns and the brain proceeds rather than blocking).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

TIMEOUT = 15
# Statuses whose stored note we treat as rejection feedback (mirrors the server's
# /api/links: rejected-ledger entries carry status "no").
REJECTED_STATUSES = {"no", "Rejected", "Declined"}


@dataclass
class Exclusion:
    links: set[str]            # normalised links already on the board / rejected
    rejection_reasons: list[str]  # distinct 'why' notes from rejected entries


class DashboardError(RuntimeError):
    pass


def _norm(link: str) -> str:
    return (link or "").strip().rstrip("/")


def _get(base: str, path: str) -> object:
    req = urllib.request.Request(f"{base}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise DashboardError(str(e)) from e


def _post(base: str, path: str, payload: object) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise DashboardError(str(e)) from e
    return body if isinstance(body, dict) else {}


def exclusion(base: str) -> Exclusion:
    """Fetch the 'already considered' set + rejection reasons. Best-effort: on
    any failure returns an empty Exclusion (caller proceeds without it)."""
    try:
        data = _get(base, "/api/links")
    except DashboardError:
        return Exclusion(links=set(), rejection_reasons=[])
    entries = data.get("exclude") if isinstance(data, dict) else None
    links: set[str] = set()
    reasons: list[str] = []
    seen_reason: set[str] = set()
    if isinstance(entries, list):
        for e in entries:
            if not isinstance(e, dict):
                continue
            link = _norm(e.get("link"))
            if link:
                links.add(link)
            status = str(e.get("status") or "").strip()
            why = str(e.get("why") or "").strip()
            if status in REJECTED_STATUSES and why and why.lower() not in seen_reason:
                seen_reason.add(why.lower())
                reasons.append(why)
    return Exclusion(links=links, rejection_reasons=reasons)


def ingest(base: str, rows: list[dict]) -> dict:
    """Publish survivor rows. Server forces Status=Potential and dedups by link."""
    return _post(base, "/api/ingest", rows)


def reject(base: str, items: list[dict]) -> dict:
    """Append model 'no' verdicts ({link, reason, source}) to the reject ledger."""
    return _post(base, "/api/reject", items)
