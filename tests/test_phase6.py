"""Phase 6 tests: scheme search (find widths/stories that fit an envelope)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from massing_explorer.load import load_program_file
from massing_explorer.search import (
    SiteEnvelope,
    _mass_options,
    _width_grid,
    apply_scheme,
    search_schemes,
)
from massing_explorer.session import StudySession
from massing_explorer.solver import solve_massing_study
from massing_explorer.tools import (
    apply_scheme as apply_scheme_tool,
    pair_masses,
    search_site_schemes,
    set_grouping,
)

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

CORE = "CORE ACADEMIC"
SPED = "SPECIAL EDUCATION"
HPE = "HEALTH & PHYSICAL EDUCATION"
DINING = "DINING & FOOD SERVICE"
ART = "ART & MUSIC"
MEDIA = "MEDIA CENTER"
ADMIN = "ADMINISTRATION & GUIDANCE"
CUSTODIAL = "CUSTODIAL & MAINTENANCE"
MEDICAL = "MEDICAL"


class TestWidthGrid(unittest.TestCase):
    def test_lower_bound_rounds_up(self) -> None:
        """Rounding a width down lengthens the plate past the cap."""
        widths = _width_grid(94.5236, 100.0)
        self.assertTrue(all(w >= 94.5236 for w in widths), widths)

    def test_upper_bound_rounds_down(self) -> None:
        widths = _width_grid(50.0, 87.4999)
        self.assertTrue(all(w <= 87.4999 for w in widths), widths)

    def test_empty_when_infeasible(self) -> None:
        self.assertEqual(_width_grid(120.0, 80.0), [])

    def test_narrow_range_returns_single(self) -> None:
        widths = _width_grid(80.0, 82.0)
        self.assertEqual(len(widths), 1)
        self.assertGreaterEqual(widths[0], 80.0)


class TestSearch(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        self.program = load_program_file(UNDERWOOD, config_path=CONFIG)

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def _session(self, study_id: str = "search_test") -> StudySession:
        session = StudySession(
            study_id=study_id, program=self.program, config_path=str(CONFIG)
        )
        set_grouping(
            session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED, ART],
                    "story_count": 4,
                },
                {
                    "id": "community",
                    "name": "Community",
                    "departments": [HPE, DINING, MEDIA, ADMIN, MEDICAL, CUSTODIAL],
                    "story_count": 2,
                },
            ],
        )
        session.double_height_rooms.append("Gymnasium")
        session.save()
        return session

    def test_finds_scheme_where_hand_picked_widths_failed(self) -> None:
        """
        70/100 ft by hand needed 324.7 ft. Under a 300 ft cap that fails, and
        the search has to find a scheme that does not.
        """
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=200,
            max_building_width_ft=100,
            max_total_length_ft=300,
            max_stories=5,
        )
        candidates, _ = search_schemes(
            session, envelope, top_n=3, config_path=str(CONFIG)
        )
        self.assertTrue(candidates)
        for cand in candidates:
            self.assertLessEqual(cand.total_length_ft, 300.0 + 1.0)
            self.assertTrue(cand.verified)

    def test_every_verified_candidate_passes_the_real_solver(self) -> None:
        """Search proposes; verify drops layout/dim failures; survivors must pass."""
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=200,
            max_building_width_ft=100,
            max_total_length_ft=300,
            max_stories=5,
        )
        verified, _ = search_schemes(
            session, envelope, top_n=5, config_path=str(CONFIG), verify=True
        )
        self.assertTrue(verified)
        for cand in verified:
            self.assertTrue(cand.verified)
            self.assertEqual(cand.failed_checks, [])

    def test_respects_width_and_length_caps(self) -> None:
        session = self._session()
        # 100 ft width leaves a 40 ft arm beside a 60 ft gym — deep enough for
        # a 40x60 cafeteria on the void floor when dining sits there.
        envelope = SiteEnvelope(
            max_building_length_ft=150,
            max_building_width_ft=100,
            max_total_length_ft=400,
            max_stories=5,
        )
        candidates, _ = search_schemes(
            session, envelope, top_n=10, config_path=str(CONFIG)
        )
        self.assertTrue(candidates)
        for cand in candidates:
            for option in cand.options:
                self.assertLessEqual(option.width_ft, 100.0)
                self.assertLessEqual(option.length_ft, 150.0 + 1e-6)

    def test_story_ceiling_respected(self) -> None:
        session = self._session()
        envelope = SiteEnvelope(max_total_length_ft=600, max_stories=2)
        candidates, _ = search_schemes(
            session, envelope, top_n=5, config_path=str(CONFIG)
        )
        for cand in candidates:
            for option in cand.options:
                self.assertLessEqual(option.stories, 2)

    def test_infeasible_envelope_explains_itself(self) -> None:
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=60,
            max_building_width_ft=60,
            max_total_length_ft=100,
            max_stories=2,
        )
        candidates, notes = search_schemes(
            session, envelope, top_n=3, config_path=str(CONFIG)
        )
        self.assertEqual(candidates, [])
        self.assertTrue(notes)
        self.assertTrue(any("relax" in n.lower() or "overrun" in n.lower() for n in notes))

    def test_frontage_cap_reports_shortest_possible(self) -> None:
        session = self._session()
        # Wide enough per mass, but nowhere near enough total frontage
        envelope = SiteEnvelope(
            max_building_length_ft=300,
            max_building_width_ft=100,
            max_total_length_ft=120,
            max_stories=2,
        )
        candidates, notes = search_schemes(
            session, envelope, top_n=3, config_path=str(CONFIG)
        )
        self.assertEqual(candidates, [])
        self.assertTrue(any("shortest possible" in n for n in notes))

    def test_preference_changes_the_answer(self) -> None:
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=200,
            max_building_width_ft=100,
            max_total_length_ft=300,
            max_stories=5,
        )
        low, _ = search_schemes(
            session, envelope, preference="low_rise", top_n=1, config_path=str(CONFIG)
        )
        compact, _ = search_schemes(
            session, envelope, preference="compact", top_n=1, config_path=str(CONFIG)
        )
        self.assertTrue(low and compact)
        # Low-rise must not be taller than compact, and should use more frontage
        self.assertLess(
            low[0].metrics["mean_stories"], compact[0].metrics["mean_stories"]
        )
        self.assertGreater(low[0].total_length_ft, compact[0].total_length_ft)

    def test_daylight_keeps_classroom_bar_shallow(self) -> None:
        """Without the daylight term the search just maxes out every width."""
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=200,
            max_building_width_ft=120,
            max_total_length_ft=400,
            max_stories=4,
        )
        candidates, _ = search_schemes(
            session, envelope, preference="balanced", top_n=1, config_path=str(CONFIG)
        )
        academic = next(o for o in candidates[0].options if o.mass_id == "academic")
        self.assertLess(academic.width_ft, envelope.max_building_width_ft)
        self.assertLessEqual(academic.width_ft, 95.0)

    def test_score_is_independent_of_candidate_set(self) -> None:
        """Metrics are absolute, so a scheme's score cannot drift with top_n."""
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=200,
            max_building_width_ft=100,
            max_total_length_ft=300,
            max_stories=5,
        )
        few, _ = search_schemes(
            session, envelope, top_n=1, config_path=str(CONFIG), verify=False
        )
        many, _ = search_schemes(
            session, envelope, top_n=50, config_path=str(CONFIG), verify=False
        )
        self.assertAlmostEqual(few[0].score, many[0].score, places=6)

    def test_pairing_fixes_width_and_only_stories_vary(self) -> None:
        session = self._session("search_paired")
        pair_masses(session, ["academic", "community"], 300)
        envelope = SiteEnvelope(
            max_building_length_ft=200,
            max_building_width_ft=120,
            max_total_length_ft=320,
            max_stories=5,
        )
        candidates, _ = search_schemes(
            session, envelope, top_n=5, config_path=str(CONFIG)
        )
        self.assertTrue(candidates)
        for cand in candidates:
            widths = {round(o.width_ft, 4) for o in cand.options}
            self.assertEqual(len(widths), 1, "paired masses must share one width")
            self.assertAlmostEqual(cand.total_length_ft, 300.0, delta=1.0)
            self.assertTrue(cand.verified)

    def test_no_masses_returns_note(self) -> None:
        session = StudySession(
            study_id="empty", program=self.program, config_path=str(CONFIG)
        )
        candidates, notes = search_schemes(session, SiteEnvelope())
        self.assertEqual(candidates, [])
        self.assertTrue(any("groupings" in n for n in notes))

    def test_apply_scheme_writes_stories_and_width(self) -> None:
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=200,
            max_building_width_ft=100,
            max_total_length_ft=300,
            max_stories=5,
        )
        candidates, _ = search_schemes(
            session, envelope, top_n=1, config_path=str(CONFIG)
        )
        chosen = candidates[0]
        apply_scheme(session, chosen)

        for option in chosen.options:
            mass = next(m for m in session.masses if m.id == option.mass_id)
            self.assertEqual(mass.story_count, option.stories)
            self.assertAlmostEqual(
                session.constraints[f"{option.mass_id}_width_ft"],
                option.width_ft,
                places=2,
            )
        # And the applied scheme solves clean
        result = solve_massing_study(session, config_path=str(CONFIG))
        self.assertEqual([v.message for v in result.validation if not v.passed], [])


class TestSilentWidthBugs(unittest.TestCase):
    """
    Regressions from an end-to-end LLM run.

    The model set `community_base_width_ft` for a mass whose id is `community`.
    Nothing rejected the key, and the solver then fell through a global
    `academic_width_ft` fallback, so the community mass was silently built at
    the academic width instead of the requested one.
    """

    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="width_bugs", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic Bar",
                    "departments": [CORE, SPED, ART],
                    "story_count": 4,
                },
                {
                    "id": "community",
                    "name": "Community Base",
                    "departments": [HPE, DINING, MEDIA, ADMIN, MEDICAL, CUSTODIAL],
                    "story_count": 2,
                },
            ],
        )
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_mismatched_width_key_is_rejected(self) -> None:
        from massing_explorer.tools import set_constraint

        out = set_constraint(self.session, "community_base_width_ft", 100)
        self.assertFalse(out["ok"])
        self.assertIn("does not match any mass", out["error"])
        self.assertIn("community_width_ft", out["valid_width_keys"])
        self.assertNotIn("community_base_width_ft", self.session.constraints)

    def test_correct_width_key_is_accepted(self) -> None:
        from massing_explorer.tools import set_constraint

        out = set_constraint(self.session, "community_width_ft", 100)
        self.assertTrue(out["ok"])
        self.assertEqual(self.session.constraints["community_width_ft"], 100)

    def test_global_width_keys_still_allowed(self) -> None:
        from massing_explorer.tools import set_constraint

        for key in ("max_building_width_ft", "fixed_width_ft", "academic_width_ft"):
            self.assertTrue(set_constraint(self.session, key, 90)["ok"], key)

    def test_academic_width_does_not_size_other_masses(self) -> None:
        self.session.constraints["academic_width_ft"] = 70
        self.session.save()
        result = solve_massing_study(self.session, config_path=str(CONFIG))

        academic = next(m for m in result.masses if m.id == "academic")
        community = next(m for m in result.masses if m.id == "community")
        self.assertAlmostEqual(academic.fixed_dim_ft, 70.0, places=3)
        self.assertNotAlmostEqual(
            community.fixed_dim_ft,
            70.0,
            places=3,
            msg="academic_width_ft must not size a non-academic mass",
        )

    def test_solve_dimensions_rejects_arguments(self) -> None:
        from massing_explorer.tools import execute_tool

        out = json.loads(
            execute_tool(self.session, "solve_dimensions", {"widths_ft": {"a": 70}})
        )
        self.assertFalse(out["ok"])
        self.assertIn("takes no arguments", out["error"])
        self.assertIn("set_constraint", out["next_step"])

    def test_solve_dimensions_still_works_without_arguments(self) -> None:
        from massing_explorer.tools import execute_tool

        out = json.loads(execute_tool(self.session, "solve_dimensions", {}))
        self.assertTrue(out["ok"])

    def test_unset_site_limits_are_disclosed(self) -> None:
        """
        The model told the user a 324.7 ft scheme cleared their 300 ft frontage
        because no frontage limit had been recorded, so nothing checked it.
        """
        from massing_explorer.tools import solve_dimensions

        self.session.constraints["academic_width_ft"] = 70
        self.session.constraints["community_width_ft"] = 100
        out = solve_dimensions(self.session)

        self.assertTrue(out["all_checks_passed"])
        self.assertIn("max_total_length_ft", out["site_limits_not_set"])
        self.assertIsNotNone(out["caution"])
        self.assertAlmostEqual(out["total_ground_length_ft"], 324.7, delta=0.5)

    def test_set_site_limit_turns_into_a_real_failure(self) -> None:
        from massing_explorer.tools import solve_dimensions

        self.session.constraints.update(
            {
                "academic_width_ft": 70,
                "community_width_ft": 100,
                "max_total_length_ft": 300,
            }
        )
        out = solve_dimensions(self.session)

        self.assertIn("max_total_length_ft", out["site_limits_active"])
        self.assertFalse(out["all_checks_passed"])
        self.assertTrue(any("300" in f for f in out["failed_checks"]))


class TestSearchTools(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="tools_search", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED, ART],
                    "story_count": 4,
                },
                {
                    "id": "community",
                    "name": "Community",
                    "departments": [HPE, DINING, MEDIA, ADMIN, MEDICAL, CUSTODIAL],
                    "story_count": 2,
                },
            ],
        )
        self.session.double_height_rooms.append("Gymnasium")
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_tool_returns_indexed_schemes(self) -> None:
        out = search_site_schemes(
            self.session,
            max_total_length_ft=300,
            max_length_ft=200,
            max_width_ft=100,
            max_stories=5,
        )
        self.assertTrue(out["ok"])
        self.assertGreater(out["found"], 0)
        self.assertEqual([s["index"] for s in out["schemes"]], list(range(out["found"])))
        self.assertTrue(all(s["verified"] for s in out["schemes"]))
        self.assertEqual(out["limits_not_checked"], [])
        self.assertIsNone(out["caution"])

    def test_unpassed_limits_are_flagged(self) -> None:
        """
        The model once reported 400 ft schemes as fitting a 300 ft site it never
        passed in, so the tool has to say which limits it did not check.
        """
        out = search_site_schemes(self.session, max_width_ft=100, max_stories=3)
        self.assertTrue(out["ok"])
        self.assertIn("max_total_length_ft", out["limits_not_checked"])
        self.assertIn("max_length_ft", out["limits_not_checked"])
        self.assertEqual(out["limits_applied"], {"max_width_ft": 100})
        self.assertIn("NOT applied", out["caution"])

    def test_tool_rejects_bad_preference(self) -> None:
        out = search_site_schemes(self.session, preference="tall_and_weird")
        self.assertFalse(out["ok"])
        self.assertIn("valid_preferences", out)

    def test_apply_tool_resolves_and_persists(self) -> None:
        search_site_schemes(
            self.session,
            max_total_length_ft=300,
            max_length_ft=200,
            max_width_ft=100,
            max_stories=5,
        )
        out = apply_scheme_tool(self.session, 0)
        self.assertTrue(out["ok"])
        self.assertTrue(out["all_checks_passed"], out.get("failed_checks"))
        self.assertTrue(out["changes"])

        reloaded = StudySession.load("tools_search")
        self.assertTrue(reloaded.last_search)
        self.assertTrue(reloaded.last_massing)

    def test_apply_tool_needs_a_search_first(self) -> None:
        out = apply_scheme_tool(self.session, 0)
        self.assertFalse(out["ok"])
        self.assertIn("search_site_schemes", out["error"])

    def test_apply_tool_rejects_bad_index(self) -> None:
        search_site_schemes(
            self.session, max_total_length_ft=300, max_length_ft=200, max_width_ft=100
        )
        out = apply_scheme_tool(self.session, 99)
        self.assertFalse(out["ok"])
        self.assertIn("index must be", out["error"])


if __name__ == "__main__":
    unittest.main()
