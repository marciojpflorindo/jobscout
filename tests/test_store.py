"""Dashboard store layer: schema/allowlist, formula-injection guard, date
normalization, CSV re-validation, and the JSON store's fail-closed behavior."""

import os
import tempfile
import unittest

import pathsetup  # noqa: F401  (sys.path side effect)
import store as S


class TestSanitize(unittest.TestCase):
    def test_trims_and_caps(self):
        self.assertEqual(S.sanitize("  hi  "), "hi")
        self.assertEqual(len(S.sanitize("x" * 5000)), S.MAX_LEN)

    def test_formula_injection_is_neutralized(self):
        for payload in ("=SUM(A1)", "+1", "-1", "@cmd"):
            self.assertTrue(S.sanitize(payload).startswith("'"),
                            f"{payload!r} should be quote-prefixed")

    def test_leading_whitespace_controls_stripped(self):
        # Leading tab/CR are removed by strip() before storage (also safe — the
        # stored value can't start with a control char that Excel would execute).
        self.assertEqual(S.sanitize("\t=cmd"), "'=cmd")  # tab gone, = still guarded
        self.assertEqual(S.sanitize("\rplain"), "plain")

    def test_plain_text_untouched(self):
        self.assertEqual(S.sanitize("Acme Corp"), "Acme Corp")

    def test_none_is_empty(self):
        self.assertEqual(S.sanitize(None), "")


class TestNormalizeDate(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(S.normalize_date("2026-03-09"), "09-03-2026")

    def test_slash_and_two_digit_year(self):
        self.assertEqual(S.normalize_date("9/3/26"), "09-03-2026")

    def test_textual_month(self):
        self.assertEqual(S.normalize_date("9 March 2026"), "09-03-2026")

    def test_unparseable_kept_verbatim(self):
        self.assertEqual(S.normalize_date("sometime soon"), "sometime soon")

    def test_invalid_day_month_rejected(self):
        # 45-99-2026 fails the 1..31 / 1..12 check -> empty, never a bad date.
        self.assertEqual(S.normalize_date("2026-99-45"), "")

    def test_blank(self):
        self.assertEqual(S.normalize_date(""), "")


class TestMonthFromDate(unittest.TestCase):
    def test_extracts_month_name(self):
        self.assertEqual(S.month_from_date("09-03-2026"), "March")

    def test_bad_input(self):
        self.assertEqual(S.month_from_date("garbage"), "")


class TestCleanRow(unittest.TestCase):
    def test_status_allowlist_fails_closed(self):
        row = S.clean_row({"Company": "Acme", "Status": "Hacked"})
        self.assertEqual(row["Status"], S.DEFAULT_STATUS)

    def test_valid_status_kept(self):
        row = S.clean_row({"Company": "Acme", "Status": "Interviewing"})
        self.assertEqual(row["Status"], "Interviewing")

    def test_derives_month_from_date(self):
        row = S.clean_row({"Company": "Acme", "Date": "2026-03-09"})
        self.assertEqual(row["Month"], "March")

    def test_has_all_columns(self):
        row = S.clean_row({"Company": "Acme"})
        self.assertEqual(set(row), set(S.COLUMNS))

    def test_job_link_capped(self):
        row = S.clean_row({"Company": "Acme", "Job link": "h" * 1000})
        self.assertLessEqual(len(row["Job link"]), 500)


class TestParseCSV(unittest.TestCase):
    def test_quotes_and_embedded_newline(self):
        csv = 'Company,Notes\r\n"Acme, Inc.","line1\nline2"\r\n'
        rows = S.parse_csv(csv)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Company"], "Acme, Inc.")
        self.assertIn("line1", rows[0]["Notes"])

    def test_escaped_quote(self):
        rows = S.parse_csv('Company\r\n"A ""B"" C"\r\n')
        self.assertEqual(rows[0]["Company"], 'A "B" C')

    def test_blank_lines_skipped(self):
        rows = S.parse_csv("Company\r\nAcme\r\n\r\n")
        self.assertEqual(len(rows), 1)

    def test_revalidates_status(self):
        rows = S.parse_csv("Company,Status\r\nAcme,Bogus\r\n")
        self.assertEqual(rows[0]["Status"], S.DEFAULT_STATUS)

    def test_too_large_rejected(self):
        with self.assertRaises(S.ValidationError):
            S.parse_csv("x" * (S.MAX_IMPORT + 1))


class TestJobStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "store.json")
        self.store = S.JobStore(self.path)

    def test_missing_file_reads_empty(self):
        self.assertEqual(self.store.get_rows(), [])
        self.assertEqual(self.store.get_rejected(), {})

    def test_corrupt_file_recovers_empty(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(self.store.get_rows(), [])

    def test_non_dict_json_recovers_empty(self):
        with open(self.path, "w") as f:
            f.write("[1, 2, 3]")
        self.assertEqual(self.store.get_rows(), [])

    def test_mutate_and_persist(self):
        self.store.mutate_rows(lambda rows: rows.append({"Company": "Acme"}))
        self.assertEqual(self.store.get_rows()[0]["Company"], "Acme")
        # A fresh handle on the same file sees the persisted write.
        self.assertEqual(S.JobStore(self.path).get_rows()[0]["Company"], "Acme")

    def test_validationerror_aborts_write(self):
        def boom(rows):
            rows.append({"Company": "X"})
            raise S.ValidationError("no")
        with self.assertRaises(S.ValidationError):
            self.store.mutate_rows(boom)
        self.assertEqual(self.store.get_rows(), [])  # nothing written

    def test_replace_rows(self):
        self.store.replace_rows([{"Company": "B"}])
        self.assertEqual(self.store.get_rows(), [{"Company": "B"}])

    def test_mutate_rejected(self):
        self.store.mutate_rejected(lambda led: led.__setitem__("u", {"reason": "x"}))
        self.assertIn("u", self.store.get_rejected())


if __name__ == "__main__":
    unittest.main()
