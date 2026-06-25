"""Onboarding pure logic: RAM->model recommendation and deterministic
profile.md / config.json rendering (no TTY)."""

import unittest

import pathsetup  # noqa: F401
import models as M
import profile_template as PT


class TestRecommend(unittest.TestCase):
    def test_unknown_ram_falls_back_to_qwen(self):
        self.assertEqual(M.recommend(None), M.QWEN)

    def test_low_ram_qwen(self):
        self.assertEqual(M.recommend(8), M.QWEN)

    def test_exactly_16_is_qwen(self):
        # Gemma only when STRICTLY greater than the floor.
        self.assertEqual(M.recommend(16), M.QWEN)

    def test_above_floor_gemma(self):
        self.assertEqual(M.recommend(17), M.GEMMA)
        self.assertEqual(M.recommend(32), M.GEMMA)

    def test_tags_are_pinned(self):
        self.assertEqual(M.QWEN.tag, "qwen3.5:9b-mlx")
        self.assertEqual(M.GEMMA.tag, "gemma4:26b-a4b-it-qat")


class TestRenderProfile(unittest.TestCase):
    def _answers(self, **kw):
        a = PT.Answers()
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_remote_only_adds_onsite_blocker(self):
        md = PT.render_profile(self._answers(remote_preference="remote-only"))
        self.assertIn("On-site or hybrid roles", md)

    def test_hybrid_no_onsite_blocker(self):
        md = PT.render_profile(self._answers(remote_preference="hybrid-ok"))
        self.assertNotIn("On-site or hybrid roles", md)

    def test_work_auth_and_excludes_listed(self):
        md = PT.render_profile(self._answers(
            work_auth="EU work rights only",
            exclude_companies=["BadCo"],
            avoid_industries=["adtech"]))
        self.assertIn("EU work rights only", md)
        self.assertIn("BadCo", md)
        self.assertIn("adtech", md)

    def test_city_country_compose_location(self):
        md = PT.render_profile(self._answers(country="Germany", city="Berlin"))
        self.assertIn("Berlin, Germany", md)

    def test_extra_countries_listed_in_search_area(self):
        # The judge must see every searched country, or it down-ranks them as
        # "wrong location". Stripping is applied.
        md = PT.render_profile(self._answers(
            country="United States", extra_countries=["Mexico", " Canada "]))
        self.assertIn("also searching Mexico, Canada", md)

    def test_empty_lists_render_placeholder(self):
        md = PT.render_profile(self._answers())
        self.assertIn("_(none given)_", md)


class TestBuildConfig(unittest.TestCase):
    def test_shape_and_stripping(self):
        a = PT.Answers(search_terms=[" backend ", "", "platform"],
                       country=" Germany ", city=" Berlin ",
                       remote_preference="remote-only", seniority=" senior ")
        cfg = PT.build_config(a, "qwen3.5:9b-mlx", "cv.md")
        self.assertEqual(cfg["model"], "qwen3.5:9b-mlx")
        self.assertEqual(cfg["search"]["queries"], ["backend", "platform"])
        self.assertEqual(cfg["search"]["country"], "Germany")
        self.assertEqual(cfg["cv_path"], "cv.md")
        self.assertEqual(cfg["dashboard_port"], PT.DEFAULT_DASHBOARD_PORT)
        self.assertEqual(cfg["extra_rss"], [])
        self.assertEqual(cfg["extra_jobspy_locations"], [])

    def test_extra_countries_map_to_locations(self):
        a = PT.Answers(search_terms=["x"], extra_countries=[" Mexico ", "", "Canada"])
        cfg = PT.build_config(a, "m", None)
        self.assertEqual(cfg["extra_jobspy_locations"], ["Mexico", "Canada"])

    def test_cv_path_none(self):
        cfg = PT.build_config(PT.Answers(search_terms=["x"]), "m", None)
        self.assertIsNone(cfg["cv_path"])

    def test_no_ntfy_key_when_omitted(self):
        cfg = PT.build_config(PT.Answers(search_terms=["x"]), "m", None)
        self.assertNotIn("ntfy", cfg)

    def test_ntfy_block_included_when_given(self):
        block = {"enabled": True, "server": "https://ntfy.sh", "topic": "jobscout-abc"}
        cfg = PT.build_config(PT.Answers(search_terms=["x"]), "m", None, block)
        self.assertEqual(cfg["ntfy"], block)


if __name__ == "__main__":
    unittest.main()
