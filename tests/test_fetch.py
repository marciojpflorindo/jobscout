"""Fetch-safety guards: the SSRF host check and the scheme guard. These must
hold without any network access."""

import unittest

import pathsetup  # noqa: F401
import fetch as F


class TestHostIsPublic(unittest.TestCase):
    def test_blocks_loopback(self):
        self.assertFalse(F.host_is_public("127.0.0.1"))

    def test_blocks_private(self):
        self.assertFalse(F.host_is_public("10.0.0.1"))
        self.assertFalse(F.host_is_public("192.168.1.1"))
        self.assertFalse(F.host_is_public("172.16.0.1"))

    def test_blocks_cloud_metadata(self):
        self.assertFalse(F.host_is_public("169.254.169.254"))

    def test_blocks_unspecified(self):
        self.assertFalse(F.host_is_public("0.0.0.0"))

    def test_blocks_empty(self):
        self.assertFalse(F.host_is_public(""))

    def test_blocks_unresolvable(self):
        self.assertFalse(F.host_is_public("no.such.host.invalid"))

    def test_allows_public_literal(self):
        self.assertTrue(F.host_is_public("8.8.8.8"))
        self.assertTrue(F.host_is_public("1.1.1.1"))


class TestSchemeGuard(unittest.TestCase):
    def test_non_http_schemes_rejected_without_network(self):
        # file:// and ftp:// never reach a socket -> None from the guard alone.
        self.assertIsNone(F.fetch_url_safe("file:///etc/passwd"))
        self.assertIsNone(F.fetch_url_safe("ftp://example.com/x"))
        self.assertIsNone(F.fetch_url_safe("gopher://example.com"))

    def test_no_host_rejected(self):
        self.assertIsNone(F.fetch_url_safe("http://"))

    def test_internal_host_rejected_before_fetch(self):
        # Loopback target: blocked by host_is_public, no connection attempted.
        self.assertIsNone(F.fetch_url_safe("http://127.0.0.1:11434/api/tags"))


class TestFeedAndPostingGuards(unittest.TestCase):
    def test_feed_bytes_none_on_blocked_host(self):
        self.assertIsNone(F.fetch_feed_bytes("http://127.0.0.1/feed.xml"))
        self.assertIsNone(F.fetch_feed_bytes("file:///etc/hosts"))

    def test_empty_url_posting_cache_none(self):
        self.assertIsNone(F.fetch_posting_text_cached(""))


if __name__ == "__main__":
    unittest.main()
