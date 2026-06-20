"""A cheap keyword pre-filter — NOT a fit judgement.

The LLM judge is the real gate; this only bounds how many postings we fetch +
judge, by ranking on how well a posting's title/description echo the user's own
search terms. It is deliberately generic (derived from `config.search.queries`,
nothing personal) and lenient: a low threshold lets genuine matches with sparse
keyword overlap still reach the judge.
"""

from __future__ import annotations

import re

TITLE_HIT = 12
DESC_HIT = 3
# Words too generic to carry signal as a standalone keyword.
STOPWORDS = {
    "and", "or", "the", "a", "an", "of", "for", "to", "in", "with", "remote",
    "senior", "junior", "lead", "staff", "manager", "engineer", "specialist",
}
# Keep at least this score to be judged. Low on purpose (see module docstring).
PREFILTER_THRESHOLD = 3


def _keywords(queries: list[str]) -> list[str]:
    """Distinct lowercase keywords from the search queries: each whole phrase
    plus its non-stopword tokens."""
    kws: list[str] = []
    seen: set[str] = set()
    for q in queries:
        phrase = q.lower().strip()
        for term in [phrase] + re.findall(r"[a-z0-9+#]+", phrase):
            if len(term) < 2 or term in STOPWORDS or term in seen:
                continue
            seen.add(term)
            kws.append(term)
    return kws


def score(title: str, description: str, keywords: list[str]) -> int:
    t = (title or "").lower()
    d = (description or "").lower()
    s = 0
    for kw in keywords:
        if kw in t:
            s += TITLE_HIT
        if kw in d:
            s += DESC_HIT
    return s


def rank(jobs: list[dict], queries: list[str], top_n: int) -> list[dict]:
    """Annotate each job with a heuristic `_score`, drop those below the
    threshold, and return the strongest `top_n`, best first."""
    keywords = _keywords(queries)
    for j in jobs:
        j["_score"] = score(j.get("title", ""), j.get("description", ""), keywords)
    kept = [j for j in jobs if j["_score"] >= PREFILTER_THRESHOLD]
    kept.sort(key=lambda j: j["_score"], reverse=True)
    return kept[:top_n]
