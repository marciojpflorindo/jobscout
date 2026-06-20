#!/usr/bin/env python3
"""JobScout brain — source -> exclude -> judge -> publish (one pipeline).

Flow:
  1. load config.json + profile.md
  2. ask the dashboard which links are already considered (+ past reject reasons)
  3. scrape the default sources (JobSpy Indeed+LinkedIn, RemoteOK), dedup
  4. drop anything already on the dashboard OR already judged locally — BEFORE
     any LLM call, so no GPU is spent re-judging a decided posting
  5. cheap heuristic pre-filter -> top N candidates
  6. fetch each candidate's full posting text (SSRF-guarded, capped, cached)
  7. judge each with the local model (fail closed on bad output), record state
  8. publish survivors (match/maybe) to the dashboard as "Potential"; send the
     'no' verdicts to the reject ledger so neither side re-judges them

Run:  python3 brain/run.py            (full run)
      python3 brain/run.py --top 25   (cap candidates judged)
      python3 brain/run.py --dry-run  (judge but don't publish)
"""

from __future__ import annotations

import argparse
import os
import sys

_BRAIN_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BRAIN_DIR)
sys.path.insert(0, _BRAIN_DIR)
sys.path.insert(0, _REPO_ROOT)   # so `ats` (the optional CV scorer) imports as a package

import config as cfg          # noqa: E402
import dashboard as dash      # noqa: E402
import fetch                  # noqa: E402
import heuristic              # noqa: E402
import notify                 # noqa: E402
import sources                # noqa: E402
import state                  # noqa: E402
from judge import Judge       # noqa: E402

from ats.cv import CVError, load_cv_text  # noqa: E402
from ats.scorer import CVScorer           # noqa: E402

DEFAULT_TOP = 30


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JobScout brain — find, judge, publish jobs.")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP,
                    help=f"max candidates to fetch + judge (default {DEFAULT_TOP})")
    ap.add_argument("--dry-run", action="store_true",
                    help="judge but do not publish to the dashboard")
    args = ap.parse_args(argv)

    try:
        conf = cfg.load()
    except cfg.ConfigError as e:
        # Onboarding hasn't run (or config is broken) — there is no ntfy config to
        # notify with, so just report and exit.
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    # One notification at the end covers every exit: a thrown error sends the
    # failure template, a clean finish sends new/none. `outcome` is None for a
    # dry run (a test, nothing published — don't ping).
    try:
        rc, outcome = _run_pipeline(conf, args)
    except Exception:
        notify.notify_run(conf.ntfy, "failure")
        raise  # keep the traceback — the failure ping says "check the terminal/log"
    if outcome is not None:
        notify.notify_run(conf.ntfy, outcome)
    return rc


def _run_pipeline(conf: "cfg.Config", args) -> tuple[int, str | None]:
    """The source -> exclude -> judge -> publish pipeline. Returns
    (exit_code, notify_outcome) where outcome is 'new'/'none'/'failure', or None
    when no notification should fire (a dry run)."""
    print(f"Model: {conf.model}   Dashboard: {conf.dashboard_base}")
    excl = dash.exclusion(conf.dashboard_base)
    if excl.links:
        print(f"Dashboard knows {len(excl.links)} link(s); "
              f"{len(excl.rejection_reasons)} reject reason(s) for feedback.")
    else:
        print("Dashboard exclusion empty or unreachable — proceeding without it.")

    # 3. source + dedup
    raw = sources.collect(conf.search, conf.extra_rss, conf.extra_jobspy_locations)
    print(f"\nRaw results: {len(raw)}")
    if not raw:
        print("No jobs collected from any source.", file=sys.stderr)
        return 1, "failure"
    jobs = sources.deduplicate(raw)
    print(f"After dedup: {len(jobs)}")

    # 4. exclude already-considered (dashboard) + already-judged (local state)
    scored = state.load_scored()
    before = len(jobs)
    jobs = [j for j in jobs
            if _norm(j.get("url", "")) not in excl.links
            and not state.already_scored(j, scored)]
    print(f"Excluded already-known/judged: {before - len(jobs)}; remaining: {len(jobs)}")
    if not jobs:
        print("Nothing new to judge.")
        return 0, "none"

    # 5. heuristic prefilter -> top N
    candidates = heuristic.rank(jobs, conf.search.queries, args.top)
    print(f"Candidates after heuristic prefilter (top {args.top}): {len(candidates)}")
    if not candidates:
        print("No candidates cleared the heuristic prefilter.")
        return 0, "none"

    # 6. full posting text
    print("Fetching full posting text...")
    with_text = 0
    for j in candidates:
        text = fetch.fetch_posting_text_cached(j["url"]) if j.get("url") else None
        j["_posting_text"] = (text or j.get("description") or "")[:fetch.MAX_PAGE_TEXT_CHARS]
        if text:
            with_text += 1
    print(f"Full text fetched: {with_text}/{len(candidates)}")

    # 7. judge (+ optional CV-fit scoring of survivors)
    judge = Judge(conf.model, conf.ollama_base, conf.profile_text, excl.rejection_reasons)
    cv_scorer = _build_cv_scorer(conf)
    survivors: list[dict] = []
    rejects: list[dict] = []
    errors = 0
    for i, j in enumerate(candidates, 1):
        verdict = judge.judge(j)
        if verdict is None:
            errors += 1
            print(f"[{i}/{len(candidates)}] ERROR  {j.get('title', '')[:50]!r}")
            continue
        v, sc, why = verdict["verdict"], verdict["score"], verdict["why"]
        state.record(j, v, sc, scored)
        flag = " [injection?]" if verdict.get("injection_suspected") else ""
        print(f"[{i}/{len(candidates)}] {v:5} {sc:3}{flag}  {j.get('title', '')[:50]!r}")
        if v == "no":
            rejects.append({"link": j.get("url", ""), "reason": why or "no",
                            "source": "jobscout"})
        else:
            note = f"{v} {sc}/100: {why}" if why else f"{v} {sc}/100"
            # Prompt-injection: surface and publish anyway — never auto-drop or
            # auto-cap (either would let a hostile posting bury or hide a job).
            if verdict.get("injection_suspected"):
                note += ("  |  ⚠️ This posting appears to contain text aimed at the "
                         "scorer — treat its score with skepticism")
            if cv_scorer is not None:
                cv = cv_scorer.score(j)
                if cv is not None:
                    note += f"  |  CV-fit {cv['score']}/100"
                    if cv["gaps"]:
                        note += f" (gaps: {cv['gaps']})"
            survivors.append({
                "Company": j.get("company", ""),
                "Role": j.get("title", ""),
                "Job link": j.get("url", ""),
                "Notes": note,
            })

    state.save_scored(scored)
    print(f"\nJudged {len(candidates)}: {len(survivors)} survivors, "
          f"{len(rejects)} rejected, {errors} errors.")

    # 8. publish
    if args.dry_run:
        print("Dry run — not publishing.")
        return 0, None  # a test run publishes nothing — don't notify
    added = _publish(conf.dashboard_base, survivors, rejects)
    # Binary outcome only — the body carries no count, just new-vs-none.
    return 0, ("new" if added > 0 else "none")


def _build_cv_scorer(conf: "cfg.Config") -> CVScorer | None:
    """Build the optional CV-fit scorer. Returns None (and prints one line) when
    no CV is configured or the CV can't be read as text — the pipeline then runs
    exactly as before, just without CV-fit notes."""
    if not conf.cv_path:
        return None
    try:
        cv_text = load_cv_text(conf.cv_path)
    except CVError as e:
        print(f"! CV-fit scoring off: {e}", file=sys.stderr)
        return None
    print(f"CV loaded ({len(cv_text)} chars) — adding a CV-fit score to each survivor.")
    return CVScorer(conf.model, conf.ollama_base, cv_text)


def _publish(base: str, survivors: list[dict], rejects: list[dict]) -> int:
    """Publish survivors + rejects. Returns the number of NEW rows the dashboard
    actually added (after its dedup), so the caller can pick new-vs-none."""
    added = 0
    if survivors:
        try:
            r = dash.ingest(base, survivors)
            added = int(r.get("added", 0) or 0)
            print(f"Published {added} (skipped {r.get('skipped', 0)} dupes).")
        except (dash.DashboardError, ValueError, TypeError) as e:
            print(f"! Could not publish survivors ({e}). They were judged but not added.",
                  file=sys.stderr)
    if rejects:
        try:
            r = dash.reject(base, rejects)
            print(f"Recorded {r.get('added', 0)} reject(s) to the ledger.")
        except dash.DashboardError as e:
            print(f"! Could not record rejects ({e}).", file=sys.stderr)
    return added


if __name__ == "__main__":
    raise SystemExit(main())
