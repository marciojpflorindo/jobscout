"""ATS: CV text extraction + quality gate (fail closed), and the CV-score
parser's fail-closed validation."""

import os
import tempfile
import unittest
import zipfile

import pathsetup  # noqa: F401
from ats import cv as CV
from ats import scorer as SC

# Enough real prose to clear MIN_CV_CHARS and the text-ratio gate.
GOOD_CV = ("Experienced backend engineer. " * 20).strip()


def _write(dirpath, name, data, mode="w", encoding="utf-8"):
    p = os.path.join(dirpath, name)
    if "b" in mode:
        with open(p, mode) as f:
            f.write(data)
    else:
        with open(p, mode, encoding=encoding) as f:
            f.write(data)
    return p


def _make_docx(path, paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
           + body + "</w:body></w:document>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


class TestLoadCV(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_reads_markdown(self):
        p = _write(self.dir, "cv.md", "# CV\n\n" + GOOD_CV)
        self.assertIn("backend engineer", CV.load_cv_text(p))

    def test_reads_txt(self):
        p = _write(self.dir, "cv.txt", GOOD_CV)
        self.assertIn("backend engineer", CV.load_cv_text(p))

    def test_reads_docx(self):
        p = os.path.join(self.dir, "cv.docx")
        _make_docx(p, ["Experienced backend engineer."] * 20)
        self.assertIn("backend engineer", CV.load_cv_text(p))

    def test_missing_file(self):
        with self.assertRaises(CV.CVError):
            CV.load_cv_text(os.path.join(self.dir, "nope.md"))

    def test_unsupported_ext(self):
        p = _write(self.dir, "cv.rtf", GOOD_CV)
        with self.assertRaises(CV.CVError):
            CV.load_cv_text(p)

    def test_oversize_rejected(self):
        p = _write(self.dir, "big.txt", "x" * (CV.MAX_CV_BYTES + 1))
        with self.assertRaises(CV.CVError):
            CV.load_cv_text(p)

    def test_too_short_rejected_by_quality_gate(self):
        p = _write(self.dir, "tiny.txt", "hi there")
        with self.assertRaises(CV.CVError):
            CV.load_cv_text(p)

    def test_binary_mess_rejected(self):
        p = _write(self.dir, "junk.txt", "%&^*#@!~`" * 100)
        with self.assertRaises(CV.CVError):
            CV.load_cv_text(p)

    def test_bad_docx_zip_rejected(self):
        p = _write(self.dir, "fake.docx", "not a zip", mode="w")
        with self.assertRaises(CV.CVError):
            CV.load_cv_text(p)

    def test_docx_without_document_xml_rejected(self):
        p = os.path.join(self.dir, "empty.docx")
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("other.xml", "<x/>")
        with self.assertRaises(CV.CVError):
            CV.load_cv_text(p)

    def test_result_capped(self):
        p = _write(self.dir, "long.md", "word " * 5000)
        self.assertLessEqual(len(CV.load_cv_text(p)), CV.MAX_CV_CHARS)


class TestLooksLikeText(unittest.TestCase):
    def test_good_text(self):
        self.assertTrue(CV._looks_like_text(GOOD_CV))

    def test_short_false(self):
        self.assertFalse(CV._looks_like_text("short"))

    def test_low_ratio_false(self):
        self.assertFalse(CV._looks_like_text("@#$%" * 100))


class TestParseCVScore(unittest.TestCase):
    def test_valid(self):
        r = SC.parse_cv_score('{"cv_match_score":80,"gaps":"k8s"}')
        self.assertEqual(r["score"], 80)
        self.assertEqual(r["gaps"], "k8s")

    def test_bad_json_none(self):
        self.assertIsNone(SC.parse_cv_score("nope"))

    def test_non_dict_none(self):
        self.assertIsNone(SC.parse_cv_score("[1]"))

    def test_missing_score_none(self):
        self.assertIsNone(SC.parse_cv_score('{"gaps":"x"}'))

    def test_bool_score_rejected(self):
        # bool is an int subclass; must be rejected, not read as 0/1.
        self.assertIsNone(SC.parse_cv_score('{"cv_match_score":true}'))

    def test_string_score_rejected(self):
        self.assertIsNone(SC.parse_cv_score('{"cv_match_score":"80"}'))

    def test_score_clamped(self):
        self.assertEqual(SC.parse_cv_score('{"cv_match_score":150}')["score"], 100)
        self.assertEqual(SC.parse_cv_score('{"cv_match_score":-3}')["score"], 0)

    def test_gaps_none_becomes_empty(self):
        self.assertEqual(SC.parse_cv_score('{"cv_match_score":90,"gaps":"none"}')["gaps"], "")

    def test_gaps_capped(self):
        r = SC.parse_cv_score('{"cv_match_score":50,"gaps":"%s"}' % ("g" * 500))
        self.assertLessEqual(len(r["gaps"]), 200)


if __name__ == "__main__":
    unittest.main()
