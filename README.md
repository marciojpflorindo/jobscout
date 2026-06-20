# JobScout

> A local-first job-search tool for macOS. A local LLM finds and *judges* job
> postings against a profile you build in a first-run interview, and a local
> dashboard tracks every application through its lifecycle. Everything runs on
> your own Mac — no cloud, no accounts, no secrets.

> **Status: in development.** This README is a skeleton; sections fill in as the
> build progresses.

---

## What it does

JobScout has three local parts:

1. **The brain** — scrapes job boards (Indeed + LinkedIn via JobSpy, plus
   RemoteOK), fetches each posting's full text, and has a local Ollama model
   judge it against your profile (a verdict + a 0–100 score). Good matches are
   published to your dashboard.
2. **The dashboard** — a local web app to review found jobs and track every
   application through its status lifecycle (Potential → Applied → Interviewing →
   Offer → … ). Rejecting a job with a note teaches the brain to down-rank
   similar jobs next run.
3. **The ATS scorer** — optional. Supply a CV and each published job also gets a
   CV-fit score (how well your CV's evidence matches that posting). Markdown,
   TXT, or DOCX CVs score most reliably; born-digital PDFs work too.

## System requirements

- **Apple Silicon Mac** (M-series). Intel Macs are untested and likely far too
  slow.
- **16 GB RAM or more** is the tested floor. Less will run behind a warning, but
  scoring may be slow or low quality.
- macOS with Terminal access (you'll paste a few commands once).

## Install

<!-- Phase 6: Homebrew → brew install python@3.12 → brew install ollama, then start.command -->
_To be written._

## First run

<!-- Phase 6: double-click start.command → it sets up the sandbox, runs the interview, launches the dashboard -->
_To be written._

## Daily loop

<!-- Phase 6: run the brain → review Potential jobs → set statuses → reject with notes -->
_To be written._

## Adding a CV later

A CV is optional and can be added any time — just re-run onboarding:

```bash
python3 onboarding/interview.py --setup
```

When it asks for a CV, give the path. It's copied into the repo (gitignored) and
its path is saved to `config.json`. The next brain run appends a **CV-fit score**
to every job it publishes.

**Format matters.** Markdown, TXT, and DOCX read most reliably. A born-digital
PDF (exported from Word, Google Docs, a CV builder) works too. A *scanned* PDF
(a photo/scan of a printed CV) has no text layer — the scorer detects this,
skips that CV with a one-line notice, and the rest of the pipeline runs
normally. For the best scores, supply a Markdown or DOCX CV.

To turn CV-fit scoring back off, re-run onboarding and leave the CV blank (or
clear `cv_path` in `config.json`).

## Advanced: extra job sources

Out of the box, the brain searches Indeed + LinkedIn (via JobSpy, using your
profile's country/city/queries) and RemoteOK. You can broaden coverage by adding
your own sources to `config.json` (written by onboarding, gitignored). Two slots:

```json
{
  "extra_jobspy_locations": ["Berlin, Germany", "Amsterdam, Netherlands"],
  "extra_rss": ["https://example.com/jobs.rss", "https://another.org/feed.atom"]
}
```

- **`extra_jobspy_locations`** — each location is searched with all of your
  profile's queries on Indeed + LinkedIn, in addition to your main location.
- **`extra_rss`** — RSS or Atom job feeds. Each is fetched safely (HTTPS only,
  timeout, size cap, no internal addresses) and parsed for title/link/summary.

Both are **resilient**: if a location is unsupported or a feed 404s, times out,
or returns junk, the brain logs one warning and moves on — a bad source never
crashes a run.

**Out of scope: raw HTML job-board URLs.** Scraping an arbitrary careers page
needs bespoke, per-site parsing that breaks whenever the site changes. JobScout
only extends through structured sources (JobSpy locations + RSS/Atom feeds). If a
board offers an RSS feed, use that.

## License

MIT — see [LICENSE](LICENSE). Author: Márcio Florindo.
