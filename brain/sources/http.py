"""SSRF-guarded, size-capped response helpers for source adapters."""

from __future__ import annotations

import json

import fetch

MAX_API_BYTES = 3_000_000
API_TIMEOUT = 15


class SourceFetchError(RuntimeError):
    """Raised when a source response cannot be fetched or safely decoded."""


def fetch_json(url: str, timeout: float = API_TIMEOUT) -> object:
    """Fetch a board API's JSON through the shared SSRF guard."""
    response = fetch.fetch_url_safe(
        url, accept="application/json", timeout=timeout, max_bytes=MAX_API_BYTES)
    if response is None:
        raise SourceFetchError("request failed")
    try:
        return json.loads(response.content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceFetchError("invalid JSON response") from exc
