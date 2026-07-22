"""Thin HTTP client for the local dashboard's /api endpoints (stdlib only).

The dashboard is localhost, single-user, no auth — so this is plain JSON over
http://127.0.0.1:<port>. Three calls the brain needs:
  exclusion()       GET  /api/links   -> known links to skip + past reject reasons
  ingest(rows)      POST /api/ingest  -> publish survivors as "Potential" rows
  reject(items)     POST /api/reject  -> append model 'no' verdicts to the ledger
The server is the single source of truth for "already considered"; exclusion is
best-effort (a down dashboard warns and the brain proceeds rather than blocking).
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib import parse

TIMEOUT = 15
# Statuses whose stored note can carry USER feedback for the judge. Deliberately
# EXCLUDES the ledger's "no" status: those entries are the model's own past
# verdicts, and feeding them back as "the user rejected this because…" would make
# the judge learn from itself instead of from the user. Their links still count
# toward the exclusion set below — they just teach nothing.
REJECTED_STATUSES = {"Rejected", "Declined"}
# The brain's own published note shape ("maybe 55/100: …"), start-anchored. Any
# line matching this is machine-written, never user feedback.
BRAIN_VERDICT = re.compile(
    r"^\s*(match|maybe|no)\s+\d{1,3}/100(?:\s*:|\s*$)", re.IGNORECASE)
# Label the dashboard's Review reject flow prepends to the user's reason.
USER_REJECT_LABEL = "Rejected: "

_URL_RE = re.compile(r"https?://\S+")
# Tracking params to drop so the same posting matches across variants. utm_* by prefix.
_DROP_PARAM_EXACT = {"gh_src", "fbclid", "gclid", "ref", "trk", "mc_cid", "mc_eid", "igshid"}


@dataclass
class Exclusion:
    links: set[str]            # normalised links already on the board / rejected
    rejection_reasons: list[str]  # distinct 'why' notes from rejected entries
    # company -> titles for every dashboard row carrying both fields (any status:
    # applied, rejected, pending review). Boards re-post the same job under fresh
    # URLs, so URL exclusion alone re-surfaces positions the user already handled;
    # position_blocked matches these fuzzily. Keys/values are normalize_text'd.
    blocked_positions: dict[str, set[str]] = field(default_factory=dict)


class DashboardError(RuntimeError):
    pass


def normalize_url(raw: str) -> str:
    """Canonical key for membership testing — applied symmetrically to the
    scraper's URL and each exclude entry. NOT used for storage. Returns '' for a
    cell with no URL (so it matches no job). Drops tracking params, the fragment,
    a trailing slash, and any prose around the first http(s) token, so a
    hand-annotated dashboard row still matches the scraper's clean URL."""
    if not raw:
        return ""
    m = _URL_RE.search(raw)              # first http(s) token; ignores surrounding prose
    if not m:
        return ""
    url = m.group(0).rstrip(".,);]'\"")  # trim punctuation the greedy match may have grabbed
    try:
        parts = parse.urlsplit(url)
    except ValueError:
        return ""
    kept = [
        (k, v) for k, v in parse.parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm") and k.lower() not in _DROP_PARAM_EXACT
    ]
    kept.sort()
    return parse.urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/"),
        parse.urlencode(kept),
        "",                              # drop the fragment
    ))


def normalize_text(s: str) -> str:
    """Canonical key for company/title matching: lowercased, trimmed, punctuation
    dropped, inner whitespace collapsed. Comparison only — never stored, and run
    on BOTH the scraped job and the dashboard entry so the transform stays
    symmetric. Punctuation is stripped because boards render the same employer
    with different commas/dots ('Camunda, Inc.' vs 'Camunda Inc')."""
    # \w (letters/digits/underscore, Unicode-aware for accented names) survives;
    # every other char becomes a space, then whitespace collapses.
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


# --- Fuzzy position matching: boards re-post the same job under fresh URLs and
# reworded titles, and hand-entered rows abbreviate ("eng manager"), so exact
# comparison misses real duplicates. Both helpers take normalize_text'd strings
# (so punctuation is already gone).

# Legal-entity suffixes boards append inconsistently ("Google" / "Google LLC" /
# "Google, Inc."). Dropped from the tail before the company prefix test so the
# same employer matches whichever suffix a given source tacked on (common EU +
# US company forms).
_COMPANY_SUFFIXES = frozenset({
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "plc", "gmbh",
    "ag", "bv", "nv", "sa", "sas", "srl", "sarl", "ab", "as", "oy", "kg", "spa",
    "lda", "unipessoal", "group", "holdings",
})

# Seniority words. When BOTH titles name a level and the levels DIFFER, they're
# treated as different roles ("Senior" vs "Staff" Product Manager) so an
# adjacent-rank opening stays visible — a deliberate choice. A level on only one
# side is ignored: a bare "Product Manager" is level-agnostic and still matches
# its variants. "intern" intentionally remains a core token because an internship
# is a materially different opening from the unqualified role.
_LEVEL_WORDS = frozenset({
    "junior", "jr", "mid", "senior", "sr", "staff", "lead", "principal", "entry",
    "associate", "head", "chief", "director", "vp", "graduate", "grad",
})

# Explicit aliases only: arbitrary prefix matching made "product" match
# "production" and "engineer" match "engineering", silently hiding different
# roles. These are common title abbreviations with one unambiguous expansion.
_LEVEL_ALIASES = {"sr": "senior", "jr": "junior", "grad": "graduate"}
_TITLE_ALIASES = {"eng": "engineer", "tech": "technical"}

# Extra words allowed on only one version of a title. Keep this deliberately
# narrow: contract/intern are employment or seniority differences, not harmless
# decoration, so those openings must remain visible.
_TITLE_FILLERS = frozenset({"remote", "hybrid", "onsite", "m", "f", "d", "w", "x"})


def _company_tokens(name: str) -> list[str]:
    """normalize_text'd company -> tokens with trailing legal suffixes removed —
    but never the last real token, so a firm literally named "Group" survives."""
    toks = name.split()
    while len(toks) > 1 and toks[-1] in _COMPANY_SUFFIXES:
        toks.pop()
    return toks


def same_company(a: str, b: str) -> bool:
    """Same employer: after dropping legal suffixes, equal or one name's
    WHOLE-token list is a prefix of the other's — "camunda" matches "camunda
    services gmbh" and "google inc" matches "google llc", but "meta" does NOT
    match "metabase" (distinct tokens, so no substring false positives)."""
    if not a or not b:
        return False
    ta, tb = _company_tokens(a), _company_tokens(b)
    if not ta or not tb:
        return False
    short, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return longer[:len(short)] == short


def _token_match(x: str, y: str) -> bool:
    """Two title tokens name the same thing: equal, an explicit abbreviation,
    or a near-spelling (difflib >= 0.85, e.g. a plural). Prefix matching is
    intentionally forbidden because it changes word class too freely
    ("product"/"production", "engineer"/"engineering")."""
    x = _TITLE_ALIASES.get(x, x)
    y = _TITLE_ALIASES.get(y, y)
    if x == y:
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.85


def _split_level(tokens: set[str]) -> tuple[set[str], set[str]]:
    """(level tokens, core role tokens): levels drive the adjacent-rank rule,
    core tokens decide whether it's the same function."""
    levels = {_LEVEL_ALIASES.get(token, token)
              for token in tokens if token in _LEVEL_WORDS}
    return levels, tokens - _LEVEL_WORDS


def similar_title(a: str, b: str) -> bool:
    """Same role? Compares TOKEN SETS, not raw characters, so word order and
    filler don't matter and "writer" vs "recruiter" (high char overlap, different
    job) is NOT a false match. Two titles match when their CORE (non-level)
    tokens match one-to-one AND any surplus words are presentation-only fillers.
    They also must not name two DIFFERENT explicit levels — so "Senior Product
    Manager" vs "Staff Product Manager" and "Data Engineer" vs "Data Engineering
    Manager" stay visible, while "Data Engineer" vs "Data Engineer - Remote" is
    suppressed as a re-post."""
    if not a or not b:
        return False
    if a == b:
        return True
    la, ca = _split_level(set(a.split()))
    lb, cb = _split_level(set(b.split()))
    if not ca or not cb:
        return False
    # Different explicit seniority on BOTH sides -> adjacent-rank role, keep it visible.
    if la and lb and la != lb:
        return False
    # Same function? Match each smaller-core token to one distinct larger token.
    # Any unmatched larger tokens must be harmless presentation qualifiers.
    short, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    remaining = set(longer)
    for token in sorted(short):
        matches = [candidate for candidate in remaining
                   if _token_match(token, candidate)]
        if not matches:
            return False
        # Prefer the closest spelling when more than one token is eligible.
        match = max(matches, key=lambda candidate: difflib.SequenceMatcher(
            None, _TITLE_ALIASES.get(token, token),
            _TITLE_ALIASES.get(candidate, candidate)).ratio())
        remaining.remove(match)
    return remaining <= _TITLE_FILLERS


def position_blocked(blocked: dict[str, set[str]], company: str, title: str) -> bool:
    """Does a scraped job's RAW (company, title) hit the dashboard's blocked map?
    Company must same_company-match a key AND the title must similar_title-match
    one of that company's titles — both sides normalize_text'd here."""
    c, t = normalize_text(company), normalize_text(title)
    if not c or not t:
        return False
    for known, titles in blocked.items():
        if same_company(c, known) and any(similar_title(t, kt) for kt in titles):
            return True
    return False


def user_feedback(note: str) -> str:
    """The user-authored portion of a rejected row's note, or "" when the note is
    machine-written.

    Only the USER's words may feed the judge's rejected-patterns block. Two machine
    shapes hide in Notes: (a) a row rejected without a note still carries the
    brain's own published verdict ("maybe 55/100: …"), and (b) the Review reject
    flow prepends the user's reason but keeps that verdict below it. Echoing the
    model's own reasoning back as user feedback inverts the loop — the better a job
    once scored, the stronger the false anti-signal it becomes. So everything from
    the first machine-verdict line onward is dropped, the reject flow's
    "Rejected: " label is stripped, and whitespace is collapsed to one line."""
    kept: list[str] = []
    for line in str(note or "").splitlines():
        if BRAIN_VERDICT.match(line):
            break
        kept.append(line)
    text = " ".join("\n".join(kept).split())
    if text.startswith(USER_REJECT_LABEL):
        text = text[len(USER_REJECT_LABEL):].strip()
    return text


def _get(base: str, path: str) -> object:
    req = urllib.request.Request(f"{base}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise DashboardError(str(e)) from e


def _post(base: str, path: str, payload: object) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise DashboardError(str(e)) from e
    return body if isinstance(body, dict) else {}


def exclusion(base: str) -> Exclusion:
    """Fetch the 'already considered' set + rejection reasons + known positions.
    Best-effort: on any failure returns an empty Exclusion (caller proceeds
    without it)."""
    try:
        data = _get(base, "/api/links")
    except DashboardError:
        return Exclusion(links=set(), rejection_reasons=[])
    entries = data.get("exclude") if isinstance(data, dict) else None
    links: set[str] = set()
    reasons: list[str] = []
    seen_reason: set[str] = set()
    blocked: dict[str, set[str]] = {}
    if isinstance(entries, list):
        for e in entries:
            if not isinstance(e, dict):
                continue
            link = normalize_url(str(e.get("link") or ""))
            if link:
                links.add(link)
            status = str(e.get("status") or "").strip()
            source = str(e.get("source") or "").strip().lower()
            why = user_feedback(str(e.get("why") or ""))
            if ((status in REJECTED_STATUSES or source == "user")
                    and why and why.lower() not in seen_reason):
                seen_reason.add(why.lower())
                reasons.append(why)
            # Every row with both fields blocks its (company, title) position —
            # any status: boards re-post handled jobs under fresh URLs.
            company = normalize_text(str(e.get("company") or ""))
            title = normalize_text(str(e.get("title") or ""))
            if company and title:
                blocked.setdefault(company, set()).add(title)
    return Exclusion(links=links, rejection_reasons=reasons, blocked_positions=blocked)


def ingest(base: str, rows: list[dict]) -> dict:
    """Publish survivor rows. Server forces Status=Potential and dedups by link."""
    return _post(base, "/api/ingest", rows)


def reject(base: str, items: list[dict]) -> dict:
    """Append model 'no' verdicts ({link, reason, source}) to the reject ledger."""
    return _post(base, "/api/reject", items)
