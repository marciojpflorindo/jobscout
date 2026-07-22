# Changelog

All notable changes to JobScout are documented here.

## v1.6 - 2026-07-22

### Added

- Six new job boards, all on by default: Arbeitnow, Remotive, Jobicy, Himalayas,
  Working Nomads, and We Work Remotely. All are global/EU remote boards reached
  through their public APIs or feeds — no accounts or keys.
- A source registry (`brain/sources/`) with per-board on/off toggles via an
  optional `sources` block in `config.json`. Unknown keys or non-boolean values
  stop the run with a clear message instead of being silently ignored.
- Per-run source health: every run prints how many usable rows each board
  actually produced, and a board returning nothing (or rows without company
  names) is flagged as DEGRADED — a dead feed no longer looks like a quiet day.
  New `--check-sources` flag scrapes and reports without judging or publishing.
- Known positions are now blocked even when re-posted under a fresh URL: the
  brain fuzzily matches each candidate's (company, title) against every
  dashboard row, tolerant of legal suffixes ("Google" vs "Google LLC"),
  reworded titles, and abbreviations — while keeping different seniority levels
  visible as separate openings.
- Survivor notes now end with a "posted Nd ago" freshness suffix when the
  board's date is parseable, so the Review queue can be triaged freshest-first.
- A judge warm-up probe right after the scrape: a stopped Ollama now surfaces
  (terminal warning + optional phone ping) before the judging pass instead of
  failing per-job. Two new fixed notification templates (progress midpoint,
  warm-up failure).

### Changed

- The SSRF guard was rewritten to pin connections to validated IPs: the
  hostname is resolved once under a deadline, every address must be globally
  routable (including IPv4 embedded in IPv6), and the HTTP connection goes to
  that exact address while HTTPS still verifies the original hostname — closing
  the DNS-rebinding window between "check the host" and "fetch the URL". One
  deadline now covers DNS, redirects, and the body read.
- Dashboard-link exclusion now normalizes URLs on both sides (tracking
  parameters, fragments, prose around a pasted link), so hand-annotated rows
  still match the scraper's clean URLs.

### Fixed

- Position blocking no longer treats arbitrary title prefixes or extra role
  words as equivalent, preventing jobs such as "Data Engineering Manager" from
  being hidden by a prior "Data Engineer" row. Seniority aliases such as
  "Senior"/"Sr." still identify genuine re-posts.
- A judging pass where every model call fails now exits as a failure and sends
  the fixed failure notification instead of reporting "no new matches".
- Missing non-finite source fields no longer become the literal text `nan`, and
  company-less jobs with the same generic title no longer collapse during dedup.
- User-written rejection reasons preserved in the reject ledger continue to
  teach the judge after their dashboard row is deleted; model verdicts still do
  not feed back into the judge.
- SSRF address validation now handles NAT64 and the Teredo client address and
  explicitly blocks special-use ranges whose `ipaddress.is_global` behavior has
  varied across supported Python patch releases.
- The judge now reads the board's own description first, with the fetched page
  appended as a supplement. Previously any non-empty page fetch replaced the
  description outright, so a JavaScript-rendered job page could reduce a real
  ad to 80 characters of page chrome — and the judge scored the chrome.
- The judge no longer learns from its own verdicts. Its past reject reasons
  (and the machine-written score notes that rejected rows still carry) were
  being fed back into the prompt as "the user rejected jobs like this",
  compounding every run. Only user-written rejection notes teach it now.
- A retired RSS feed that answers 200 with an HTML page is now detected and
  rejected instead of silently parsing to zero jobs; a feed that parses but
  carries zero entries now warns instead of contributing silence.

## v1.5 - 2026-06-25

### Added

- Optional local-LLM setup help during onboarding. When the selected Ollama model
  is running and downloaded, JobScout can suggest clearer target roles and
  focused job-board search terms from the user's own answers.
- Generic no-LLM cleanup for onboarding answers: spacing, casing, duplicate
  removal, and guidance when search terms look too broad or too long.
- Progress feedback while the local model is generating onboarding suggestions,
  so the terminal no longer sits at a silent cursor.
- Search-plan logging before scraping, including how many search terms, boards,
  and extra markets will be queried.
- A top-level dashboard view switch: Tracker, Applications, and Review.
- Visible project version marker via `VERSION` and the README.
- Clearer run output and README guidance explaining the default 30-candidate cap
  and why untriaged Review jobs appear again on later runs.

### Changed

- Onboarding copy is more explicit about the difference between target roles,
  scraper search terms, and the judging profile.
- The application/company list now lives under its own Applications button,
  making it easier to reach without scrolling past charts.
- The dashboard view buttons now use the clearer segmented-control style from
  the personal applications dashboard.

### Fixed

- `3-open-dashboard.command` now launches the actual numbered search command.
- Setup command names in scripts and docs now consistently use
  `1-install.command` and `2-search-jobs.command`.
- The dashboard server version now matches the public `v1.5` release marker.
- Review candidates now have an explicit Reject flow that captures feedback,
  records it for future exclusions, and keeps the rejected job in dashboard stats.
- Local-model onboarding suggestions are stripped of terminal control characters
  before being printed or accepted.
