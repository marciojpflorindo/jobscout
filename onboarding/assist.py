"""Optional onboarding help for profile wording and search terms.

This module deliberately avoids a built-in role taxonomy. The deterministic
path only cleans up the user's own wording (spacing, case, duplicates) and gives
generic search-term guidance. Domain expansion is only attempted through the
user's local Ollama model, when it is already running and the selected model is
downloaded.

Threat model:
  Inputs: interactive onboarding answers (semi-trusted local user input) and
    local-model output (hostile/untrusted).
  Network: only the configured local Ollama HTTP endpoint is contacted. Non-local
    bases are refused so onboarding help cannot accidentally send profile data to
    a remote service.
  Failure: every Ollama failure or malformed response returns a reason string;
    callers fall back to the manual/deterministic flow.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from profile_template import Answers, OLLAMA_BASE

REQUEST_TIMEOUT = 90
READINESS_TIMEOUT = 3
NUM_CTX = 4096

MAX_PROMPT_FIELD_CHARS = 800
MAX_ITEM_CHARS = 90
MAX_REASON_CHARS = 180
MAX_TARGET_PATHS = 6
MAX_SEARCH_TERMS = 6
MAX_PROFILE_NOTES = 6


@dataclass(frozen=True)
class Suggestion:
    text: str
    reason: str = ""


@dataclass(frozen=True)
class Assistance:
    target_paths: list[Suggestion]
    search_terms: list[Suggestion]
    profile_notes: list[Suggestion]


def normalize_phrase(raw: str, *, lowercase: bool = True) -> str:
    """Generic phrase cleanup: split compact/camel-case text, collapse spacing,
    and optionally lowercase. It does not add role-specific synonyms."""
    text = str(raw or "")[:MAX_ITEM_CHARS]
    text = re.sub(r"[_/|]+", " ", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text.lower() if lowercase else text


def clean_search_terms(items: list[str], *, max_items: int | None = MAX_SEARCH_TERMS) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        term = normalize_phrase(item, lowercase=True)
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        out.append(term)
        if max_items is not None and len(out) >= max_items:
            break
    return out


def clean_target_paths(items: list[str], *, max_items: int | None = MAX_TARGET_PATHS) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        path = normalize_phrase(item, lowercase=False)
        key = path.casefold()
        if not path or key in seen:
            continue
        seen.add(key)
        out.append(path)
        if max_items is not None and len(out) >= max_items:
            break
    return out


def seed_search_terms(target_paths: list[str]) -> list[str]:
    """Fallback defaults derived only from the user's own target roles."""
    return clean_search_terms(target_paths, max_items=MAX_SEARCH_TERMS)


def search_term_guidance(terms: list[str]) -> list[str]:
    """Generic, domain-independent warnings about scraper query quality."""
    cleaned = clean_search_terms(terms, max_items=None)
    notes: list[str] = []
    if not cleaned:
        return ["JobScout needs at least one search phrase so it has something to scrape."]
    if len(cleaned) > MAX_SEARCH_TERMS:
        notes.append(
            f"{len(cleaned)} search phrases is a lot. Each phrase is sent to multiple "
            "job boards, so runs get slower and noisier as the list grows."
        )
    broad = [t for t in cleaned if len(re.findall(r"[a-z0-9+#]+", t)) == 1]
    if broad:
        sample = ", ".join(broad[:3])
        notes.append(
            "Single-word searches can be very broad. Keep them only when the word is "
            f"a specific title, technology, or field: {sample}."
        )
    long_terms = [t for t in cleaned if len(t) > 60 or len(t.split()) > 7]
    if long_terms:
        notes.append(
            "Very long searches often perform poorly on job boards. Use short phrases "
            "for scraping and put nuanced preferences in the profile instead."
        )
    return notes


def ollama_model_ready(model: str, base: str = OLLAMA_BASE) -> tuple[bool, str]:
    """Return whether the local Ollama server is reachable and has `model`."""
    if not _is_local_http_base(base):
        return False, "the configured Ollama URL is not a local HTTP endpoint"
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/api/tags", timeout=READINESS_TIMEOUT) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
        return False, f"Ollama is not reachable yet ({e})"
    models = payload.get("models") if isinstance(payload, dict) else None
    names = {
        str(m.get("name") or "").strip()
        for m in models or []
        if isinstance(m, dict)
    }
    if model in names:
        return True, "ready"
    return False, f"the selected model is not downloaded yet ({model})"


def llm_suggest(answers: Answers, model: str, base: str = OLLAMA_BASE) -> tuple[Assistance | None, str]:
    """Ask the local model for suggestions. Never raises; malformed output fails
    closed and the caller keeps the normal manual flow."""
    if not _is_local_http_base(base):
        return None, "refusing to send onboarding answers to a non-local Ollama URL"

    body = {
        "model": model,
        "format": "json",
        "stream": False,
        "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(answers)},
        ],
    }
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
        return None, f"the local model call failed ({e})"
    if not isinstance(payload, dict):
        return None, "the local model returned an unexpected response shape"
    message = payload.get("message")
    raw = message.get("content", "") if isinstance(message, dict) else ""
    result = parse_llm_suggestions(raw)
    if result is None:
        return None, "the local model did not return usable JSON suggestions"
    return result, "ok"


def parse_llm_suggestions(raw: str) -> Assistance | None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    result = Assistance(
        target_paths=_parse_items(obj.get("target_paths"), kind="target"),
        search_terms=_parse_items(obj.get("search_terms"), kind="search"),
        profile_notes=_parse_items(obj.get("profile_notes"), kind="note"),
    )
    if not result.target_paths and not result.search_terms and not result.profile_notes:
        return None
    return result


def _parse_items(raw, *, kind: str) -> list[Suggestion]:
    if not isinstance(raw, list):
        return []
    limit = {
        "target": MAX_TARGET_PATHS,
        "search": MAX_SEARCH_TERMS,
        "note": MAX_PROFILE_NOTES,
    }[kind]
    out: list[Suggestion] = []
    seen: set[str] = set()
    for item in raw:
        text = ""
        reason = ""
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = str(
                item.get("text")
                or item.get("term")
                or item.get("path")
                or item.get("role")
                or item.get("note")
                or ""
            )
            reason = str(item.get("reason") or "").strip()[:MAX_REASON_CHARS]
        if kind == "search":
            text = normalize_phrase(text, lowercase=True)
        else:
            text = normalize_phrase(text, lowercase=False)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(Suggestion(text=text[:MAX_ITEM_CHARS], reason=reason))
        if len(out) >= limit:
            break
    return out


def _is_local_http_base(base: str) -> bool:
    parsed = urlparse(base)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _cap(text: str) -> str:
    return str(text or "").strip()[:MAX_PROMPT_FIELD_CHARS]


def _user_prompt(a: Answers) -> str:
    target_paths = "\n".join(f"- {_cap(p)}" for p in a.target_paths if str(p).strip())
    return (
        "Use only the candidate information below. Do not assume a fixed career taxonomy.\n\n"
        f"Professional self-description:\n{_cap(a.self_description)}\n\n"
        f"Target seniority:\n{_cap(a.seniority)}\n\n"
        f"Target roles or paths the user typed:\n{target_paths or '(none)'}\n\n"
        "Return suggestions that help this person configure JobScout."
    )


_SYSTEM_PROMPT = (
    "You help a user set up a local job-search scraper and judge. The scraper "
    "uses short job-board search phrases to collect postings; a later local LLM "
    "judge uses the full profile to decide fit. These are different jobs.\n\n"
    "Use only the user's own description and target paths. Suggest clearer wording "
    "and related search phrases only when they are directly supported by the user's "
    "input. Do not invent a broad career direction, do not list every synonym you "
    "know, and do not create a giant taxonomy.\n\n"
    "Return ONLY this JSON object, no markdown:\n"
    "{"
    '"target_paths":[{"text":"role/path wording","reason":"short reason"}],'
    '"search_terms":[{"text":"job-board search phrase","reason":"short reason"}],'
    '"profile_notes":[{"text":"preference or detail the user should mention later","reason":"short reason"}]'
    "}\n\n"
    "Rules: keep 3-6 search terms; each search term should usually be 2-5 words; "
    "avoid broad one-word searches unless the user's field is genuinely known by "
    "one specific word; keep reasons short and practical."
)
