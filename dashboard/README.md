# dashboard/

Local web app to review found jobs and track every application through its
status lifecycle. A small stdlib-only Python HTTP server bound to `127.0.0.1`
serves the static front end and the `/api/*` endpoints, storing everything in one
JSON file (`store.json`, gitignored). No auth — localhost, single user.

## Run

```bash
python3 dashboard/server.py            # opens http://127.0.0.1:8765/
python3 dashboard/server.py --port 9000 --store /path/to/store.json --no-open
```

(`start.command` launches this for you — see the repo README.)

## Layout

| File | Role |
|---|---|
| `server.py` | HTTP server + routing; the eight `/api/*` endpoints; path-traversal-safe static serving. |
| `store.py` | Single source of truth + trust boundary: schema (`COLUMNS`, `STATUSES`, `RESPONDED_STATUSES`, `MONTHS`), sanitize/normalize-date/clean-row, CSV parse/serialise, the JSON store (one process-wide lock, atomic writes), and the reject ledger. |
| `static/` | `index.html`, `app.js`, `style.css`, and pinned `vendor/` Chart.js. Renders via `textContent`; only `http(s)` links are linkified (XSS-safe). |

## Endpoints (no auth)

`GET /api/data` · `POST /api/add` · `POST /api/update` (one field + Response-date
auto-stamp) · `POST /api/delete` (Company-fingerprint guarded) · `POST /api/import`
(CSV replace) · `POST /api/ingest` (brain survivors → `Potential`, dedup by Job
link) · `GET /api/links` (the "already considered" exclusion set for the brain) ·
`POST /api/reject` (append model `no`s to a FIFO-capped ledger).

## Data model

One row: `Month, Company, Date, Role, Job link, Contact via, Status, Response
date, Notes`. Dates are `DD-MM-YYYY`. `Status` is a closed allowlist, fails closed
to `Applied`. `Potential` is pre-application (brain candidates): excluded from
KPIs/charts, and shown in the front end's own ⭐ Review tab (Tracker holds applied jobs).
`Response date` is auto-stamped the first time a row enters a responded status
(never overwritten). `_updated` is an internal last-touched timestamp (not a
column; drives the "Last activity" sort; kept out of CSV export).
