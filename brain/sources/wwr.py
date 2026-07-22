"""We Work Remotely RSS adapter (major remote board, title-embedded company)."""

from __future__ import annotations

import sys
from collections.abc import Iterator

import fetch

from .rss import entry_date

WWR_FEED = "https://weworkremotely.com/remote-jobs.rss"
MAX_RSS_ENTRIES = 100


def _warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr, flush=True)


def _split_title(raw_title: str) -> tuple[str, str]:
    """WWR titles are "Company Name: Job Title" with no separate company field.

    Split on the first colon into (company, title); if there is no colon, treat
    the whole string as the title and leave the company blank.
    """
    company, sep, title = raw_title.partition(":")
    if sep and title.strip():
        return company.strip(), title.strip()
    return "", raw_title.strip()


def collect_wwr(_conf) -> Iterator[dict]:
    """Fetch WWR's RSS without letting feedparser reach out to the network."""
    try:
        import feedparser

        print("  RSS: We Work Remotely...", flush=True)
        raw = fetch.fetch_feed_bytes(WWR_FEED)
        if not raw:
            _warn("RSS feed unreachable, blocked, or not a feed: We Work Remotely")
            return
        feed = feedparser.parse(raw)
        if not feed.entries:
            # Same reason as rss.scrape_rss: a feed that parses to zero items is
            # a dead source that looks like a quiet one unless it says so.
            _warn("RSS feed parsed but has NO items: We Work Remotely — check the URL")
            return
        for entry in feed.entries[:MAX_RSS_ENTRIES]:
            company, title = _split_title(entry.get("title") or "")
            # region/country are custom (non-standard) RSS elements; fall back to
            # "Remote" when feedparser does not surface them.
            location = (entry.get("region") or entry.get("country") or "Remote")
            yield {
                "source": "RSS/We Work Remotely",
                "title": title,
                "company": company,
                "location": location,
                "url": entry.get("link"),
                "description": entry.get("summary") or entry.get("description") or "",
                "date": entry_date(entry),
            }
    except Exception as exc:  # noqa: BLE001 -- a bad feed is non-fatal
        _warn(f"RSS feed failed (We Work Remotely): {exc}")
        return
