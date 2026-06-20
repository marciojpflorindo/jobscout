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
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"
PROFILE_PATH = REPO_ROOT / "profile.md"
STATE_DIR = Path(__file__).resolve().parent / "state"

DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
DEFAULT_DASHBOARD_PORT = 8765
VALID_REMOTE_PREFS = ("remote-only", "hybrid-ok", "on-site")


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
class Config:
    model: str
    ollama_base: str
    search: Search
    cv_path: str | None
    dashboard_port: int
    extra_rss: list[str]
    extra_jobspy_locations: list[str]
    profile_text: str

    @property
    def dashboard_base(self) -> str:
        return f"http://127.0.0.1:{self.dashboard_port}"


def _str_list(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, (str, int, float)) and str(x).strip()]


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
    )
