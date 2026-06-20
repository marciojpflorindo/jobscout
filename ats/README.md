# ats/ — optional CV-fit scorer

If you supply a CV at onboarding, the brain appends a **CV-fit score** to each
job it publishes — a 0–100 estimate of how well your CV's *actual stated
evidence* matches that posting, plus a one-line note on the biggest gaps. It
runs on the same local Ollama model as the judge; nothing leaves your machine,
and the score never decides whether a job is published — it's just extra signal
on the dashboard.

No CV configured? The pipeline runs exactly as before, with no CV-fit notes.

## Supported CV formats (by reliability)

| Format | How it's read | Reliability |
|---|---|---|
| `.md` / `.txt` | read directly (stdlib) | best |
| `.docx` | stdlib `zipfile` → `word/document.xml` | best |
| `.pdf` (born-digital) | `pypdf` | good |
| `.pdf` (scanned / image-only) | — | **not scored** (see below) |

**Markdown, TXT, or DOCX give the most reliable scores.** A born-digital PDF
(exported from Word, Google Docs, LaTeX, a CV builder) reads well. A *scanned*
PDF — a photo or scan of a printed CV — has no text layer; rather than feed the
model garbage and invent a meaningless score, the loader's quality gate rejects
it and prints one line telling you to supply a text/MD/DOCX CV. Heavily designed
multi-column PDFs may read in jumbled order; convert to Markdown for the best
result.

## Files

- `cv.py` — turn a CV file into clean text. Caps file size, PDF pages, and DOCX
  decompressed size; a quality gate rejects empty/binary-looking extractions.
  Fails closed with a user-facing `CVError` on anything unusable.
- `scorer.py` — `CVScorer`: local-Ollama CV-vs-posting fit score, validated
  field-by-field and failing closed (returns `None`) on malformed model output,
  exactly like the brain's judge.

## Manual / test use

```bash
# from the repo root
python3 -m ats.scorer --cv cv.md --text-file some-posting.txt
# -> {"score": 72, "gaps": "no Kubernetes; limited people-management evidence"}
```

`pypdf` (in `requirements.txt`) is needed only for PDF CVs; everything else is
stdlib.
