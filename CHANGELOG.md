# Changelog

All notable changes to JobScout are documented here.

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
  records it for future exclusions, and removes the candidate from Review.
- Local-model onboarding suggestions are stripped of terminal control characters
  before being printed or accepted.
