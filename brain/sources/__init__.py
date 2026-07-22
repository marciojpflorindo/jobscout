"""Registry-driven job sourcing with per-board failure isolation and health.

Every source is wrapped so one bad board (a format change, a timeout, a dead
feed) logs a warning and is skipped — a run never crashes on a source. Every
yielded row is hostile: normalize_job caps and validates it here before anything
downstream sees it.
"""

from __future__ import annotations

import sys

from .base import Job, SourceSpec, normalize_job
from .registry import SOURCE_BY_KEY, SOURCES

MAX_RESULTS_PER_SOURCE = 10_000

# --- per-run source health ----------------------------------------------------
# A dead board does not raise — it just yields less. A feed URL that now serves
# an HTML page, or an API that renamed a field, produces a clean-looking run
# that quietly returns nothing, and reading the code can't catch that: the code
# is correct, the world moved. Counting what each source ACTUALLY produced is
# what makes the difference visible. collect() fills HEALTH; run.py prints it.
HEALTH: list[dict] = []
# Below this share of rows carrying a company name, a source is reported as
# degraded. An empty company is not cosmetic: it disables company-based dedup
# and rejection feedback for every row that source produces.
MIN_COMPANY_RATE = 0.5


def _warn(message: str) -> None:
    print(f"  ! {message}", file=sys.stderr, flush=True)


def _record_health(source: SourceSpec, usable: int, invalid: int, with_company: int) -> None:
    """Log one enabled source's yield, and warn NOW if it looks broken."""
    problem = ""
    if usable == 0:
        problem = "produced NO usable rows"
    elif (with_company / usable) < MIN_COMPANY_RATE:
        problem = f"{usable - with_company}/{usable} rows carry no company name"
    HEALTH.append({"key": source.key, "label": source.label, "rows": usable,
                   "invalid": invalid, "with_company": with_company, "problem": problem})
    if problem:
        _warn(f"{source.label} looks DEGRADED: {problem}")


def degraded() -> list[dict]:
    """The sources this run flagged. Empty when everything pulled its weight."""
    return [h for h in HEALTH if h["problem"]]


def health_summary() -> str:
    """One block for the end of the scrape: which enabled sources are working
    and which are silently dead."""
    if not HEALTH:
        return "Source health: no sources ran."
    bad = degraded()
    lines = [f"Source health: {len(HEALTH)} source(s) ran, {len(bad)} degraded."]
    # Degraded first, then by yield — the thing to act on leads.
    for h in sorted(HEALTH, key=lambda x: (not x["problem"], -x["rows"])):
        invalid = f", {h['invalid']} invalid" if h["invalid"] else ""
        note = f"   <-- {h['problem']}" if h["problem"] else ""
        lines.append(f"  {'!' if h['problem'] else ' '} {h['label'][:28]:28} "
                     f"{h['rows']:>5} row(s), {h['with_company']:>5} with a company"
                     f"{invalid}{note}")
    if bad:
        lines.append("  A degraded source is usually a feed URL that now answers 200 "
                     "with an HTML page, or an API that renamed a field. Check it: "
                     "brain/run.py --check-sources")
    return "\n".join(lines)


def collect(conf) -> list[Job]:
    """Run enabled registry adapters and normalize their hostile output."""
    jobs: list[Job] = []
    seen_collectors: set[int] = set()
    HEALTH.clear()
    for source in SOURCES:
        if not conf.source_on(source.key):
            continue
        if source.active is not None and not source.active(conf):
            continue
        collector_id = id(source.collect)
        if collector_id in seen_collectors:
            continue
        seen_collectors.add(collector_id)
        print(f"=== {source.label} ===", flush=True)
        invalid = 0
        collected = 0
        usable = 0
        with_company = 0
        rows = None
        try:
            rows = iter(source.collect(conf))
            for raw in rows:
                collected += 1
                job = normalize_job(raw, source.label)
                if job is None:
                    invalid += 1
                else:
                    jobs.append(job)
                    usable += 1
                    if job["company"].strip():
                        with_company += 1
                if collected >= MAX_RESULTS_PER_SOURCE:
                    _warn(f"{source.label} reached the "
                          f"{MAX_RESULTS_PER_SOURCE}-row cap; stopping")
                    break
        except Exception as exc:  # noqa: BLE001 -- one board must not stop a run
            _warn(f"{source.label} failed: {exc!r}")
        finally:
            close = getattr(rows, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 -- cleanup cannot stop the run
                    pass
        if invalid:
            _warn(f"{source.label} skipped {invalid} invalid row(s)")
        _record_health(source, usable, invalid, with_company)
    return jobs


def deduplicate(jobs: list[dict]) -> list[dict]:
    """Drop duplicate postings by URL, then by title+company when both exist.

    A title without a company is not a stable identity: unrelated feeds often
    publish the same generic title, so their distinct URLs must remain visible.
    """
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    out: list[dict] = []
    for job in jobs:
        url = (job.get("url") or "").strip().rstrip("/")
        title = (job.get("title") or "").lower().strip()
        company = (job.get("company") or "").lower().strip()
        key = f"{title}|{company}" if title and company else ""
        if url and url in seen_urls:
            continue
        if key and key in seen_keys:
            continue
        if url:
            seen_urls.add(url)
        if key:
            seen_keys.add(key)
        out.append(job)
    return out


__all__ = [
    "HEALTH",
    "Job",
    "MAX_RESULTS_PER_SOURCE",
    "MIN_COMPANY_RATE",
    "SOURCE_BY_KEY",
    "SOURCES",
    "SourceSpec",
    "collect",
    "deduplicate",
    "degraded",
    "health_summary",
    "normalize_job",
]
