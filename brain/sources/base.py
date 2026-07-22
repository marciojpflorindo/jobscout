"""Shared source contract and hostile-row normalization."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, TypedDict
from urllib.parse import urlsplit

TITLE_CAP = 300
DESC_CAP = 2_000
URL_CAP = 4_096
SOURCE_CAP = 100
DATE_CAP = 100


class Job(TypedDict):
    source: str
    title: str
    company: str
    location: str
    url: str
    description: str
    date: str


Collector = Callable[[Any], Iterable[Mapping[str, object]]]


@dataclass(frozen=True)
class SourceSpec:
    """One supported board and the adapter that collects from it.

    `active` (optional) says whether the source applies to this config at all —
    e.g. the user-RSS source only runs when feeds are configured. Different from
    the on/off toggle: an inactive source is "not set up", not "broken", so it
    is skipped without being health-checked.
    """

    key: str
    label: str
    collect: Collector
    active: Callable[[Any], bool] | None = None


def _text(value: object, cap: int) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        try:
            return value.isoformat()[:cap]
        except (OverflowError, ValueError):
            return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return str(value).strip()[:cap]


def normalize_job(raw: object, default_source: str = "") -> Job | None:
    """Validate and cap one untrusted adapter row.

    A usable posting needs a title and an absolute HTTP(S) URL. Invalid rows are
    skipped at the source boundary rather than reaching fetch, judge, or publish.
    """
    try:
        if not isinstance(raw, Mapping):
            return None
        title = _text(raw.get("title"), TITLE_CAP)
        raw_url = _text(raw.get("url"), URL_CAP + 1)
        if not title or not raw_url or len(raw_url) > URL_CAP:
            return None
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        if (parsed.scheme not in ("http", "https") or not hostname
                or parsed.username is not None or parsed.password is not None):
            return None
        hostname.rstrip(".").encode("idna").decode("ascii")
        port = parsed.port
        if port is not None and not 1 <= port <= 65_535:
            return None
        return {
            "source": _text(raw.get("source"), SOURCE_CAP)
            or _text(default_source, SOURCE_CAP),
            "title": title,
            "company": _text(raw.get("company"), TITLE_CAP),
            "location": _text(raw.get("location"), TITLE_CAP) or "Unspecified",
            "url": raw_url,
            "description": _text(raw.get("description"), DESC_CAP),
            "date": _text(raw.get("date"), DATE_CAP),
        }
    except Exception:
        # Adapter mappings and every field they expose are hostile. A broken
        # getter, malformed hostname, or odd scalar fails this row only.
        return None
