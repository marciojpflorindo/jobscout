"""Optional ntfy run notifications — fixed generic templates, fail-safe POST.

An ntfy topic is PUBLIC: anyone who knows the topic name sees every message sent
to it, with no authentication. The only protection is an unguessable topic. So a
notification body is NEVER allowed to carry anything personal — no job title,
company, URL, profile field, count, or raw error string. The three fixed
templates below are the ONLY bodies ever sent, and the new-vs-none distinction
is a binary, not a count.

The POST can never fail the run: every path returns cleanly (a notification is a
courtesy, not a result). Only http/https with a hard timeout is allowed. Enabled
entirely by config — `brain/run.py` calls `notify_run()` with the parsed
`config.ntfy`; when that is None (block absent or disabled in config.json) it is
a silent no-op.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import urlparse

NOTIFY_TIMEOUT = 10  # seconds; the POST must never hang the end of a run

# The ONLY bodies ever sent on a run finishing. Generic by design.
TEMPLATES = {
    "new": "✅ JobScout finished — new potential jobs to review. Open your dashboard.",
    "none": "✅ JobScout finished — no new matches this run.",
    "failure": "⚠️ JobScout run failed — check the terminal/log.",
}


def template(kind: str) -> str | None:
    """The fixed body for a result kind ('new' | 'none' | 'failure'), or None for
    an unknown kind (the caller then sends nothing)."""
    return TEMPLATES.get(kind)


def notify_run(ntfy, kind: str) -> bool:
    """POST one fixed template to {server}/{topic}. Returns True only when a ping
    was actually sent, False otherwise (disabled, unknown kind, or any failure).
    NEVER raises — the run's success must not depend on a reachable phone.

    `ntfy` is the parsed `config.Ntfy` (or None). None / disabled => silent no-op.
    """
    if ntfy is None or not getattr(ntfy, "enabled", False):
        return False
    body = template(kind)
    if body is None:
        return False
    return _post(getattr(ntfy, "server", ""), getattr(ntfy, "topic", ""), body)


def _post(server: str, topic: str, body: str) -> bool:
    """http/https-only POST of a fixed body to {server}/{topic}, hard timeout,
    all failures swallowed. Returns True only on a completed request."""
    server = (server or "").rstrip("/")
    topic = (topic or "").strip()
    if not server or not topic:
        return False
    url = f"{server}/{topic}"
    if urlparse(url).scheme not in ("http", "https"):
        return False
    try:
        req = urllib.request.Request(
            url, data=body.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"}, method="POST")
        with urllib.request.urlopen(req, timeout=NOTIFY_TIMEOUT):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False
