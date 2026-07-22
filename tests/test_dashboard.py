"""The brain's dashboard client: rejection feedback comes ONLY from the user.

The judge's "USER-REJECTED PATTERNS" block instructs the model to treat listed
reasons as red flags. Feeding it its own past verdicts (the reject ledger's "no"
entries, or the machine note a rejected row still carries) would make it learn
from itself — the echo these tests pin down.
"""

import unittest

import pathsetup  # noqa: F401
import dashboard as D


class TestUserFeedback(unittest.TestCase):
    def test_pure_machine_note_is_dropped(self):
        # A row rejected from the tracker without a note still carries the brain's
        # published verdict — that must never read as user feedback.
        self.assertEqual(D.user_feedback("maybe 55/100: duties look adjacent"), "")
        self.assertEqual(D.user_feedback("match 90/100: strong fit"), "")
        self.assertEqual(D.user_feedback("no 20/100: hard blocker"), "")
        self.assertEqual(D.user_feedback("match 90/100   "), "")

    def test_machine_note_case_and_padding(self):
        self.assertEqual(D.user_feedback("  Maybe 60/100: x"), "")

    def test_review_reject_keeps_user_reason_only(self):
        # The Review reject flow writes "Rejected: <reason>\n\n<old machine note>".
        note = "Rejected: too much travel\n\nmaybe 55/100: duties look adjacent"
        self.assertEqual(D.user_feedback(note), "too much travel")

    def test_plain_user_note_passes_through(self):
        self.assertEqual(D.user_feedback("agency role, not in-house"),
                         "agency role, not in-house")

    def test_multiline_user_note_collapses_to_one_line(self):
        self.assertEqual(D.user_feedback("too senior\nfor me"), "too senior for me")

    def test_empty_and_none(self):
        self.assertEqual(D.user_feedback(""), "")
        self.assertEqual(D.user_feedback(None), "")

    def test_verdict_word_mid_sentence_is_not_machine(self):
        # Only START-anchored verdict lines are machine; a user sentence that
        # merely contains a verdict word must survive.
        self.assertEqual(D.user_feedback("no relocation offered"),
                         "no relocation offered")

    def test_user_note_that_starts_like_a_score_is_not_eaten(self):
        self.assertEqual(D.user_feedback("no 1/100 chance I would relocate"),
                         "no 1/100 chance I would relocate")


class TestExclusionFeedback(unittest.TestCase):
    """exclusion() parses /api/links: every link excludes, but only user-actioned
    statuses with user-authored notes become rejection reasons."""

    def setUp(self):
        self._get = D._get
        self.payload = {}
        D._get = lambda base, path: self.payload

    def tearDown(self):
        D._get = self._get

    def _exclusion(self, entries):
        self.payload = {"exclude": entries}
        return D.exclusion("http://x")

    def test_model_no_entries_exclude_but_teach_nothing(self):
        excl = self._exclusion([
            {"link": "https://a.com/1", "status": "no", "why": "wrong domain"},
        ])
        self.assertIn("https://a.com/1", excl.links)
        self.assertEqual(excl.rejection_reasons, [])

    def test_user_ledger_entry_teaches_after_row_is_deleted(self):
        excl = self._exclusion([
            {"link": "https://a.com/1", "status": "no", "source": "user",
             "why": "too much travel"},
        ])
        self.assertEqual(excl.rejection_reasons, ["too much travel"])

    def test_user_rejected_row_teaches_its_reason(self):
        excl = self._exclusion([
            {"link": "https://a.com/1", "status": "Rejected",
             "why": "Rejected: agency role\n\nmaybe 55/100: adjacent"},
        ])
        self.assertEqual(excl.rejection_reasons, ["agency role"])

    def test_rejected_row_with_machine_only_note_teaches_nothing(self):
        # Employer-rejected application whose Notes still hold the brain's praise:
        # the old code fed "match 78/100: …" back as a user red flag.
        excl = self._exclusion([
            {"link": "https://a.com/1", "status": "Rejected",
             "why": "match 78/100: strong fit"},
        ])
        self.assertIn("https://a.com/1", excl.links)
        self.assertEqual(excl.rejection_reasons, [])

    def test_declined_row_user_note_teaches(self):
        excl = self._exclusion([
            {"link": "https://a.com/1", "status": "Declined", "why": "salary too low"},
        ])
        self.assertEqual(excl.rejection_reasons, ["salary too low"])

    def test_non_rejected_statuses_teach_nothing(self):
        excl = self._exclusion([
            {"link": "https://a.com/1", "status": "Applied", "why": "great fit"},
            {"link": "https://a.com/2", "status": "Potential", "why": "note"},
        ])
        self.assertEqual(excl.rejection_reasons, [])
        self.assertEqual(len(excl.links), 2)


class TestNormalizeUrl(unittest.TestCase):
    def test_tracking_params_and_fragment_dropped(self):
        self.assertEqual(
            D.normalize_url("https://x.com/job/1?utm_source=a&gh_src=b&id=7#top"),
            D.normalize_url("https://X.com/job/1/?id=7"))

    def test_prose_around_url_ignored(self):
        self.assertEqual(
            D.normalize_url("see https://x.com/job/1, applied twice"),
            D.normalize_url("https://x.com/job/1"))

    def test_no_url_is_empty(self):
        self.assertEqual(D.normalize_url("no link here"), "")
        self.assertEqual(D.normalize_url(""), "")


class TestFuzzyPositionMatching(unittest.TestCase):
    """Boards re-post the same job under fresh URLs and reworded titles; the
    fuzzy block is what keeps a handled position from re-surfacing."""

    def test_same_company_ignores_legal_suffixes(self):
        self.assertTrue(D.same_company("google inc", "google llc"))
        self.assertTrue(D.same_company("camunda", "camunda services gmbh"))

    def test_same_company_no_substring_false_positive(self):
        self.assertFalse(D.same_company("meta", "metabase"))

    def test_similar_title_reworded_repost_matches(self):
        a = D.normalize_text("Data Engineer")
        b = D.normalize_text("Data Engineer - Remote")
        self.assertTrue(D.similar_title(a, b))

    def test_similar_title_explicit_abbreviation_matches(self):
        a = D.normalize_text("Technical Writer")
        b = D.normalize_text("Tech Writer")
        self.assertTrue(D.similar_title(a, b))

    def test_similar_title_different_role_noun_does_not_match(self):
        # High char overlap, different job — must NOT fuse.
        a = D.normalize_text("Technical Writer")
        b = D.normalize_text("Technical Recruiter")
        self.assertFalse(D.similar_title(a, b))

    def test_similar_title_adjacent_rank_stays_visible(self):
        a = D.normalize_text("Senior Product Manager")
        b = D.normalize_text("Staff Product Manager")
        self.assertFalse(D.similar_title(a, b))

    def test_similar_title_level_on_one_side_still_matches(self):
        a = D.normalize_text("Data Engineer")
        b = D.normalize_text("Senior Data Engineer")
        self.assertTrue(D.similar_title(a, b))

    def test_similar_title_seniority_aliases_match(self):
        a = D.normalize_text("Senior Technical Writer")
        b = D.normalize_text("Sr. Technical Writer")
        self.assertTrue(D.similar_title(a, b))

    def test_similar_title_prefixes_do_not_hide_other_roles(self):
        different_roles = (
            ("Data Engineer", "Data Engineering Manager"),
            ("Product Manager", "Production Manager"),
        )
        for first, second in different_roles:
            with self.subTest(first=first, second=second):
                self.assertFalse(D.similar_title(
                    D.normalize_text(first), D.normalize_text(second)))

    def test_similar_title_meaningful_surplus_stays_visible(self):
        for qualifier in ("Manager", "Contract", "Intern"):
            with self.subTest(qualifier=qualifier):
                self.assertFalse(D.similar_title(
                    D.normalize_text("Data Engineer"),
                    D.normalize_text(f"Data Engineer {qualifier}")))

    def test_position_blocked_end_to_end(self):
        blocked = {"acme": {D.normalize_text("Senior Data Engineer")}}
        self.assertTrue(D.position_blocked(
            blocked, "Acme, Inc.", "Senior Data Engineer (Remote)"))
        self.assertFalse(D.position_blocked(blocked, "Other Co", "Senior Data Engineer"))
        self.assertFalse(D.position_blocked(blocked, "", "Senior Data Engineer"))


class TestExclusionPositions(unittest.TestCase):
    def setUp(self):
        self._get = D._get
        self.payload = {}
        D._get = lambda base, path: self.payload

    def tearDown(self):
        D._get = self._get

    def test_rows_with_company_and_title_build_the_blocked_map(self):
        self.payload = {"exclude": [
            {"link": "https://a.com/1", "status": "Applied",
             "company": "Acme Inc", "title": "Backend Developer"},
            {"link": "", "status": "Rejected",
             "company": "Globex", "title": "Docs Lead"},  # link-less row still teaches
            {"link": "https://a.com/2", "status": "no", "why": "x"},  # no fields: no block
        ]}
        excl = D.exclusion("http://x")
        self.assertTrue(D.position_blocked(excl.blocked_positions, "Acme", "Backend Developer"))
        self.assertTrue(D.position_blocked(excl.blocked_positions, "Globex", "Docs Lead"))
        self.assertFalse(D.position_blocked(excl.blocked_positions, "Initech", "Developer"))

    def test_links_are_normalize_urld(self):
        self.payload = {"exclude": [
            {"link": "https://A.com/job/1/?utm_source=x", "status": "Applied"},
        ]}
        excl = D.exclusion("http://x")
        self.assertIn(D.normalize_url("https://a.com/job/1"), excl.links)


if __name__ == "__main__":
    unittest.main()
