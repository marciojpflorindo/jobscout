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


def save_scored(scored: dict[str, dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SCORED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(scored, indent=2, sort_keys=True), encoding="utf-8")
    fd = os.open(str(tmp), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(SCORED_FILE)
