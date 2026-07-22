"""SSRF-safe URL fetching and posting-text extraction.

Every URL the brain fetches — a job posting page, or a user-supplied RSS feed —
is HOSTILE input. This module is the single choke point. Hostile URLs are
resolved once under a DNS deadline. Every resolved address must be globally
routable, and the HTTP(S) connection is then pinned to one of those exact
addresses — so the request cannot be re-routed by a second DNS answer (DNS
rebinding). HTTPS still sends SNI and verifies the certificate against the
original hostname. Redirects repeat the complete validation. One deadline covers
DNS + connect + every redirect hop + the body read, and bodies have hard byte
caps. Environment proxies are deliberately bypassed: a proxy would resolve the
target outside this policy, so on a proxied network these fetches fail closed.

Failures never raise to callers: any validation, network, redirect, timeout, or
size problem returns None ("fall back to the scraped description").
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import queue
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import HTTPError
from urllib3.util import Timeout

from config import STATE_DIR

PAGE_FETCH_TIMEOUT = 12          # one deadline: DNS + connect + redirects + body
DNS_TIMEOUT = 3
MAX_PAGE_BYTES = 3_000_000       # stop reading a body past this (DoS guard)
MAX_PAGE_TEXT_CHARS = 8_000      # extracted-text cap stored per job
MAX_URL_CHARS = 4_096
MAX_REDIRECTS = 3
PAGE_CACHE_DIR = STATE_DIR / "page_cache"
# A real UA — many job boards 403 the default requests/urllib agent.
FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_SSL_CONTEXT = ssl.create_default_context()

# Explicit special-use ranges whose classification has varied across supported
# Python patch releases. NAT64 is handled separately by unwrapping its embedded
# IPv4 target, preserving legitimate IPv6-only access while still blocking a
# synthesized private/link-local destination.
_BLOCKED_V4_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "100.64.0.0/10",       # shared address space / CGNAT
    "192.0.0.0/24",        # IETF protocol assignments
    "198.18.0.0/15",       # benchmark testing
    "240.0.0.0/4",         # reserved / limited broadcast
))
_BLOCKED_V6_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "2001:db8::/32",       # documentation
))
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")


class FetchError(RuntimeError):
    """Internal fail-closed fetch error; public functions return None."""


@dataclass(frozen=True)
class SafeResponse:
    """The already-read, size-capped result of a guarded fetch."""
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def close(self) -> None:
        """Compatibility no-op: the underlying pool is closed before return."""


# --- address policy: only globally routable targets, resolved once ------------

def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FetchError("request deadline exceeded")
    return remaining


def _ascii_hostname(hostname: str) -> str:
    if not hostname or "%" in hostname:
        raise FetchError("invalid hostname")
    try:
        result = hostname.rstrip(".").encode("idna").decode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise FetchError("invalid hostname") from exc
    if not result:
        raise FetchError("invalid hostname")
    return result


def _unwrap_embedded(address):
    """Reduce an IPv6 address that tunnels an IPv4 one to that inner IPv4.

    is_global inspects the outer address only, so ::ffff:169.254.169.254 (mapped),
    2002::/16 (6to4), Teredo, and 64:ff9b::/96 (NAT64) can read as global while
    actually reaching a private/link-local target. Checking the embedded IPv4
    closes that bypass independently of the interpreter's CVE-2024-4032 fix.
    """
    if isinstance(address, ipaddress.IPv6Address):
        # Teredo exposes (server, client); the client is the destination-bearing
        # field. Checking the public server would validate the wrong endpoint.
        for embedded in (address.ipv4_mapped, address.sixtofour,
                         address.teredo[1] if address.teredo else None,
                         ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF)
                         if address in _NAT64_NETWORK else None):
            if embedded is not None:
                return embedded
    return address


def _address_is_public(address) -> bool:
    """Stable public-address policy independent of ipaddress patch quirks."""
    blocked = (_BLOCKED_V4_NETWORKS if isinstance(address, ipaddress.IPv4Address)
               else _BLOCKED_V6_NETWORKS)
    if any(address in network for network in blocked):
        return False
    embedded = _unwrap_embedded(address)
    if embedded != address:
        return _address_is_public(embedded)
    return address.is_global


def _resolve_public_ips(hostname: str, port: int, deadline: float) -> list[str]:
    """Resolve once under a deadline and require every answer to be global.

    The resolver runs in a daemon thread because getaddrinfo has no timeout of
    its own — a stalled resolver would otherwise hold the whole run hostage.
    """
    hostname = _ascii_hostname(hostname)
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _address_is_public(literal):
            raise FetchError("non-public address")
        return [str(literal)]

    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            infos = socket.getaddrinfo(
                hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
            result_queue.put((True, infos))
        except Exception as exc:  # fail closed if the platform resolver surprises us
            result_queue.put((False, exc))

    threading.Thread(target=resolve, name="jobscout-dns", daemon=True).start()
    try:
        ok, value = result_queue.get(timeout=min(DNS_TIMEOUT, _remaining(deadline)))
    except queue.Empty as exc:
        raise FetchError("DNS resolution timed out") from exc
    if not ok:
        if isinstance(value, BaseException):
            raise FetchError("DNS resolution failed") from value
        raise FetchError("DNS resolution failed")

    addresses: list[str] = []
    for info in value:  # type: ignore[union-attr]
        raw_address = info[4][0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise FetchError("DNS returned an invalid address") from exc
        if not _address_is_public(address):
            raise FetchError("DNS returned a non-public address")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise FetchError("DNS returned no addresses")
    return addresses


def host_is_public(hostname: str) -> bool:
    """Return whether every resolved address is globally routable."""
    try:
        _resolve_public_ips(hostname, 443, time.monotonic() + DNS_TIMEOUT)
        return True
    except Exception:
        return False


# --- the pinned request -------------------------------------------------------

def _authority(hostname: str, port: int, scheme: str) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    return host if port == default_port else f"{host}:{port}"


def _request_target(parsed) -> str:
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if not parsed.query:
        return path
    query = quote(parsed.query, safe="%/:?@!$&'()*+,;=-._~")
    return f"{path}?{query}"


def _read_capped(raw, max_bytes: int, deadline: float) -> bytes:
    content_length = raw.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise FetchError("response exceeds byte cap")
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    while True:
        # Re-arm the socket timeout each chunk so a slow-loris body can't stretch
        # one read past the overall deadline.
        remaining = _remaining(deadline)
        connection = raw.connection
        if connection is not None and connection.sock is not None:
            connection.sock.settimeout(remaining)
        # Ask for at most one byte beyond the remaining budget. urllib3 2.7
        # applies this amount to decompressed output, preventing a compressed
        # response from creating a large transient decoded chunk before the cap
        # check below rejects it.
        amount = min(16_384, max_bytes - total + 1)
        chunk = raw.read(amount, decode_content=True, cache_content=False)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FetchError("response exceeds byte cap")
        chunks.append(chunk)
    return b"".join(chunks)


def _request_pinned(parsed, hostname: str, port: int, address: str,
                    headers: Mapping[str, str], deadline: float,
                    max_bytes: int) -> SafeResponse:
    """One GET against one validated IP. HTTPS keeps SNI + certificate checks on
    the ORIGINAL hostname while the TCP connection goes to the pinned address."""
    remaining = _remaining(deadline)
    timeout = Timeout(total=remaining, connect=remaining, read=remaining)
    common = {
        "host": address,
        "port": port,
        "timeout": timeout,
        "maxsize": 1,
        "block": True,
        "retries": False,
    }
    if parsed.scheme == "https":
        pool = HTTPSConnectionPool(
            **common,
            cert_reqs="CERT_REQUIRED",
            ssl_context=_SSL_CONTEXT,
            assert_hostname=hostname,
            server_hostname=hostname,
        )
    else:
        pool = HTTPConnectionPool(**common)

    raw = None
    try:
        raw = pool.request(
            "GET",
            _request_target(parsed),
            headers=headers,
            preload_content=False,
            redirect=False,
            retries=False,
            timeout=timeout,
        )
        response_headers = dict(raw.headers.items())
        if raw.status != 200:
            return SafeResponse(raw.status, response_headers, b"")
        content = _read_capped(raw, max_bytes, deadline)
        return SafeResponse(raw.status, response_headers, content)
    finally:
        if raw is not None:
            raw.close()
        pool.close()


def fetch_url_safe(url: str, accept: str = "text/html",
                   timeout: float = PAGE_FETCH_TIMEOUT,
                   max_bytes: int = MAX_PAGE_BYTES) -> SafeResponse | None:
    """Fetch one URL within a single DNS/connect/redirect/body deadline.

    The connection is pinned to an address returned by the guarded resolution;
    HTTPS verification remains tied to the original hostname. Any validation,
    network, redirect, timeout, or size failure returns None.
    """
    if (not isinstance(url, str) or len(url) > MAX_URL_CHARS
            or not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not math.isfinite(timeout) or timeout <= 0
            or not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
            or max_bytes <= 0):
        return None

    deadline = time.monotonic() + float(timeout)
    current = url
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            if len(current) > MAX_URL_CHARS:
                return None
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return None
            # userinfo in a URL ("https://user:pass@host") is a smuggling vector,
            # not something a job board needs.
            if parsed.username is not None or parsed.password is not None:
                return None
            hostname = _ascii_hostname(parsed.hostname)
            try:
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError:
                return None
            if not 1 <= port <= 65_535:
                return None

            addresses = _resolve_public_ips(hostname, port, deadline)
            headers = {
                "User-Agent": FETCH_UA,
                "Accept": accept,
                "Accept-Encoding": "gzip, deflate",
                # Host is the pinned authority — the target line carries the IP,
                # so the header must say who we actually mean.
                "Host": _authority(hostname, port, parsed.scheme),
            }
            response = None
            for address in addresses:
                try:
                    response = _request_pinned(
                        parsed, hostname, port, address, headers, deadline, max_bytes)
                    break
                except (FetchError, HTTPError, OSError, TimeoutError, ssl.SSLError):
                    continue
            if response is None:
                return None

            if response.status_code in (301, 302, 303, 307, 308):
                if redirect_count >= MAX_REDIRECTS:
                    return None
                location = response.headers.get("Location")
                if not isinstance(location, str) or not location:
                    return None
                current = urljoin(current, location)
                continue
            if response.status_code != 200:
                return None
            return response
    except Exception:
        return None
    return None


# --- feed + posting readers ---------------------------------------------------

RSS_ACCEPT = ("application/rss+xml, application/atom+xml, application/xml, "
              "text/xml, */*")


def fetch_feed_bytes(url: str) -> bytes | None:
    """Feed bytes, or None when the URL did not answer with a feed.

    The Content-Type check is load-bearing. A retired feed URL rarely 404s — it
    usually answers 200 with the site's normal HTML page. feedparser then parses
    zero entries and raises nothing, so a dead feed looks identical to a quiet
    one and can stay dead for months unnoticed. Denylist "html" rather than
    allowlisting XML: real feeds ship under a wide spread of types (even typo'd
    charsets), while "this is an HTML page" is the unambiguous failure signal.

    Callers MUST parse these bytes directly and never hand the URL to the feed
    parser: feedparser, given a URL, fetches it itself with no timeout and no
    SSRF guard — which would defeat this whole choke point.
    """
    response = fetch_url_safe(url, accept=RSS_ACCEPT, max_bytes=MAX_PAGE_BYTES)
    if response is None:
        return None
    if "html" in response.headers.get("Content-Type", "").lower():
        return None
    return response.content


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
    response = fetch_url_safe(url, accept="text/html", max_bytes=MAX_PAGE_BYTES)
    if response is None:
        return None
    if "html" not in response.headers.get("Content-Type", "").lower():
        return None
    return _extract_text(response.content)


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
