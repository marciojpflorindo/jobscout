"""User-supplied RSS/Atom feeds as a source.

Trust: the URL is user-written (trusted enough to attempt) but the host and the
feed body are HOSTILE. The fetch goes through `fetch.fetch_feed_bytes` (the
SSRF + timeout + size-cap + content-type choke point), the parser is handed the
already-fetched bytes (never the URL — feedparser would otherwise fetch it
itself, unguarded), and every extracted field is capped downstream by
normalize_job. A broken feed never crashes a run.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator

import fetch

MAX_RSS_ENTRIES = 100            # cap entries taken from any one feed


def _warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr, flush=True)


def entry_date(entry) -> str:
    """A feed entry's date as ISO YYYY-MM-DD when feedparser could parse it.

    Feeds ship RFC-822 strings ("Fri, 18 Jul 2026 09:30:15 +0000"), which the
    freshness note's ISO-prefix parse rejects — so RSS rows would silently be the
    only sources without a "posted Nd ago" note. feedparser normalizes any date
    format it understands into the *_parsed struct_time; prefer that, and fall
    back to the raw string (no note, same as before) rather than guessing.
    """
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return time.strftime("%Y-%m-%d", parsed)
            except (TypeError, ValueError, OverflowError):
                pass
    return str(entry.get("published") or entry.get("updated") or "")


def scrape_rss(feed_url: str) -> Iterator[dict]:
    """One user-supplied feed. Yields nothing on any error (logged)."""
    try:
        import feedparser  # lazy: only the RSS path pulls this dependency
        print(f"  RSS: {feed_url}...", flush=True)
        raw = fetch.fetch_feed_bytes(feed_url)
        if not raw:
            _warn(f"RSS feed unreachable, blocked, or not a feed: {feed_url!r}")
            return
        feed = feedparser.parse(raw)
        # The quiet half of a dead feed: valid XML with an empty channel. Without
        # this line the source contributes nothing and the run still looks clean.
        if not feed.entries:
            _warn(f"RSS feed parsed but has no entries (moved or retired?): {feed_url!r}")
            return
        for entry in feed.entries[:MAX_RSS_ENTRIES]:
            summary = entry.get("summary") or entry.get("description") or ""
            yield {
                "source": "RSS",
                "title": entry.get("title"),
                "company": entry.get("author"),
                "location": "",
                "url": entry.get("link"),
                "description": summary,
                "date": entry_date(entry),
            }
    except Exception as e:  # noqa: BLE001 — a bad feed is non-fatal
        _warn(f"RSS feed failed ({feed_url!r}): {e!r}")


def collect_rss(conf) -> Iterator[dict]:
    for feed_url in conf.extra_rss:
        yield from scrape_rss(feed_url)
