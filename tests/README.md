# Tests

Stdlib `unittest` only — no test dependencies beyond the runtime ones the brain
already needs (`requests`, `beautifulsoup4`, plus the lazily-imported
`feedparser`/`pypdf`). Run them from the repo root, inside the same `.venv`
`1-install.command` builds:

```bash
.venv/bin/python -m unittest discover -s tests
```

What's covered (the trust-boundary / fail-closed behavior, not the happy path):

| File | Module(s) | Focus |
|---|---|---|
| `test_store.py` | `dashboard/store.py` | status allowlist, CSV formula-injection guard, date normalization, CSV re-validation, JSON-store corrupt-file recovery + abort-on-error |
| `test_judge.py` | `brain/judge.py` | hostile model output fails closed; score clamp; `disqualified` forces `no`; rejection-pattern prompt block; injection-flag surfaces (never changes verdict/score); posting fenced + can't escape the fence |
| `test_notify.py` | `brain/config.py`, `brain/notify.py` | `ntfy` config parses + fails closed (bad topic/non-http server); self-hosted LAN server allowed; only three fixed generic templates; disabled/None = silent no-op |
| `test_fetch.py` | `brain/fetch.py` | SSRF host guard (loopback/private/metadata/unresolvable); scheme guard; no network touched |
| `test_sources.py` | `brain/sources.py`, `brain/config.py` | dedup, field caps, graceful per-source skip (RSS + JobSpy), RSS parses pre-fetched bytes |
| `test_state.py` | `brain/state.py` | stable job ids, corrupt-ledger recovery, save/load round-trip; pending-outbox round-trip + corrupt/non-dict recovery + idempotent clear |
| `test_run.py` | `brain/run.py` | durable publish — outbox merge/dedup, cleared on success, kept + merged on failure, recovered on retry, nothing-to-send → no-op; chunked sends (≤ server cap, no deadlock past 500); outbox persisted before `save_scored`; dry-run records nothing |
| `test_heuristic.py` | `brain/heuristic.py` | keyword scoring + threshold ranking |
| `test_assist.py` | `onboarding/assist.py` | generic cleanup, search-term guidance, local-only Ollama guard, hostile suggestion parsing |
| `test_onboarding.py` | `onboarding/models.py`, `onboarding/profile_template.py` | RAM→model recommendation; deterministic profile/config rendering; optional `ntfy` block included/omitted |
| `test_ats.py` | `ats/cv.py`, `ats/scorer.py` | CV extraction + quality gate (md/txt/docx, oversize, binary, bad zip); score parser fail-closed |

`pathsetup.py` puts the component directories on `sys.path` so the tests import
the modules the way the app does.
