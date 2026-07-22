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
| `test_notify.py` | `brain/config.py`, `brain/notify.py` | `ntfy` config parses + fails closed (bad topic/non-http server); self-hosted LAN server allowed; only five fixed generic templates; disabled/None = silent no-op |
| `test_fetch.py` | `brain/fetch.py` | pinned SSRF fetch: address policy (loopback/private/metadata/special-use/embedded-IPv4 including NAT64 + Teredo), DNS resolved once then pinned (no rebinding), redirect re-validation, decoded-byte caps, invalid URL forms; HTML-as-feed rejected |
| `test_sources.py` | `brain/sources/`, `brain/config.py` | registry toggles + failure isolation + result caps; per-run source HEALTH (dead/nameless boards flagged); normalize_job caps hostile rows; per-adapter mapping/envelope tests (RemoteOK, Arbeitnow, Remotive, Jobicy, Himalayas paging + placeholder-company fallback, Working Nomads ES envelope + hostile slug, WWR title split); RSS zero-entry warning + RFC-822→ISO dates + external-entity blocking; `sources` config block fails loudly on mistakes |
| `test_dashboard.py` | `brain/dashboard.py` | rejection feedback comes only from the USER (machine verdicts never echo back); URL normalization (tracking params, prose); fuzzy position blocking (legal suffixes, adjacent-rank titles, no substring false positives) |
| `test_server.py` | `dashboard/server.py` | dashboard API contracts consumed by the brain, including preservation of user-authored reject-ledger provenance |
| `test_state.py` | `brain/state.py` | stable job ids, corrupt-ledger recovery, save/load round-trip; pending-outbox round-trip + corrupt/non-dict recovery + idempotent clear |
| `test_run.py` | `brain/run.py` | durable publish — outbox merge/dedup, cleared on success, kept + merged on failure, recovered on retry, nothing-to-send → no-op; chunked sends (≤ server cap, no deadlock past 500); outbox persisted before `save_scored`; dry-run records nothing; posting text = description first + fetched appended; freshness suffix fail-soft |
| `test_heuristic.py` | `brain/heuristic.py` | keyword scoring + threshold ranking |
| `test_assist.py` | `onboarding/assist.py` | generic cleanup, search-term guidance, local-only Ollama guard, hostile suggestion parsing |
| `test_onboarding.py` | `onboarding/models.py`, `onboarding/profile_template.py` | RAM→model recommendation; deterministic profile/config rendering; optional `ntfy` block included/omitted |
| `test_ats.py` | `ats/cv.py`, `ats/scorer.py` | CV extraction + quality gate (md/txt/docx, oversize, binary, bad zip); score parser fail-closed |

`pathsetup.py` puts the component directories on `sys.path` so the tests import
the modules the way the app does.
