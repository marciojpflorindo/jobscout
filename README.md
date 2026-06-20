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
   CV-fit score.

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

<!-- Phase 4: re-run onboarding to attach a CV and turn on ATS scoring -->
_To be written._

## Advanced: extra job sources

<!-- Phase 5: config block for extra RSS feeds + JobSpy locations; raw HTML boards out of scope -->
_To be written._

## License

MIT — see [LICENSE](LICENSE). Author: Márcio Florindo.
