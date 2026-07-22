"""Load + validate the brain's inputs: config.json and profile.md (stdlib only).

config.json (written by onboarding) is the machine-readable settings; profile.md
is the human-readable judging brief. Both live at the repo root and are
gitignored. This module is the single place that reads them, so every other
brain module gets a validated `Config` and never touches the raw files.

Trust: both files are local and user-written (trusted), but we still validate
shape and fail with a clear message — a half-written config should not crash the
pipeline deep inside a scrape.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"
PROFILE_PATH = REPO_ROOT / "profile.md"
STATE_DIR = Path(__file__).resolve().parent / "state"

DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
DEFAULT_DASHBOARD_PORT = 8765
VALID_REMOTE_PREFS = ("remote-only", "hybrid-ok", "on-site")

DEFAULT_NTFY_SERVER = "https://ntfy.sh"
# An ntfy topic is a URL path segment; ntfy itself restricts it to this charset.
# Validating here means a malformed topic fails closed (notifications disabled)
# rather than producing a surprising POST target.
VALID_NTFY_TOPIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ConfigError(RuntimeError):
    """Raised when config.json / profile.md are missing or malformed."""


@dataclass
class Search:
    queries: list[str] = field(default_factory=list)
    country: str = ""
    city: str = ""
    remote_preference: str = "remote-only"
    seniority: str = ""


@dataclass
class Ntfy:
    """Resolved run-notification settings. Only ever constructed when the config
    block is present, enabled, and valid — so the brain can treat `Config.ntfy is
    not None` as 'notifications are on and safe to POST'."""
    server: str
    topic: str
    enabled: bool = True


@dataclass
class Config:
    model: str
    ollama_base: str
    search: Search
    cv_path: str | None
    dashboard_port: int
    extra_rss: list[str]
    extra_jobspy_locations: list[str]
    profile_text: str
    ntfy: Ntfy | None = None
    # Per-source overrides from the optional `sources` block. Sources default ON:
    # only keys the user explicitly set appear here.
    sources: dict[str, bool] = field(default_factory=dict)

    @property
    def dashboard_base(self) -> str:
        return f"http://127.0.0.1:{self.dashboard_port}"

    def source_on(self, name: str) -> bool:
        return self.sources.get(name, True)


def _str_list(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, (str, int, float)) and str(x).strip()]


def parse_ntfy(raw) -> Ntfy | None:
    """Validate the optional `ntfy` block. Fails closed: a missing block, an
    explicit `enabled: false`, a bad topic, or a non-http(s) server all return
    None (no notifications), so the brain never POSTs to a surprising target."""
    if not isinstance(raw, dict) or not bool(raw.get("enabled", False)):
        return None
    topic = str(raw.get("topic") or "").strip()
    if not VALID_NTFY_TOPIC.match(topic):
        return None
    server = str(raw.get("server") or "").strip() or DEFAULT_NTFY_SERVER
    parsed = urlparse(server)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return Ntfy(server=server.rstrip("/"), topic=topic, enabled=True)


def _source_toggles(raw) -> dict[str, bool]:
    """Validate the optional `sources` block: {"remoteok": false, ...}.

    Every built-in board defaults ON, so a config written before a board existed
    (or with no block at all) still searches everything — the block only carries
    the user's explicit overrides. An unknown key or a non-boolean value is a
    config mistake and fails loudly rather than being silently ignored.
    """
    if raw is None:
        return {}
    # Imported here, not at module top: sources' adapters import fetch, which
    # imports this module for STATE_DIR — a top-level import would be circular.
    from sources.registry import SOURCE_KEYS
    if not isinstance(raw, dict):
        raise ConfigError("config.json 'sources' must be an object of boolean toggles.")
    unknown = sorted(set(raw) - SOURCE_KEYS)
    if unknown:
        raise ConfigError(
            f"config.json 'sources' has unknown key(s): {', '.join(unknown)}. "
            f"Supported: {', '.join(sorted(SOURCE_KEYS))}.")
    toggles: dict[str, bool] = {}
    for key, value in raw.items():
        if not isinstance(value, bool):
            raise ConfigError(
                f"config.json 'sources.{key}' must be true or false (no quotes).")
        toggles[key] = value
    return toggles


def load() -> Config:
    """Read + validate config.json and profile.md, or raise ConfigError."""
    if not CONFIG_PATH.exists():
        raise ConfigError(
            f"No config.json at {CONFIG_PATH}. Run onboarding first: "
            "python3 onboarding/interview.py")
    if not PROFILE_PATH.exists():
        raise ConfigError(
            f"No profile.md at {PROFILE_PATH}. Run onboarding first: "
            "python3 onboarding/interview.py")

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ConfigError(f"Could not read config.json: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("config.json must be a JSON object.")

    model = str(raw.get("model") or "").strip()
    if not model:
        raise ConfigError("config.json is missing a 'model'. Re-run onboarding.")

    s = raw.get("search") if isinstance(raw.get("search"), dict) else {}
    search = Search(
        queries=_str_list(s.get("queries")),
        country=str(s.get("country") or "").strip(),
        city=str(s.get("city") or "").strip(),
        remote_preference=(str(s.get("remote_preference") or "remote-only").strip()
                           if str(s.get("remote_preference") or "").strip() in VALID_REMOTE_PREFS
                           else "remote-only"),
        seniority=str(s.get("seniority") or "").strip(),
    )
    if not search.queries:
        raise ConfigError("config.json has no search queries. Re-run onboarding.")

    profile_text = PROFILE_PATH.read_text(encoding="utf-8").strip()
    if not profile_text:
        raise ConfigError("profile.md is empty. Re-run onboarding or edit it by hand.")

    cv_path = raw.get("cv_path")
    cv_path = str(cv_path).strip() if isinstance(cv_path, str) and cv_path.strip() else None

    try:
        port = int(raw.get("dashboard_port") or DEFAULT_DASHBOARD_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_DASHBOARD_PORT

    return Config(
        model=model,
        ollama_base=str(raw.get("ollama_base") or DEFAULT_OLLAMA_BASE).strip() or DEFAULT_OLLAMA_BASE,
        search=search,
        cv_path=cv_path,
        dashboard_port=port,
        extra_rss=_str_list(raw.get("extra_rss")),
        extra_jobspy_locations=_str_list(raw.get("extra_jobspy_locations")),
        profile_text=profile_text,
        ntfy=parse_ntfy(raw.get("ntfy")),
        sources=_source_toggles(raw.get("sources")),
    )
