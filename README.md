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
with Homebrew; `install.command` handles everything else.

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

## Setup (run once)

Double-click **`install.command`** in the JobScout folder (or run
`./install.command` in Terminal). It:

1. checks your prerequisites and stops with a clear fix if anything's missing,
2. builds a local Python sandbox (`.venv`) and installs the pinned dependencies,
3. walks you through a short **interview** — who you are, the roles you want,
   where you're searching, and your dealbreakers,
4. recommends a local model for your Mac's RAM and offers to download it,
5. optionally sets up **phone notifications** when a run finishes (off by
   default — see [Run notifications](#run-notifications)).

Your answers are written to two local files at the repo root — `profile.md` (your
hand-editable judging brief) and `config.json` (model + search settings). Both are
gitignored and never leave your machine. Nothing is uploaded anywhere.

> If you'd rather not download the model during setup, decline the offer —
> JobScout prints the exact `ollama pull …` command to run yourself later.

## Daily loop

Once you're set up, **make sure Ollama is running**, then double-click
**`search-jobs.command`** (or run `./search-jobs.command`). One launch does the whole loop:

1. it starts your local **dashboard**,
2. runs the **search** — scrapes the sources, fetches each posting's full text,
   judges every one against your profile, and publishes the good matches to the
   dashboard as **Potential** (this can take several minutes; your Mac is kept
   awake while it runs),
3. opens the dashboard in your browser to review, and keeps it running until you
   press **Ctrl-C**.

The dashboard has two tabs:

- **⭐ Review** — the Potential jobs the search just found, waiting for your call.
  The count badge shows how many are left to triage. Keep the good ones by moving
  them to **Applied** (or any later status); **reject with a note** when one isn't
  right — that note teaches the next search to down-rank similar jobs, so matches
  improve over time.
- **📊 Tracker** — every job you've applied to, with your stats and charts, tracked
  through its status lifecycle: Applied → In conversation → Interviewing → Offer → … .

After a fresh search you'll land on **Review** (that's where the new jobs are); once
you've triaged them, switch to **Tracker** to manage your pipeline.

> Don't want to watch the terminal? Turn on [run notifications](#run-notifications)
> and JobScout pings your phone when each run finishes.

### Just open the dashboard (no search)

To look at and update the jobs you already have — without scraping and without the
several-minute wait — double-click **`open-dashboard.command`** (or run
`./search-jobs.command --no-search`). It brings the dashboard straight up; it doesn't even
need Ollama running.

Search options are forwarded to the brain, for example `./search-jobs.command --dry-run`
(judge without publishing) or `./search-jobs.command --top 10` (cap how many postings
are judged).

> **Your results are never lost.** Each run writes its judged matches to a local
> outbox *before* sending them to the dashboard. If the dashboard is unreachable,
> the matches are kept and published automatically on the next run — so a hiccup
> never wastes a run. To push held results immediately without re-searching:
> `.venv/bin/python brain/run.py --publish-only`.

To change your profile or model later, re-run the interview:

```bash
./search-jobs.command --setup
```

## Adding a CV later

A CV is optional and can be added any time — without redoing the whole interview:

```bash
./search-jobs.command --add-cv
```

Give the path when asked. It's copied into the repo (gitignored) and its path is
saved to `config.json`. The next search appends a **CV-fit score** to every job it
publishes.

**Format matters.** Markdown, TXT, and DOCX read most reliably. A born-digital
PDF (exported from Word, Google Docs, a CV builder) works too. A *scanned* PDF
(a photo/scan of a printed CV) has no text layer — the scorer detects this,
skips that CV with a one-line notice, and the rest of the pipeline runs
normally. For the best scores, supply a Markdown or DOCX CV.

To turn CV-fit scoring back off, re-run onboarding and leave the CV blank (or
clear `cv_path` in `config.json`).

## Run notifications

A brain run can take a while, so JobScout can ping your phone when one finishes —
via [ntfy](https://ntfy.sh), a free push service. It's **off by default**;
onboarding asks if you want it.

If you enable it:

1. JobScout generates a long, random **topic** (e.g. `jobscout-x7Qa…`) and shows it.
2. Install the **ntfy** app (iOS/Android) — or open `ntfy.sh/<topic>` in a
   browser — and **subscribe to exactly that topic**.
3. Onboarding sends a test notification so you can confirm it works.

After each run you'll get one of three generic messages: *new jobs to review*,
*no new matches*, or *run failed* — that's it.

> **Topics are public.** Anyone who knows your topic name can read its messages,
> so JobScout never puts anything personal in them (no job title, company, count,
> URL, or error text) and uses a long random topic that's effectively impossible
> to guess. Keep your topic to yourself. Self-hosting an ntfy server? Set its URL
> when onboarding asks. To change or disable notifications, re-run onboarding with
> `./search-jobs.command --setup`.

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
repo root, after `install.command` has built `.venv`:

```bash
.venv/bin/python -m unittest discover -s tests
```

See [`tests/README.md`](tests/README.md) for what each file covers.

## License

MIT — see [LICENSE](LICENSE). Author: Márcio Florindo.
