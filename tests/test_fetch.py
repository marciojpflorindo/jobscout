"""Pinned SSRF fetch: address policy, DNS deadlines, redirects, byte limits, and
the feed/posting content-type guards. These must hold without network access."""

import socket
import time
import types
import unittest
from unittest.mock import patch

import pathsetup  # noqa: F401
import fetch as F


class FakeSocket:
    def __init__(self):
        self.timeouts: list[float] = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)


class FakeRawResponse:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status = status
        self.headers = headers or {}
        self._chunks = list(chunks)
        self.connection = types.SimpleNamespace(sock=FakeSocket())
        self.closed = False
        self.read_amounts: list[int] = []

    def read(self, amount, decode_content=True, cache_content=False):
        self.read_amounts.append(amount)
        return self._chunks.pop(0) if self._chunks else b""

    def close(self):
        self.closed = True


class FakePool:
    responses: list[FakeRawResponse] = []
    initializations: list[dict] = []
    requests: list[dict] = []

    def __init__(self, **kwargs):
        self.initializations.append(kwargs)

    def request(self, method, target, **kwargs):
        self.requests.append({"method": method, "target": target, **kwargs})
        return self.responses.pop(0)

    def close(self):
        pass

    @classmethod
    def reset(cls, *responses):
        cls.responses = list(responses)
        cls.initializations = []
        cls.requests = []


class TestAddressPolicy(unittest.TestCase):
    def test_blocks_non_global_literals_including_cgnat(self):
        for address in ("127.0.0.1", "10.0.0.1", "192.168.1.1",
                        "169.254.169.254", "100.64.0.1", "192.0.0.10",
                        "198.18.0.1", "240.0.0.1", "2001:db8::1", "0.0.0.0"):
            with self.subTest(address=address):
                self.assertFalse(F.host_is_public(address))

    def test_blocks_embedded_ipv4_bypasses(self):
        # IPv6 wrappers around a private IPv4: mapped, 6to4, NAT64, and Teredo.
        # is_global on the outer address (or Teredo's public server field) can
        # otherwise let these through.
        for address in (
            "::ffff:169.254.169.254",
            "2002:7f00:1::",
            "64:ff9b::7f00:1",
            "2001:0000:0808:0808:0000:ffff:80ff:fffe",
        ):
            with self.subTest(address=address):
                self.assertFalse(F.host_is_public(address))

    def test_allows_nat64_wrapping_a_public_ipv4(self):
        self.assertTrue(F.host_is_public("64:ff9b::808:808"))

    def test_allows_global_literals(self):
        self.assertTrue(F.host_is_public("8.8.8.8"))
        self.assertTrue(F.host_is_public("1.1.1.1"))

    def test_blocks_empty(self):
        self.assertFalse(F.host_is_public(""))

    def test_mixed_public_and_private_dns_fails_closed(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with patch.object(F.socket, "getaddrinfo", return_value=answers):
            self.assertFalse(F.host_is_public("example.test"))

    def test_unresolvable_and_malformed_hosts_fail_closed(self):
        with patch.object(F.socket, "getaddrinfo", side_effect=socket.gaierror()):
            self.assertFalse(F.host_is_public("missing.test"))
        self.assertFalse(F.host_is_public("\ud800.example"))

    def test_dns_resolution_has_a_deadline(self):
        def slow_dns(*_args, **_kwargs):
            time.sleep(0.2)
            return []

        started = time.monotonic()
        with patch.object(F.socket, "getaddrinfo", side_effect=slow_dns):
            with self.assertRaises(F.FetchError):
                F._resolve_public_ips("slow.test", 443, time.monotonic() + 0.02)
        self.assertLess(time.monotonic() - started, 0.15)


class TestPinnedFetch(unittest.TestCase):
    def setUp(self):
        FakePool.reset()

    def test_https_connects_to_validated_ip_but_verifies_original_host(self):
        FakePool.reset(FakeRawResponse(
            headers={"Content-Type": "text/html"}, chunks=[b"ok"]))
        with patch.object(F, "_resolve_public_ips", return_value=["93.184.216.34"]), \
                patch.object(F, "HTTPSConnectionPool", FakePool):
            response = F.fetch_url_safe("https://example.com/jobs?q=analyst")

        self.assertEqual(response.content, b"ok")
        init = FakePool.initializations[0]
        self.assertEqual(init["host"], "93.184.216.34")
        self.assertEqual(init["server_hostname"], "example.com")
        self.assertEqual(init["assert_hostname"], "example.com")
        self.assertEqual(init["cert_reqs"], "CERT_REQUIRED")
        request = FakePool.requests[0]
        self.assertEqual(request["headers"]["Host"], "example.com")
        self.assertEqual(request["target"], "/jobs?q=analyst")

    def test_dns_is_resolved_once_then_connection_is_pinned(self):
        # The DNS-rebinding scenario: a second resolution would answer 127.0.0.1.
        # The pinned connection must never ask twice.
        calls = 0

        def alternating_dns(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            address = "93.184.216.34" if calls == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

        FakePool.reset(FakeRawResponse(chunks=[b"ok"]))
        with patch.object(F.socket, "getaddrinfo", side_effect=alternating_dns), \
                patch.object(F, "HTTPSConnectionPool", FakePool):
            response = F.fetch_url_safe("https://rebind.test/job")
        self.assertIsNotNone(response)
        self.assertEqual(calls, 1)
        self.assertEqual(FakePool.initializations[0]["host"], "93.184.216.34")

    def test_redirect_repeats_resolution_for_new_host(self):
        hosts: list[str] = []

        def resolve(hostname, _port, _deadline):
            hosts.append(hostname)
            return ["93.184.216.34"]

        FakePool.reset(
            FakeRawResponse(status=302, headers={"Location": "https://other.test/final"}),
            FakeRawResponse(chunks=[b"done"]),
        )
        with patch.object(F, "_resolve_public_ips", side_effect=resolve), \
                patch.object(F, "HTTPSConnectionPool", FakePool):
            response = F.fetch_url_safe("https://first.test/start")
        self.assertEqual(response.content, b"done")
        self.assertEqual(hosts, ["first.test", "other.test"])

    def test_redirect_to_non_public_host_is_rejected(self):
        def resolve(hostname, _port, _deadline):
            if hostname == "internal.test":
                raise F.FetchError("private")
            return ["93.184.216.34"]

        FakePool.reset(FakeRawResponse(
            status=302, headers={"Location": "http://internal.test/admin"}))
        with patch.object(F, "_resolve_public_ips", side_effect=resolve), \
                patch.object(F, "HTTPSConnectionPool", FakePool):
            self.assertIsNone(F.fetch_url_safe("https://first.test/start"))

    def test_body_cap_fails_closed_and_exact_limit_succeeds(self):
        for chunks, expected in (([b"123", b"45"], b"12345"),
                                 ([b"123", b"456"], None)):
            FakePool.reset(FakeRawResponse(chunks=chunks))
            with patch.object(F, "_resolve_public_ips", return_value=["93.184.216.34"]), \
                    patch.object(F, "HTTPSConnectionPool", FakePool):
                response = F.fetch_url_safe("https://example.test", max_bytes=5)
            self.assertEqual(response.content if response else None, expected)

    def test_body_reads_at_most_one_byte_past_remaining_budget(self):
        raw = FakeRawResponse(chunks=[b"123456"])
        FakePool.reset(raw)
        with patch.object(F, "_resolve_public_ips", return_value=["93.184.216.34"]), \
                patch.object(F, "HTTPSConnectionPool", FakePool):
            self.assertIsNone(F.fetch_url_safe("https://example.test", max_bytes=5))
        self.assertEqual(raw.read_amounts, [6])

    def test_oversized_content_length_fails_before_body_read(self):
        raw = FakeRawResponse(headers={"Content-Length": "6"}, chunks=[b"123456"])
        FakePool.reset(raw)
        with patch.object(F, "_resolve_public_ips", return_value=["93.184.216.34"]), \
                patch.object(F, "HTTPSConnectionPool", FakePool):
            self.assertIsNone(F.fetch_url_safe("https://example.test", max_bytes=5))
        self.assertEqual(raw._chunks, [b"123456"])

    def test_invalid_url_forms_never_connect(self):
        invalid = (
            "file:///etc/passwd", "ftp://example.com/x", "gopher://example.com",
            "http://", "https://user:pass@example.com", "https://\ud800.example",
            "https://example.com:99999",
        )
        with patch.object(F, "_request_pinned") as request:
            for url in invalid:
                with self.subTest(url=repr(url)):
                    self.assertIsNone(F.fetch_url_safe(url))
        request.assert_not_called()


class TestFeedAndPosting(unittest.TestCase):
    def test_feed_returns_already_capped_response_bytes(self):
        response = F.SafeResponse(200, {"Content-Type": "application/xml"}, b"<rss/>")
        with patch.object(F, "fetch_url_safe", return_value=response) as fetch:
            self.assertEqual(F.fetch_feed_bytes("https://example.com/feed"), b"<rss/>")
        self.assertEqual(fetch.call_args.kwargs["max_bytes"], F.MAX_PAGE_BYTES)

    def test_feed_rejects_an_html_page_served_as_a_feed(self):
        # A retired feed URL rarely 404s — it serves the site's HTML page with a
        # 200, feedparser parses 0 entries, and the source dies in total silence.
        for content_type in ("text/html", "text/html; charset=utf-8", "TEXT/HTML"):
            with self.subTest(content_type=content_type):
                response = F.SafeResponse(200, {"Content-Type": content_type},
                                          b"<!DOCTYPE html><html><body>nope</body></html>")
                with patch.object(F, "fetch_url_safe", return_value=response):
                    self.assertIsNone(F.fetch_feed_bytes("https://example.com/feed"))

    def test_feed_accepts_the_content_types_real_feeds_use(self):
        # Deliberately a denylist, not an XML allowlist: real feeds ship under a
        # wide spread of types — including typo'd charsets — and some send none
        # at all. Only "this is HTML" is an unambiguous failure.
        for content_type in ("application/rss+xml", "text/xml; charset=uft-8",
                             "application/atom+xml", ""):
            with self.subTest(content_type=content_type):
                response = F.SafeResponse(200, {"Content-Type": content_type}, b"<rss/>")
                with patch.object(F, "fetch_url_safe", return_value=response):
                    self.assertEqual(F.fetch_feed_bytes("https://example.com/feed"), b"<rss/>")

    def test_posting_rejects_non_html(self):
        response = F.SafeResponse(200, {"Content-Type": "application/pdf"}, b"pdf")
        with patch.object(F, "fetch_url_safe", return_value=response):
            self.assertIsNone(F.fetch_posting_text("https://example.com/job"))

    def test_posting_extracts_and_caps_html_text(self):
        html = b"<html><script>bad()</script><body>Hello <b>world</b></body></html>"
        response = F.SafeResponse(200, {"Content-Type": "text/html"}, html)
        with patch.object(F, "fetch_url_safe", return_value=response):
            self.assertEqual(F.fetch_posting_text("https://example.com/job"), "Hello world")

    def test_feed_bytes_none_on_blocked_host(self):
        self.assertIsNone(F.fetch_feed_bytes("http://127.0.0.1/feed.xml"))
        self.assertIsNone(F.fetch_feed_bytes("file:///etc/hosts"))

    def test_empty_url_posting_cache_none(self):
        self.assertIsNone(F.fetch_posting_text_cached(""))


if __name__ == "__main__":
    unittest.main()
