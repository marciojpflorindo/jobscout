"""JobScout dashboard — a small local HTTP server (stdlib only).

Binds to 127.0.0.1, serves the static front end, and exposes the /api/* the
front end and the brain use. No auth: this is localhost, single user. Ported and
de-clouded from the legacy Netlify functions — the cloud blob store, the
session/edge auth, the shared ingest token, and the GitHub backup are all dropped.

Run directly:  python3 dashboard/server.py  [--port N] [--store PATH]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store as S  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEFAULT_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store.json")

MAX_DELETE = 1000
MAX_INGEST = 500
MAX_REJECT = 500

# Module-level store handle, set in main().
STORE: S.JobStore | None = None


# --- helpers ----------------------------------------------------------------
def _clip(v, n):
    return v.strip()[:n] if isinstance(v, str) else ""


class Handler(BaseHTTPRequestHandler):
    server_version = "JobScout/2.0"

    # Quieter logging: one line per request, no client noise to stderr spam.
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # --- response helpers ---
    def _send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_body(self, max_bytes):
        length = int(self.headers.get("Content-Length") or 0)
        if length > max_bytes:
            raise S.ValidationError("request body too large")
        data = self.rfile.read(length) if length else b""
        if len(data) > max_bytes:
            raise S.ValidationError("request body too large")
        return data

    def _read_json(self):
        raw = self._read_body(S.MAX_BODY)
        if not raw:
            raise S.ValidationError("invalid request body")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise S.ValidationError("invalid JSON")

    # --- routing ---
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/data":
                return self._api_data()
            if path == "/api/links":
                return self._api_links()
            if path.startswith("/api/"):
                return self._send_json(404, {"error": "not found"})
            return self._serve_static(path)
        except S.ValidationError as e:
            return self._send_json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — last-resort guard, logged
            sys.stderr.write(f"{path} error: {e!r}\n")
            return self._send_json(500, {"error": "internal error"})

    def do_HEAD(self):
        # Allow HEAD for static assets; APIs respond 405.
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/"):
            return self._send_json(405, {"error": "method not allowed"})
        return self._serve_static(path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        routes = {
            "/api/add": self._api_add,
            "/api/update": self._api_update,
            "/api/delete": self._api_delete,
            "/api/import": self._api_import,
            "/api/ingest": self._api_ingest,
            "/api/reject": self._api_reject,
        }
        fn = routes.get(path)
        if not fn:
            return self._send_json(404, {"error": "not found"})
        try:
            return fn()
        except S.ValidationError as e:
            return self._send_json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — last-resort guard, logged
            sys.stderr.write(f"{path} error: {e!r}\n")
            return self._send_json(500, {"error": "internal error"})

    # --- static files (path-traversal safe) ---
    def _serve_static(self, url_path):
        rel = url_path.lstrip("/")
        if rel == "":
            rel = "index.html"
        # Resolve and confirm the target stays inside STATIC_DIR.
        target = os.path.realpath(os.path.join(STATIC_DIR, rel))
        root = os.path.realpath(STATIC_DIR)
        if target != root and not target.startswith(root + os.sep):
            return self._send_json(403, {"error": "forbidden"})
        if not os.path.isfile(target):
            return self._send_json(404, {"error": "not found"})
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        try:
            with open(target, "rb") as f:
                data = f.read()
        except OSError:
            return self._send_json(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # --- API: data --------------------------------------------------------
    def _api_data(self):
        rows = STORE.get_rows()
        return self._send_json(200, {
            "statuses": S.STATUSES,
            "months": S.MONTHS,
            "columns": S.COLUMNS,
            "rows": [{**r, "id": i} for i, r in enumerate(rows)],
        })

    # --- API: add ---------------------------------------------------------
    def _api_add(self):
        body = self._read_json()
        if not isinstance(body, dict):
            raise S.ValidationError("invalid data")
        row = S.clean_row(body)
        if not row["Company"]:
            raise S.ValidationError('The "Company" field is required.')
        row["_updated"] = S.now_stamp()

        new_id = {"i": None}

        def mut(rows):
            rows.append(row)
            new_id["i"] = len(rows) - 1

        STORE.mutate_rows(mut)
        return self._send_json(200, {"ok": True, "id": new_id["i"]})

    # --- API: update (one field of one row) -------------------------------
    def _api_update(self):
        body = self._read_json()
        if not isinstance(body, dict):
            raise S.ValidationError("invalid data")
        try:
            idx = int(body.get("id"))
        except (TypeError, ValueError):
            raise S.ValidationError("invalid id")
        field = body.get("field")
        if field not in S.COLUMNS:
            raise S.ValidationError("field not editable")

        def mut(rows):
            if idx < 0 or idx >= len(rows):
                raise S.ValidationError("row does not exist")
            # Optional stale-guard: refuse if the row moved underneath the client.
            expect = body.get("expect")
            if isinstance(expect, str) and (rows[idx].get("Company") or "") != expect:
                raise S.ValidationError("The list changed. Reload and try again.")
            value = body.get("value") or ""
            if field == "Status":
                if value not in S.STATUSES:
                    raise S.ValidationError("status not allowed")
            elif field in ("Date", "Response date"):
                value = S.normalize_date(value)
            else:
                value = S.sanitize(value, S.MAX_NOTES if field == "Notes" else S.MAX_LEN)
            rows[idx][field] = value
            if field == "Date" and not str(rows[idx].get("Month") or "").strip():
                rows[idx]["Month"] = S.month_from_date(value)
            # Auto-stamp the Response date the first time Status moves to a
            # "responded" state — only when empty, so it marks the FIRST response.
            if (field == "Status" and value in S.RESPONDED_STATUSES
                    and not str(rows[idx].get("Response date") or "").strip()):
                rows[idx]["Response date"] = S.today_date()
            rows[idx]["_updated"] = S.now_stamp()

        STORE.mutate_rows(mut)
        return self._send_json(200, {"ok": True})

    # --- API: delete ------------------------------------------------------
    def _api_delete(self):
        body = self._read_json()
        if not isinstance(body, dict):
            raise S.ValidationError("invalid data")
        if isinstance(body.get("items"), list):
            entries = body["items"]
        else:
            raw = body.get("ids") if isinstance(body.get("ids"), list) else (
                [body["id"]] if body.get("id") is not None else [])
            entries = [{"id": i} for i in raw]
        # Strict: ids must be non-negative ints. Reject the whole batch on any bad
        # element rather than coercing (int(None/"")→error / 0 would nuke row 0).
        for e in entries:
            if not isinstance(e, dict) or not isinstance(e.get("id"), int) or isinstance(e.get("id"), bool) or e["id"] < 0:
                raise S.ValidationError("invalid ids")
        if not entries:
            raise S.ValidationError("no valid id")
        if len(entries) > MAX_DELETE:
            raise S.ValidationError("too many ids")

        remove = {e["id"] for e in entries}
        removed = {"n": 0}

        def mut(rows):
            for e in entries:
                if isinstance(e.get("company"), str):
                    row = rows[e["id"]] if 0 <= e["id"] < len(rows) else None
                    if not row or (row.get("Company") or "") != e["company"]:
                        raise S.ValidationError("The list changed. Reload and try again.")
            kept = [r for i, r in enumerate(rows) if i not in remove]
            removed["n"] = len(rows) - len(kept)
            rows[:] = kept

        STORE.mutate_rows(mut)
        return self._send_json(200, {"ok": True, "removed": removed["n"]})

    # --- API: import (replace whole store from raw CSV) -------------------
    def _api_import(self):
        raw = self._read_body(S.MAX_IMPORT)
        text = raw.decode("utf-8", errors="replace")
        if not text.strip():
            raise S.ValidationError("empty CSV")
        rows = S.parse_csv(text)
        # Guard a DESTRUCTIVE replace: a CSV whose header lacks a recognizable
        # "Company" column would parse to all-blank rows and wipe the store.
        if not any((r.get("Company") or "").strip() for r in rows):
            raise S.ValidationError("CSV has no recognizable 'Company' column (nothing changed).")
        STORE.replace_rows(rows)
        return self._send_json(200, {"ok": True, "count": len(rows)})

    # --- API: ingest (brain survivors -> Potential rows) ------------------
    def _api_ingest(self):
        body = self._read_json()
        items = body if isinstance(body, list) else (
            body.get("jobs") if isinstance(body, dict) and isinstance(body.get("jobs"), list) else None)
        if items is None:
            raise S.ValidationError("expected an array of jobs")
        if len(items) > MAX_INGEST:
            raise S.ValidationError(f"too many jobs (max {MAX_INGEST})")

        today = S.today_date()
        result = {}

        def mut(rows):
            # Re-derive existing links each call so a re-run never piles up dupes.
            seen = {(r.get("Job link") or "").strip() for r in rows}
            seen.discard("")
            added = skipped = 0
            for item in items:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                row = S.clean_row({
                    **item,
                    "Status": "Potential",                       # force; never trust sent Status
                    "Date": item.get("Date") or today,
                    "Contact via": item.get("Contact via") or "Job finder",
                })
                if not row["Company"] and not row["Role"]:
                    skipped += 1
                    continue
                link = (row.get("Job link") or "").strip()
                if link and link in seen:
                    skipped += 1
                    continue
                if link:
                    seen.add(link)
                row["_updated"] = S.now_stamp()
                rows.append(row)
                added += 1
            result.update(added=added, skipped=skipped, total=len(rows))

        STORE.mutate_rows(mut)
        return self._send_json(200, {"ok": True, **result})

    # --- API: links (the "already considered" exclusion set) --------------
    def _api_links(self):
        rows = STORE.get_rows()
        ledger = STORE.get_rejected()
        exclude = {}
        for r in rows:
            link = (r.get("Job link") or "").strip()
            if link and link not in exclude:
                exclude[link] = {
                    "link": link,
                    "status": _clip(r.get("Status"), 40),
                    "why": _clip(r.get("Notes"), 500),
                }
        for link, entry in ledger.items():
            key = (link or "").strip()
            if key and key not in exclude:
                exclude[key] = {
                    "link": key,
                    "status": "no",
                    "why": _clip(entry.get("reason") if isinstance(entry, dict) else "", 500),
                }
        return self._send_json(200, {"exclude": list(exclude.values())})

    # --- API: reject (append model `no` verdicts to the ledger) -----------
    def _api_reject(self):
        body = self._read_json()
        items = body if isinstance(body, list) else (
            body.get("rejected") if isinstance(body, dict) and isinstance(body.get("rejected"), list) else None)
        if items is None:
            raise S.ValidationError("expected an array of rejections")
        if len(items) > MAX_REJECT:
            raise S.ValidationError(f"too many rejections (max {MAX_REJECT})")

        today = S.today_date()
        result = {}

        def mut(ledger):
            added = skipped = 0
            for item in items:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                link = _clip(item.get("link"), 500)
                if not link or link in ledger:
                    skipped += 1
                    continue
                ledger[link] = {
                    "reason": _clip(item.get("reason"), 500),
                    "source": _clip(item.get("source"), 40) or "model",
                    "date": today,
                }
                added += 1
            # Bound growth: dict preserves insertion order, so the first keys are
            # the oldest — evict them (FIFO) until back under the cap.
            keys = list(ledger.keys())
            if len(keys) > S.REJECT_CAP:
                for k in keys[:len(keys) - S.REJECT_CAP]:
                    del ledger[k]
            result.update(added=added, skipped=skipped, total=len(ledger))

        STORE.mutate_rejected(mut)
        return self._send_json(200, {"ok": True, **result})


def main(argv=None):
    global STORE
    ap = argparse.ArgumentParser(description="JobScout dashboard server (localhost only).")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--store", default=DEFAULT_STORE, help="path to the JSON store")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    args = ap.parse_args(argv)

    STORE = S.JobStore(args.store)
    # 127.0.0.1 only — never bind a routable interface (no auth on these endpoints).
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"JobScout dashboard on {url}  (store: {args.store})")
    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
