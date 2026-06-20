"""JobScout dashboard data store — schema, validation, and the JSON file.

This module is the single source of truth and the trust boundary for everything
the browser (or the brain) sends: the closed Status allowlist, DD-MM-YYYY dates,
a spreadsheet formula-injection guard, and the reject ledger. Ported in spirit
from the legacy Netlify `store.js`, de-clouded: one JSON file on disk guarded by
a process-wide lock instead of Netlify Blobs, and no auth (localhost, one user).

The file holds a single object: {"rows": [...], "rejected": {<link>: {...}}}.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone

# --- Schema (English, hardcoded — no i18n) ----------------------------------
COLUMNS = [
    "Month", "Company", "Date", "Role", "Job link",
    "Contact via", "Status", "Response date", "Notes",
]

# "Potential" = a job-finder candidate not yet applied to (inserted via /api/ingest).
# Pre-application: deliberately NOT in RESPONDED_STATUSES, and excluded from the
# KPIs/charts in the front end. Applying = change Status to "Applied".
STATUSES = [
    "Potential",
    "Applied", "In conversation", "Interviewing", "Offer",
    "Accepted", "Rejected", "Declined", "No response",
]

# Statuses that mean the company actually replied. Reaching one of these for the
# first time stamps the Response date (if still empty). Mirrors RESPONDED in app.js.
RESPONDED_STATUSES = [
    "In conversation", "Interviewing", "Offer", "Accepted", "Rejected", "Declined",
]

# Status a row falls back to when an unknown value is supplied (fail closed).
DEFAULT_STATUS = "Applied"

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_MONTH_NUM = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
# Common three-letter abbreviations, for tolerant CSV import.
_MONTH_NUM.update({m.lower()[:3]: i + 1 for i, m in enumerate(MONTHS)})

DEFAULT_YEAR = datetime.now().year
MAX_LEN = 2000
MAX_NOTES = 20000
MAX_BODY = 256 * 1024          # 256 KB cap per JSON request
MAX_IMPORT = 4 * 1024 * 1024   # 4 MB cap for a CSV upload


class ValidationError(Exception):
    """Raised by a mutator to abort a write with a 400 (no write performed)."""


# --- Sanitizing / parsing ---------------------------------------------------
def sanitize(value, max_len=MAX_LEN):
    """Trim, cap length, and neutralize spreadsheet formula-injection.

    The store can be exported to CSV and reopened in Sheets/Excel, where a
    leading = + - @ (tab/CR) is a formula vector — prefix those with a quote.
    """
    s = ("" if value is None else str(value)).strip()[:max_len]
    if s and s[0] in "=+-@\t\r":
        s = "'" + s
    return s


def _fmt(d, mo, y):
    if 1 <= d <= 31 and 1 <= mo <= 12:
        return f"{d:02d}-{mo:02d}-{y:04d}"
    return ""


def normalize_date(raw):
    """Return DD-MM-YYYY. Unparseable input is returned unchanged (never guessed)."""
    s = ("" if raw is None else str(raw)).strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return _fmt(int(m[3]), int(m[2]), int(m[1]))
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", s)
    if m:
        y = int(m[3])
        if y < 100:
            y += 2000
        return _fmt(int(m[1]), int(m[2]), y)
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        return _fmt(int(m[1]), int(m[2]), DEFAULT_YEAR)
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s*(\d{4})?$", s)
    if m:
        key = m[2].lower()
        if key in _MONTH_NUM:
            return _fmt(int(m[1]), _MONTH_NUM[key], int(m[3]) if m[3] else DEFAULT_YEAR)
    return s  # unknown shape: keep as-is so nothing is silently corrupted


def today_date():
    """Today as DD-MM-YYYY (local time). Used to auto-stamp the Response date."""
    d = datetime.now()
    return _fmt(d.day, d.month, d.year)


def now_stamp():
    """ISO-8601 instant — the internal `_updated` last-touched stamp on every write.

    Not a COLUMNS field: stays out of CSV export/import and the front-end search.
    """
    return datetime.now(timezone.utc).isoformat()


def month_from_date(ddmmyyyy):
    m = re.match(r"^\d{1,2}-(\d{1,2})-\d{4}$", ddmmyyyy or "")
    if m:
        n = int(m[1])
        if 1 <= n <= 12:
            return MONTHS[n - 1]
    return ""


def clean_row(inp):
    """Build one clean row from arbitrary input (used by add + import + ingest).

    Status is allowlisted and fails closed to DEFAULT_STATUS.
    """
    date = normalize_date(inp.get("Date"))
    status = inp.get("Status")
    return {
        "Month": sanitize(inp.get("Month"), 20) or month_from_date(date),
        "Company": sanitize(inp.get("Company")),
        "Date": date,
        "Role": sanitize(inp.get("Role")),
        "Job link": sanitize(inp.get("Job link"), 500),
        "Contact via": sanitize(inp.get("Contact via")),
        "Status": status if status in STATUSES else DEFAULT_STATUS,
        "Response date": normalize_date(inp.get("Response date")),
        "Notes": sanitize(inp.get("Notes"), MAX_NOTES),
    }


# --- CSV (RFC-4180-ish parser; handles quotes, escaped quotes, newlines) ----
def parse_csv(text):
    if len(text) > MAX_IMPORT:
        raise ValidationError("CSV too large")
    if text and text[0] == "﻿":
        text = text[1:]  # strip BOM
    grid = []
    field, row, in_quotes = "", [], False
    i = 0
    while i < len(text):
        c = text[i]
        if in_quotes:
            if c == '"':
                if i + 1 < len(text) and text[i + 1] == '"':
                    field += '"'
                    i += 1
                else:
                    in_quotes = False
            else:
                field += c
        elif c == '"':
            in_quotes = True
        elif c == ",":
            row.append(field)
            field = ""
        elif c == "\n":
            row.append(field)
            grid.append(row)
            field, row = "", []
        elif c != "\r":
            field += c
        i += 1
    if field != "" or row:
        row.append(field)
        grid.append(row)

    if not grid:
        return []
    header = [h.strip() for h in grid[0]]
    out = []
    for cells in grid[1:]:
        if not any(c.strip() for c in cells):
            continue  # skip blank lines
        raw = {header[j]: (cells[j] if j < len(cells) else "") for j in range(len(header))}
        out.append(clean_row(raw))  # re-validate every imported cell, never trust the file
    return out


def to_csv(rows, cols=COLUMNS):
    """Serialize rows back to CSV (used by the download/export path)."""
    def cell(v):
        s = "" if v is None else str(v)
        if re.search(r'[",\n\r]', s):
            return '"' + s.replace('"', '""') + '"'
        return s

    lines = [",".join(cell(c) for c in cols)]
    for r in rows:
        lines.append(",".join(cell(r.get(c)) for c in cols))
    return "\r\n".join(lines) + "\r\n"


# --- The JSON store (one file, process-wide lock) ---------------------------
REJECT_CAP = 5000  # bound the append-only reject ledger; evict oldest past this


class JobStore:
    """One JSON file: {"rows": [...], "rejected": {<link>: {...}}}.

    A single process-wide lock serialises every read-modify-write, so the
    brain-ingests-while-user-edits case is safe. Writes are atomic (temp file +
    os.replace) so a crash mid-write can never truncate the live store.
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return {"rows": [], "rejected": {}}
        if not isinstance(data, dict):
            return {"rows": [], "rejected": {}}
        rows = data.get("rows")
        rej = data.get("rejected")
        return {
            "rows": rows if isinstance(rows, list) else [],
            "rejected": rej if isinstance(rej, dict) else {},
        }

    def _write(self, data):
        d = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=0)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get_rows(self):
        with self._lock:
            return self._read()["rows"]

    def get_rejected(self):
        with self._lock:
            return self._read()["rejected"]

    def mutate_rows(self, mutator):
        """Read-modify-write the rows list. `mutator(rows)` mutates in place and
        may return a value (passed back) or raise ValidationError to abort."""
        with self._lock:
            data = self._read()
            result = mutator(data["rows"])  # ValidationError propagates, no write
            self._write(data)
            return result

    def replace_rows(self, rows):
        with self._lock:
            data = self._read()
            data["rows"] = rows
            self._write(data)

    def mutate_rejected(self, mutator):
        with self._lock:
            data = self._read()
            result = mutator(data["rejected"])
            self._write(data)
            return result
