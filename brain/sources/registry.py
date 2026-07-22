"""Explicit allowlist of supported job boards.

This tuple is the ONLY code-level list of sources: config.json's `sources` block
is validated against these keys, and the central collector runs exactly what is
listed here. Adding a board = one adapter module + one SourceSpec entry (no
dynamic module discovery).
"""

from __future__ import annotations

from .arbeitnow import collect_arbeitnow
from .base import SourceSpec
from .himalayas import collect_himalayas
from .jobicy import collect_jobicy
from .jobspy import collect_jobspy
from .remoteok import collect_remoteok
from .remotive import collect_remotive
from .rss import collect_rss
from .working_nomads import collect_working_nomads
from .wwr import collect_wwr

SOURCES = (
    # Indeed and LinkedIn share one collector (the central loop runs it once)
    # but keep independent toggle keys.
    SourceSpec("indeed", "JobSpy (Indeed + LinkedIn)", collect_jobspy),
    SourceSpec("linkedin", "JobSpy (Indeed + LinkedIn)", collect_jobspy),
    SourceSpec("remoteok", "RemoteOK", collect_remoteok),
    SourceSpec("arbeitnow", "Arbeitnow", collect_arbeitnow),
    SourceSpec("remotive", "Remotive", collect_remotive),
    SourceSpec("jobicy", "Jobicy", collect_jobicy),
    SourceSpec("himalayas", "Himalayas", collect_himalayas),
    SourceSpec("working_nomads", "Working Nomads", collect_working_nomads),
    SourceSpec("wwr", "We Work Remotely RSS", collect_wwr),
    # User-configured feeds: only "set up" when the config lists any, so an
    # empty list is skipped rather than reported as a degraded source.
    SourceSpec("rss", "User RSS feeds", collect_rss,
               active=lambda conf: bool(conf.extra_rss)),
)

SOURCE_BY_KEY = {source.key: source for source in SOURCES}
if len(SOURCE_BY_KEY) != len(SOURCES):
    raise RuntimeError("Source registry contains duplicate keys.")

SOURCE_KEYS = frozenset(SOURCE_BY_KEY)
