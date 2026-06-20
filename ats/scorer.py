"""Local-Ollama CV-fit scorer — how well the user's CV evidences a posting.

This is the optional third lens (after the profile judge). Given the user's CV
text and one posting, a local model estimates how well the CV's *actual stated
evidence* matches the posting's requirements, and returns a 0-100 score plus a
one-line gap summary. The score is appended to the published job's dashboard
note; it never gates publishing.

Same hostile-output discipline as the brain's Judge: talk to local Ollama over
/api/chat in JSON mode, validate every field, and fail closed (return None) on
anything malformed so a bad model reply can never produce a fabricated score.
The CV stays in this process — only the score/gap line is emitted.

Manual / test use (run from the repo root):
    python3 -m ats.scorer --cv cv.md --text-file posting.txt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from ats.cv import CVError, load_cv_text

NUM_CTX = 8192               # CV + capped posting + instructions + headroom
MAX_POSTING_CHARS = 6_000    # posting chars sent alongside the CV
REQUEST_TIMEOUT = 300        # per-job ceiling so one slow score can't hang a run

SYSTEM_PROMPT = (
    "You are an ATS-style résumé-fit checker. You are given a candidate's RÉSUMÉ "
    "and a JOB POSTING. Estimate how well the résumé's ACTUAL, STATED evidence "
    "matches the posting's requirements. Score ONLY on evidence present in the "
    "résumé text — never credit a skill or experience the résumé does not show, "
    "and do not invent or assume anything. A higher score means the résumé "
    "already evidences what the posting asks for.\n\n"
    "Return ONLY a JSON object, no prose, no markdown fences:\n"
    '{"cv_match_score": <integer 0-100>, "gaps": "<one short line naming the '
    'biggest requirements the résumé does NOT evidence, or \'none\'>"}'
)


class CVScorer:
    """Holds the resolved model + base URL + CV text for a run."""

    def __init__(self, model: str, ollama_base: str, cv_text: str):
        self.model = model
        self.base = ollama_base.rstrip("/")
        self.cv_text = cv_text

    def _call_ollama(self, user: str) -> str:
        body = {
            "model": self.model,
            "format": "json",
            "stream": False,
            "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            f"{self.base}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            payload = json.load(resp)
        return payload.get("message", {}).get("content", "") if isinstance(payload, dict) else ""

    def score(self, job: dict) -> dict | None:
        """Score one job's posting against the CV. Returns {"score", "gaps"} or
        None on hard failure (after one retry) so the caller just omits the note."""
        posting = (job.get("_posting_text") or job.get("description") or "")[:MAX_POSTING_CHARS]
        if not posting.strip():
            return None
        user = (
            f"RÉSUMÉ:\n{self.cv_text}\n\n"
            f"JOB POSTING (title: {job.get('title', '')}):\n{posting}"
        )
        for attempt in (1, 2):
            try:
                raw = self._call_ollama(user)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                if attempt == 2:
                    print(f"    ! CV scorer Ollama call failed twice ({e})", flush=True)
                    return None
                time.sleep(2)
                continue
            result = parse_cv_score(raw)
            if result is not None:
                return result
            if attempt == 2:
                print(f"    ! Unparseable CV-score output, skipping: {raw[:120]!r}", flush=True)
                return None
        return None


def parse_cv_score(raw: str) -> dict | None:
    """Validate the model's JSON field-by-field. Fail closed: bad JSON, wrong
    shape, or a missing/garbage score returns None."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    value = obj.get("cv_match_score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    score = max(0, min(100, score))
    gaps = str(obj.get("gaps", "")).strip()[:200]
    if gaps.lower() == "none":
        gaps = ""
    return {"score": score, "gaps": gaps}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ats.scorer",
                                 description="Score a CV against one posting (manual/test).")
    ap.add_argument("--cv", required=True, help="path to the CV file (.md/.txt/.docx/.pdf)")
    ap.add_argument("--text-file", required=True, help="path to the posting text")
    ap.add_argument("--model", default="gemma4:26b-a4b-it-qat", help="Ollama model tag")
    ap.add_argument("--ollama-base", default="http://127.0.0.1:11434", help="Ollama base URL")
    args = ap.parse_args(argv)

    try:
        cv_text = load_cv_text(args.cv)
    except CVError as e:
        print(f"CV error: {e}", file=sys.stderr)
        return 1
    try:
        posting = open(args.text_file, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print(f"posting error: {e}", file=sys.stderr)
        return 1

    scorer = CVScorer(args.model, args.ollama_base, cv_text)
    result = scorer.score({"_posting_text": posting, "title": ""})
    if result is None:
        print("No score (model unavailable or unparseable output).", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
