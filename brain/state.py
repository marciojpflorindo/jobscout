"""Idempotency state — which posting URLs the brain has already judged.

Keyed by URL so a re-run never re-scores (and never re-spends GPU on) a posting
it already decided, even if the dashboard row was deleted. Separate from the
dashboard's own exclusion set, which can be cleared independently. Atomic writes
(tmp + fsync + rename) so an interrupted run can't corrupt the ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from config import STATE_DIR

SCORED_FILE = STATE_DIR / "scored.json"
# Durable "outbox" of judged results not yet confirmed published to the dashboard.
# Written before every publish attempt so a failed/missing dashboard never loses a
# run's output; cleared once the dashboard accepts it. (See brain/run.py.)
PENDING_FILE = STATE_DIR / "pending.json"


def _job_id(job: dict) -> str:
    url = (job.get("url") or "").strip().rstrip("/")
    if url:
        return url
    title = (job.get("title") or "").lower().strip()
    company = (job.get("company") or "").lower().strip()
    if title or company:
        return f"{title}|{company}"
    # No URL and no title/company: hashing the whole posting keeps two distinct
    # junk jobs from colliding on a shared empty "|" id (which would make the
    # second look already-scored and silently skip it). Stable across runs, so
    # idempotency still holds.
    blob = json.dumps(job, sort_keys=True, ensure_ascii=False, default=str)
    return "hash:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()


def load_scored() -> dict[str, dict]:
    """Map of job_id -> {scored_at, verdict, score}. Missing/corrupt -> {}."""
    if not SCORED_FILE.exists():
        return {}
    try:
        data = json.loads(SCORED_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def already_scored(job: dict, scored: dict[str, dict]) -> bool:
    return _job_id(job) in scored


def record(job: dict, verdict: str, score: int, scored: dict[str, dict]) -> None:
    scored[_job_id(job)] = {
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": verdict,
        "score": score,
    }


def _atomic_write(path, text: str) -> None:
    """tmp + fsync + rename, so an interrupted write can't corrupt the target."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    fd = os.open(str(tmp), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(path)


def save_scored(scored: dict[str, dict]) -> None:
    _atomic_write(SCORED_FILE, json.dumps(scored, indent=2, sort_keys=True))


# --- pending publish outbox -------------------------------------------------

def load_pending() -> dict[str, list]:
    """Return the durable outbox as {'survivors': [...], 'rejects': [...]}.
    Missing/corrupt/malformed -> empty lists (the run still works)."""
    empty = {"survivors": [], "rejects": []}
    if not PENDING_FILE.exists():
        return empty
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    s, r = data.get("survivors"), data.get("rejects")
    return {
        "survivors": [x for x in s if isinstance(x, dict)] if isinstance(s, list) else [],
        "rejects": [x for x in r if isinstance(x, dict)] if isinstance(r, list) else [],
    }


def save_pending(survivors: list[dict], rejects: list[dict]) -> None:
    """Persist the outbox before a publish attempt. Atomic."""
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "survivors": survivors,
        "rejects": rejects,
    }
    _atomic_write(PENDING_FILE, json.dumps(payload, indent=2, ensure_ascii=False))


def clear_pending() -> None:
    """Remove the outbox once the dashboard has accepted everything."""
    try:
        PENDING_FILE.unlink()
    except FileNotFoundError:
        pass
