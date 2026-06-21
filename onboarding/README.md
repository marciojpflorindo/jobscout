# onboarding/

First-run interview + model selection (Python, stdlib only). Collects who you
are and what you're looking for, writes a templated `profile.md` and a
machine-readable `config.json`, detects your Mac's RAM, recommends an Ollama
model that fits, and offers to download it.

## Run it

```bash
python3 onboarding/interview.py          # first run, or redo the interview
python3 onboarding/interview.py --setup   # explicit re-run (confirms first)
```

`search-jobs.command` runs this automatically on first launch (wired in Phase 6).

## What it produces (both gitignored, at the repo root)

- **`profile.md`** — the human-readable judging brief the brain reads. Mirrors
  the legacy `job-finder-brief.md` structure (who-this-is-for, ranked target
  paths, Tier-A hard blockers, Tier-B judge-by-duties, 0–100 rubric). Built
  deterministically from your answers — no LLM call. Hand-edit it any time; the
  next brain run uses the edited file.
- **`config.json`** — machine settings the brain consumes: chosen model tag,
  JobSpy search params (queries / country / city / remote preference /
  seniority), optional CV path, dashboard port, and the advanced-source slots
  (`extra_rss`, `extra_jobspy_locations`, filled in Phase 5).

## Model tiers (pinned stock Ollama tags, verified 2026-06-20)

| Detected RAM | Recommended model | Tag |
|---|---|---|
| > 16 GiB | Gemma MoE (26B sparse) | `gemma4:26b-a4b-it-qat` |
| ≤ 16 GiB (and fallback) | Qwen 7B-class (9B MLX) | `qwen3.5:9b-mlx` |

You can override with your own tag (shown as untested). Intel Macs and
< 16 GiB get a loud warning but are not hard-blocked.

## Files

- `hardware.py` — Apple-Silicon + total-RAM detection (degrades to "unknown").
- `models.py` — pinned model tiers + RAM → recommendation (pure).
- `profile_template.py` — answers → `profile.md` + `config.json` (pure).
- `interview.py` — the interactive orchestrator + `main()`/`--setup`.

`models.py` and `profile_template.py` are pure (no I/O), so the Phase 7 test
suite can exercise the mapping and the templating without a Mac or a TTY.
