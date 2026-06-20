"""Load a local CV file to plain text for CV-fit scoring (fail closed).

The CV path comes from config.json (the user supplied it at onboarding), so the
*source* is trusted — but the *file* is not: it may be huge, binary, a scanned
(image-only) PDF, or a malformed/zip-bomb DOCX. This module is the single place
that turns a CV file into clean text, and it caps everything:

  * a byte cap on the file before we read it (DoS / accidental huge file),
  * a page cap on PDFs and a decompressed-size cap on DOCX (zip bomb),
  * a quality gate on the result — text too short or mostly non-letters (the
    signature of a scanned PDF or a binary mess) is REJECTED, not fed to the
    model as garbage.

Supported formats by reliability:
  .md / .txt  — read directly (best)
  .docx       — extracted with the stdlib (zipfile -> word/document.xml) (best)
  .pdf        — extracted with pypdf; born-digital PDFs read well, scanned ones
                are caught by the quality gate (good / guarded)

On any unusable input `load_cv_text` raises `CVError` with a one-line, user-
facing reason; the caller turns CV-fit scoring off for the run and prints it.
It never returns garbage and never partially succeeds.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

MAX_CV_BYTES = 5_000_000      # refuse to read a CV file larger than this
MAX_DOCX_XML_BYTES = 20_000_000  # decompressed document.xml cap (zip-bomb guard)
MAX_PDF_PAGES = 30           # stop after this many pages (runaway PDF guard)
MAX_CV_CHARS = 6_000         # chars of CV text sent to the model
MIN_CV_CHARS = 200           # below this the extraction is treated as failed
MIN_TEXT_RATIO = 0.6         # letters+whitespace / total; below this = "binary mess"

# Formats we can turn into text here. Onboarding may store others, but scoring
# only runs on these.
SUPPORTED_EXTS = (".md", ".txt", ".docx", ".pdf")


class CVError(RuntimeError):
    """Raised when a CV file is missing, too big, unsupported, or unreadable as
    text. The message is safe to show the user."""


def load_cv_text(path_str: str) -> str:
    """Return clean CV text (capped to MAX_CV_CHARS), or raise CVError.

    Fail closed: any read/parse failure or a low-quality extraction raises,
    rather than returning partial or garbage text the model would score blindly.
    """
    path = Path(path_str).expanduser()
    if not path.is_file():
        raise CVError(f"no CV file at {path}")

    try:
        size = path.stat().st_size
    except OSError as e:
        raise CVError(f"could not stat CV file ({e})") from e
    if size > MAX_CV_BYTES:
        raise CVError(f"CV file is too large ({size:,} bytes; cap {MAX_CV_BYTES:,})")

    ext = path.suffix.lower()
    if ext in (".md", ".txt"):
        raw = _read_text_file(path)
    elif ext == ".docx":
        raw = _docx_text(path)
    elif ext == ".pdf":
        raw = _pdf_text(path)
    else:
        raise CVError(
            f"unsupported CV type '{ext or '(none)'}' — use {', '.join(SUPPORTED_EXTS)}")

    text = _clean(raw)
    if not _looks_like_text(text):
        raise CVError(
            f"couldn't read '{path.name}' as text (a scanned/image-only PDF or an "
            "unusual layout?) — supply a Markdown, TXT, or DOCX CV for CV-fit scoring")
    return text[:MAX_CV_CHARS]


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise CVError(f"could not read CV file ({e})") from e


def _docx_text(path: Path) -> str:
    """Extract paragraph text from a .docx with the stdlib only.

    A .docx is a zip; the body lives in word/document.xml. We read only that one
    member, refuse it if its declared decompressed size is implausibly large
    (zip-bomb guard), turn paragraph closes into newlines, and strip all tags.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                info = zf.getinfo("word/document.xml")
            except KeyError as e:
                raise CVError("DOCX has no word/document.xml — is it a real .docx?") from e
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise CVError("DOCX body is implausibly large; refusing to read it")
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except zipfile.BadZipFile as e:
        raise CVError("CV is not a valid .docx (bad zip)") from e
    except OSError as e:
        raise CVError(f"could not open CV file ({e})") from e

    # Paragraph and line-break tags -> newlines, then drop every remaining tag.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:br\s*/?>", "\n", xml)
    stripped = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(stripped)


def _pdf_text(path: Path) -> str:
    """Extract text from a PDF via pypdf (born-digital PDFs only; scanned ones
    yield little/no text and get caught by the quality gate downstream)."""
    try:
        from pypdf import PdfReader  # lazy: only the PDF path needs the dep
    except ImportError as e:
        raise CVError("PDF support needs pypdf (pip install -r requirements.txt)") from e
    try:
        reader = PdfReader(str(path))
        parts = [(page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]]
    except Exception as e:  # pypdf raises a variety of types on malformed PDFs
        raise CVError(f"could not parse the PDF ({type(e).__name__})") from e
    return "\n".join(parts)


def _clean(raw: str) -> str:
    """Collapse horizontal whitespace and runs of blank lines; keep line breaks
    (they help the model see structure). Strip leading/trailing space."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_text(text: str) -> bool:
    """Reject the 'binary mess' / empty-scan signature: too short, or a low ratio
    of letters+whitespace to total characters."""
    stripped = text.strip()
    if len(stripped) < MIN_CV_CHARS:
        return False
    textual = sum(1 for ch in stripped if ch.isalpha() or ch.isspace())
    return (textual / len(stripped)) >= MIN_TEXT_RATIO
