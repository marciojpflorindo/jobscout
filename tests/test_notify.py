"""ntfy run notifications: config block parses + fails closed; the notify layer
sends only the three fixed generic templates and never POSTs when disabled."""

import unittest

import pathsetup  # noqa: F401
import config as C
import notify as N


class TestParseNtfy(unittest.TestCase):
    def test_missing_block_is_none(self):
        self.assertIsNone(C.parse_ntfy(None))
        self.assertIsNone(C.parse_ntfy({}))

    def test_disabled_is_none(self):
        self.assertIsNone(C.parse_ntfy(
            {"enabled": False, "server": "https://ntfy.sh", "topic": "jobscout-x"}))

    def test_valid_block(self):
        n = C.parse_ntfy(
            {"enabled": True, "server": "https://ntfy.sh", "topic": "jobscout-abc123"})
        self.assertIsNotNone(n)
        self.assertEqual(n.server, "https://ntfy.sh")
        self.assertEqual(n.topic, "jobscout-abc123")
        self.assertTrue(n.enabled)

    def test_server_defaults_when_absent(self):
        n = C.parse_ntfy({"enabled": True, "topic": "jobscout-abc"})
        self.assertEqual(n.server, C.DEFAULT_NTFY_SERVER)

    def test_trailing_slash_stripped(self):
        n = C.parse_ntfy(
            {"enabled": True, "server": "https://ntfy.sh/", "topic": "jobscout-abc"})
        self.assertEqual(n.server, "https://ntfy.sh")

    def test_bad_topic_fails_closed(self):
        for bad in ("", "has space", "with/slash", "x" * 65, "-leadingdash"):
            self.assertIsNone(C.parse_ntfy(
                {"enabled": True, "server": "https://ntfy.sh", "topic": bad}), bad)

    def test_non_http_server_fails_closed(self):
        for bad in ("ftp://ntfy.sh", "file:///etc/passwd", "javascript:alert(1)", "ntfy.sh"):
            self.assertIsNone(C.parse_ntfy(
                {"enabled": True, "server": bad, "topic": "jobscout-abc"}), bad)

    def test_self_hosted_lan_server_allowed(self):
        # Self-hosting on a private address is a feature, not an SSRF target — the
        # value is the user's own config, and notify only POSTs (no fetch-and-read).
        n = C.parse_ntfy(
            {"enabled": True, "server": "http://192.168.1.5:8080", "topic": "jobscout-abc"})
        self.assertEqual(n.server, "http://192.168.1.5:8080")


class TestTemplates(unittest.TestCase):
    def test_three_fixed_templates(self):
        self.assertEqual(set(N.TEMPLATES), {"new", "none", "failure"})

    def test_template_selection(self):
        self.assertIn("new potential jobs", N.template("new"))
        self.assertIn("no new matches", N.template("none"))
        self.assertIn("run failed", N.template("failure"))

    def test_unknown_kind_is_none(self):
        self.assertIsNone(N.template("bogus"))

    def test_templates_carry_no_placeholders(self):
        # No format slots -> impossible to interpolate a count/title/URL/error.
        for body in N.TEMPLATES.values():
            self.assertNotIn("{", body)
            self.assertNotIn("%", body)


class TestNotifyRun(unittest.TestCase):
    def test_none_config_is_silent_noop(self):
        self.assertFalse(N.notify_run(None, "new"))

    def test_disabled_config_is_silent_noop(self):
        n = C.Ntfy(server="https://ntfy.sh", topic="jobscout-x", enabled=False)
        self.assertFalse(N.notify_run(n, "new"))

    def test_unknown_kind_sends_nothing(self):
        sent = []
        orig = N._post
        N._post = lambda *a: sent.append(a) or True
        try:
            n = C.Ntfy(server="https://ntfy.sh", topic="jobscout-x")
            self.assertFalse(N.notify_run(n, "bogus"))
            self.assertEqual(sent, [])
        finally:
            N._post = orig

    def test_enabled_posts_fixed_body(self):
        captured = {}
        orig = N._post

        def fake_post(server, topic, body):
            captured.update(server=server, topic=topic, body=body)
            return True

        N._post = fake_post
        try:
            n = C.Ntfy(server="https://ntfy.sh", topic="jobscout-x")
            self.assertTrue(N.notify_run(n, "new"))
            self.assertEqual(captured["body"], N.TEMPLATES["new"])
            self.assertEqual(captured["topic"], "jobscout-x")
        finally:
            N._post = orig

    def test_post_rejects_non_http(self):
        self.assertFalse(N._post("ftp://ntfy.sh", "jobscout-x", "hi"))

    def test_post_rejects_empty_target(self):
        self.assertFalse(N._post("", "jobscout-x", "hi"))
        self.assertFalse(N._post("https://ntfy.sh", "", "hi"))


if __name__ == "__main__":
    unittest.main()
