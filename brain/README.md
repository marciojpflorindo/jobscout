# brain/

One `source → exclude → judge → publish` pipeline (Python). Scrapes job boards,
fetches each candidate's full posting text, has the local Ollama model judge it
against your `profile.md`, and publishes the survivors to the dashboard as
"Potential" rows — `no` verdicts go to the reject ledger so nothing is judged
twice.

## Run it

```bash
python3 brain/run.py            # full run: find, judge, publish
python3 brain/run.py --top 25   # cap how many candidates get fetched + judged
python3 brain/run.py --dry-run  # judge but don't publish (inspect the verdicts)
```

Needs the venv deps (`requirements.txt`), a running Ollama with your model
pulled, and the dashboard server running (for exclusion + publishing). Run
onboarding first so `config.json` + `profile.md` exist.

## Pipeline

1. **`config.py`** — loads + validates `config.json` (model, search params, port)
   and `profile.md` (the judging brief). Fails with a clear message, never a
   traceback, if either is missing.
2. **`dashboard.py`** — asks `/api/links` which links are already considered and
   collects past reject reasons (the feedback signal). Best-effort: a down
   dashboard warns and the run proceeds.
3. **`sources.py`** — JobSpy (Indeed + LinkedIn) parameterized by the profile's
   country/city/queries/remote-pref, plus RemoteOK. Each source is wrapped so one
   bad source logs a warning and is skipped, never crashing the run. Then dedup.
4. **Exclusion** — drop anything already on the dashboard *or* already judged
   locally (`state.py`) **before** any LLM call, so no GPU is wasted re-judging.
5. **`heuristic.py`** — a cheap keyword pre-filter (derived from your search
   terms, nothing personal) ranks candidates and keeps the top N. This only
   bounds volume; the LLM is the real gate.
6. **`fetch.py`** — SSRF-safe full-text fetch (http/https only, public-IP check
   on every redirect hop, hard timeout, byte cap, HTML-only, text extraction),
   with a disk cache. The single choke point for every hostile URL.
7. **`judge.py`** — the local model judges each posting against `profile.md`, a
   strict JSON output contract, + an injected "USER-REJECTED PATTERNS" block.
   Output is validated field-by-field and **fails closed** — malformed output is
   skipped, never published as a fabricated verdict.
8. **`state.py`** — records every judged URL (atomic write) so re-runs are
   idempotent.
9. **Publish** — survivors (`match`/`maybe`) → `/api/ingest` as "Potential";
   `no`s → `/api/reject`.

## Sources

Shipped by default: **JobSpy (Indeed + LinkedIn)** and **RemoteOK**. User-added
RSS feeds and extra JobSpy locations are read from `config.json`
(`extra_rss`, `extra_jobspy_locations`) — wired up in Phase 5. Raw HTML job
boards are out of scope (each needs bespoke scraping).
