# JobScout

> A local-first job-search tool for macOS. A local LLM finds and *judges* job
> postings against a profile you build in a first-run interview, and a local
> dashboard tracks every application through its lifecycle. Everything runs on
> your own Mac — no cloud, no accounts, no secrets.

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

JobScout needs three things on your Mac: a Python 3.12 interpreter, Ollama (the
local model runner), and the JobScout code itself. You install the first two once
with Homebrew; `start.command` handles everything else.

**1. Install Homebrew** (if you don't already have it). Open Terminal and follow
the one-line command at <https://brew.sh>.

**2. Install Python 3.12 and Ollama:**

```bash
brew install python@3.12
brew install ollama
```

**3. Start Ollama** — open the Ollama app (`open -a Ollama`) or run `ollama serve`
in a Terminal tab. Leave it running; it's the local model server.

**4. Get JobScout** — download or clone this repository to a folder you'll keep,
for example:

```bash
git clone <repo-url> ~/jobscout
```

That's the whole manual install. JobScout never auto-installs Homebrew, Python, or
Ollama for you, and it never pipes a script from the internet into your shell.

## First run

Double-click **`start.command`** in the JobScout folder (or run `./start.command`
in Terminal). On the first run it:

1. checks your prerequisites and stops with a clear fix if anything's missing,
2. builds a local Python sandbox (`.venv`) and installs the pinned dependencies,
3. walks you through a short **interview** — who you are, the roles you want,
   where you're searching, and your dealbreakers,
4. recommends a local model for your Mac's RAM and offers to download it, and
5. opens the **dashboard** in your browser.

Your answers are written to two local files at the repo root — `profile.md` (your
hand-editable judging brief) and `config.json` (model + search settings). Both are
gitignored and never leave your machine. Nothing is uploaded anywhere.

> If you'd rather not download the model during onboarding, decline the offer —
> JobScout prints the exact `ollama pull …` command to run yourself later.

## Daily loop

Once you're set up, a typical session is:

1. **Make sure Ollama is running**, then **find jobs** — run the brain:

   ```bash
   .venv/bin/python brain/run.py
   ```

   It scrapes the sources, fetches each posting's full text, judges every one
   against your profile, and publishes the good matches to your dashboard as
   **Potential**. (Use `--dry-run` to judge without publishing, or `--top N` to
   cap how many it judges.)

2. **Open the dashboard** — double-click `start.command` (later runs skip setup
   and go straight to the dashboard).

3. **Review the Potential jobs.** As you act on each, move it along its status:
   Potential → Applied → In conversation → Interviewing → Offer → … .

4. **Reject with a note.** When a match isn't right, reject it and say why — that
   note teaches the next brain run to down-rank similar jobs, so the matches get
   better over time.

To change your profile, model, or add a CV later, re-run onboarding:

```bash
./start.command --setup
```

## Adding a CV later

A CV is optional and can be added any time — just re-run onboarding:

```bash
./start.command --setup
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

## Running the tests

The trust-boundary behavior (input validation, SSRF guards, fail-closed model
parsing, graceful source skips) is covered by a stdlib `unittest` suite. From the
repo root, after the first run has built `.venv`:

```bash
.venv/bin/python -m unittest discover -s tests
```

See [`tests/README.md`](tests/README.md) for what each file covers.

## License

MIT — see [LICENSE](LICENSE). Author: Márcio Florindo.
