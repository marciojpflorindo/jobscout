"""Dashboard API contracts consumed by the brain."""

import unittest

import pathsetup  # noqa: F401
import server


class _FakeStore:
    def __init__(self, rows=None, rejected=None):
        self.rows = rows or []
        self.rejected = rejected or {}

    def get_rows(self):
        return self.rows

    def get_rejected(self):
        return self.rejected


class TestApiLinks(unittest.TestCase):
    def setUp(self):
        self.original_store = server.STORE

    def tearDown(self):
        server.STORE = self.original_store

    def test_rejected_ledger_preserves_source_for_feedback_policy(self):
        server.STORE = _FakeStore(rejected={
            "https://example.com/job": {
                "reason": "too much travel", "source": "user", "date": "22-07-2026",
            },
        })
        captured = {}
        handler = server.Handler.__new__(server.Handler)
        handler._send_json = lambda status, body: captured.update(status=status, body=body)

        handler._api_links()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["body"]["exclude"], [{
            "link": "https://example.com/job",
            "status": "no",
            "why": "too much travel",
            "source": "user",
        }])


if __name__ == "__main__":
    unittest.main()
