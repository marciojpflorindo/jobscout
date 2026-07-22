# brain/

One `source → exclude → judge → publish` pipeline (Python). Scrapes job boards,
fetches each candidate's full posting text, has the local Ollama model judge it
against your `profile.md`, and publishes the survivors to the dashboard as
"Potential" rows — `no` verdicts go to the reject ledger so nothing is judged
twice.

## Run it

```bash
python3 brain/run.py                 # full run: find, judge, publish
python3 brain/run.py --top 25        # cap how many candidates get fetched + judged
python3 brain/run.py --dry-run       # judge but don't publish (inspect the verdicts)
python3 brain/run.py --check-sources # scrape only: per-source health, no judging
```

Needs the venv deps (`requirements.txt`), a running Ollama with your model
pulled, and the dashboard server running (for exclusion + publishing). Run
onboarding first so `config.json` + `profile.md` exist.

## Pipeline

1. **`config.py`** — loads + validates `config.json` (model, search params, port,
   per-board `sources` toggles) and `profile.md` (the judging brief). Fails with
   a clear message, never a traceback, if either is missing.
2. **`dashboard.py`** — asks `/api/links` which links are already considered,
   which (company, title) positions the dashboard already knows, and the user's
   past reject reasons (the feedback signal — machine verdicts are filtered out
   so the judge never learns from itself). Best-effort: a down dashboard warns
   and the run proceeds.
3. **`sources/`** — the board registry and one adapter module per board (see
   below). Each source is wrapped so one bad board logs a warning and is
   skipped, never crashing the run; every row is validated + capped at this
   boundary (`base.normalize_job`). Per-run health is counted (`HEALTH`) and a
   source that quietly returns nothing is flagged as DEGRADED. Then dedup.
4. **Exclusion** — drop anything already on the dashboard (by normalized URL
   *or* by fuzzy company+title match, since boards re-post jobs under fresh
   URLs) *or* already judged locally (`state.py`) **before** any LLM call, so no
   GPU is wasted re-judging.
5. **`heuristic.py`** — a cheap keyword pre-filter (derived from your search
   terms, nothing personal) ranks candidates and keeps the top N. This only
   bounds volume; the LLM is the real gate.
6. **`fetch.py`** — SSRF-safe full-text fetch: every hostile URL is resolved
   once under a deadline, every address must be globally routable, and the
   connection is **pinned** to a validated IP (HTTPS still verifies the original
   hostname), so a second DNS answer can't re-route the request. Redirects
   re-validate; one deadline covers DNS + connect + redirects + body; hard byte
   caps; HTML-only text extraction with a disk cache. The single choke point for
   every hostile URL.
7. **`judge.py`** — the local model judges each posting against `profile.md`, a
   strict JSON output contract, + an injected "USER-REJECTED PATTERNS" block.
   Output is validated field-by-field and **fails closed** — malformed output is
   skipped, never published as a fabricated verdict. A `warmup()` probe runs
   after the scrape so a stopped Ollama surfaces before the judging pass, not
   per-job.
8. **`state.py`** — records every judged URL (atomic write) so re-runs are
   idempotent.
9. **Publish** — survivors (`match`/`maybe`) → `/api/ingest` as "Potential",
   each note ending with a "posted Nd ago" freshness suffix when the board's
   date parses; `no`s → `/api/reject`.

## Sources (`brain/sources/`)

`registry.py` is the only code-level list of boards: JobSpy (Indeed + LinkedIn),
RemoteOK, Arbeitnow, Remotive, Jobicy, Himalayas, Working Nomads, We Work
Remotely RSS, and the user's own RSS feeds (`extra_rss`). Every board defaults
ON; the optional `sources` block in `config.json` carries explicit off-switches
and rejects unknown keys loudly.

To add a board: one adapter module under `sources/` yielding the common job
shape (`{source, title, company, location, url, description, date}`), one
`SourceSpec` entry in `registry.py`, tests in `tests/test_sources.py`. All
fetching goes through `fetch.py`/`sources/http.py` — never call `requests` or a
parser's URL mode directly from an adapter. Raw HTML job boards are out of
scope (each needs bespoke scraping).
