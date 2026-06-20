"""SSRF-safe URL fetching + posting-text extraction (requests + BeautifulSoup).

Every URL the brain fetches — a job posting page, or a user-supplied RSS feed
(Phase 5) — is HOSTILE input. This module is the single choke point that:
  * allows only http/https,
  * resolves the host and refuses any private / loopback / link-local / reserved
    / cloud-metadata address (SSRF guard), re-checked on every redirect hop,
  * enforces a hard timeout and a response-size byte cap (slow-loris / DoS),
  * rejects non-HTML responses,
  * parses HTML for text only — BeautifulSoup never executes anything.
It never raises to the caller: any failure returns None ("fall back to the
scraped description"). `fetch_url_safe` exposes the raw guarded GET for non-HTML
callers (e.g. RSS); `fetch_posting_text` adds caching + text extraction.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import STATE_DIR

PAGE_FETCH_TIMEOUT = 12          # seconds per request
MAX_PAGE_BYTES = 3_000_000       # stop reading a body past this (DoS guard)
MAX_PAGE_TEXT_CHARS = 8_000      # extracted-text cap stored per job
MAX_REDIRECTS = 3
PAGE_CACHE_DIR = STATE_DIR / "page_cache"
# A real UA — many job boards 403 the default requests/urllib agent.
FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def host_is_public(hostname: str) -> bool:
    """True only if every address `hostname` resolves to is a public IP.

    Fails closed: unresolvable hosts and any private/loopback/link-local/
    reserved/multicast address (e.g. 127.0.0.1, 169.254.169.254 metadata, 10.x)
    return False.
    """
    if not hostname:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])  # strip scope id
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def fetch_url_safe(url: str, accept: str = "text/html") -> requests.Response | None:
    """Guarded GET following up to MAX_REDIRECTS hops manually, re-checking the
    SSRF guard on each. Returns the final 200 Response (stream=True; caller must
    read with a byte cap and close it), or None on any failure/redirect-limit."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if not host_is_public(parsed.hostname):
            return None
        try:
            resp = requests.get(
                current,
                headers={"User-Agent": FETCH_UA, "Accept": accept},
                timeout=PAGE_FETCH_TIMEOUT,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException:
            return None
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                return None
            current = urljoin(current, location)
            continue
        if resp.status_code != 200:
            resp.close()
            return None
        return resp
    return None  # too many redirects


def _read_capped(resp: requests.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=16_384):
        chunks.append(chunk)
        total += len(chunk)
        if total >= MAX_PAGE_BYTES:
            break
    return b"".join(chunks)


def _extract_text(html_bytes: bytes) -> str:
    """Strip posting HTML to readable text. Markup is hostile — BeautifulSoup
    parses, never executes; we only read text out."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "svg"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return " ".join(text.split())[:MAX_PAGE_TEXT_CHARS]


def fetch_posting_text(url: str) -> str | None:
    """Fetch a live posting and return its readable text, or None on any failure."""
    resp = fetch_url_safe(url, accept="text/html")
    if resp is None:
        return None
    try:
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            return None
        return _extract_text(_read_capped(resp))
    except requests.RequestException:
        return None
    finally:
        resp.close()


def fetch_posting_text_cached(url: str) -> str | None:
    """fetch_posting_text with a disk cache (state/page_cache/<sha256>.txt) so
    re-runs and interrupted runs don't re-fetch. Only non-empty fetches cache."""
    if not url:
        return None
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache_file = PAGE_CACHE_DIR / f"{key}.txt"
    if cache_file.exists():
        try:
            return cache_file.read_text(encoding="utf-8")
        except OSError:
            pass
    text = fetch_posting_text(url)
    if text:
        try:
            PAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(text, encoding="utf-8")
        except OSError:
            pass
    return text
