"""
Stepped / terraced floor plates - step 9 of the manual workflow.

Until now every floor of a mass had the same plate: `solve_stepped_floors`
existed in the solver but had no call sites, so unequal plates were unreachable.
These tests cover the real path.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from massing_explorer.load import load_program_file
from massing_explorer.search import SiteEnvelope, search_schemes
from massing_explorer.session import StudySession
from massing_explorer.solver import (
    check_step_geometry,
    plate_area,
    resolve_step_weights,
    solve_mass_footprint,
    solve_massing_study,
    solve_stepped_plates,
)
from massing_explorer.tools import (
    clear_floor_steps,
    execute_tool,
    mark_double_height,
    pair_masses,
    set_floor_steps,
    set_floor_taper,
    set_grouping,
)

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

CORE = "CORE ACADEMIC"
SPED = "SPECIAL EDUCATION"
ART = "ART & MUSIC"
HPE = "HEALTH & PHYSICAL EDUCATION"
DINING = "DINING & FOOD SERVICE"
MEDIA = "MEDIA CENTER"
ADMIN = "ADMINISTRATION & GUIDANCE"
CUSTODIAL = "CUSTODIAL & MAINTENANCE"
MEDICAL = "MEDICAL"


class TestStepMath(unittest.TestCase):
    def test_uniform_weights_match_plate_area(self) -> None:
        """The stepped path must reduce exactly to the flat case."""
        for stories in (1, 2, 3, 4):
            for void in (0.0, 6000.0):
                plates = solve_stepped_plates(24000, [1.0] * stories, void)
                self.assertAlmostEqual(
                    plates[0], plate_area(24000, stories, void), places=6
                )
                self.assertEqual(len(plates), stories)

    def test_weights_set_relative_plate_sizes(self) -> None:
        plates = solve_stepped_plates(30000, [1.0, 0.5, 0.25])
        self.assertAlmostEqual(plates[1] / plates[0], 0.5, places=6)
        self.assertAlmostEqual(plates[2] / plates[0], 0.25, places=6)

    def test_area_is_conserved(self) -> None:
        plates = solve_stepped_plates(30000, [1.0, 0.8, 0.4])
        self.assertAlmostEqual(sum(plates), 30000, places=6)

    def test_area_is_conserved_with_a_void(self) -> None:
        """Sum of plates less the single void cut must equal the target."""
        plates = solve_stepped_plates(30000, [1.0, 0.8, 0.4], 6000)
        self.assertAlmostEqual(sum(plates) - 6000, 30000, places=6)

    def test_single_story_ignores_void(self) -> None:
        """One storey has no floor above, so there is nothing to cut."""
        plates = solve_stepped_plates(12000, [1.0], 6000)
        self.assertAlmostEqual(plates[0], 12000, places=6)

    def test_rejects_non_positive_weights(self) -> None:
        with self.assertRaises(ValueError):
            solve_stepped_plates(10000, [1.0, 0.0])
        with self.assertRaises(ValueError):
            solve_stepped_plates(10000, [1.0, -0.5])

    def test_empty_weights(self) -> None:
        self.assertEqual(solve_stepped_plates(10000, []), [])

    def test_footprint_lengths_follow_weights_at_constant_width(self) -> None:
        floors, actual = solve_mass_footprint(
            target_gsf=24000,
            story_count=3,
            fixed_width_ft=80,
            weights=[1.0, 0.5, 0.5],
        )
        self.assertAlmostEqual(actual, 24000, places=6)
        self.assertTrue(all(f.width_ft == 80 for f in floors))
        self.assertAlmostEqual(floors[1].length_ft / floors[0].length_ft, 0.5, places=6)


class TestStepWeightResolution(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="weights", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED, ART],
                    "story_count": 3,
                }
            ],
        )
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_default_is_uniform(self) -> None:
        self.assertEqual(resolve_step_weights(self.session, "academic", 3), [1.0] * 3)

    def test_taper_is_geometric(self) -> None:
        set_floor_taper(self.session, "academic", 0.8)
        weights = resolve_step_weights(self.session, "academic", 3)
        self.assertAlmostEqual(weights[1], 0.8, places=6)
        self.assertAlmostEqual(weights[2], 0.64, places=6)

    def test_taper_survives_a_story_count_change(self) -> None:
        set_floor_taper(self.session, "academic", 0.5)
        self.assertEqual(len(resolve_step_weights(self.session, "academic", 5)), 5)
        self.assertEqual(len(resolve_step_weights(self.session, "academic", 2)), 2)

    def test_explicit_weights_win_over_taper(self) -> None:
        set_floor_taper(self.session, "academic", 0.8)
        set_floor_steps(self.session, "academic", [1.0, 0.3, 0.3])
        self.assertEqual(
            resolve_step_weights(self.session, "academic", 3), [1.0, 0.3, 0.3]
        )
        self.assertNotIn("academic", self.session.floor_tapers)

    def test_explicit_weights_padded_and_trimmed(self) -> None:
        set_floor_steps(self.session, "academic", [1.0, 0.5])
        self.assertEqual(
            resolve_step_weights(self.session, "academic", 4), [1.0, 0.5, 0.5, 0.5]
        )
        self.assertEqual(resolve_step_weights(self.session, "academic", 1), [1.0])

    def test_clear_restores_uniform(self) -> None:
        set_floor_taper(self.session, "academic", 0.7)
        clear_floor_steps(self.session, "academic")
        self.assertEqual(resolve_step_weights(self.session, "academic", 3), [1.0] * 3)

    def test_persists_across_reload(self) -> None:
        set_floor_steps(self.session, "academic", [1.0, 0.6, 0.3])
        reloaded = StudySession.load("weights")
        self.assertEqual(reloaded.floor_steps["academic"], [1.0, 0.6, 0.3])
        set_floor_taper(reloaded, "academic", 0.9)
        again = StudySession.load("weights")
        self.assertAlmostEqual(again.floor_tapers["academic"], 0.9)
        self.assertEqual(again.floor_steps, {})


class TestSteppedSolve(unittest.TestCase):
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

    def _academic(self, stories: int = 3) -> StudySession:
        session = StudySession(
            study_id="stepped", program=self.program, config_path=str(CONFIG)
        )
        set_grouping(
            session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED, ART],
                    "story_count": stories,
                }
            ],
        )
        session.constraints["academic_width_ft"] = 80
        session.save()
        return session

    def _athletics(self, stories: int = 3) -> StudySession:
        session = StudySession(
            study_id="stepped_void", program=self.program, config_path=str(CONFIG)
        )
        set_grouping(
            session,
            [
                {
                    "id": "athletics",
                    "name": "Athletics",
                    "departments": [HPE, DINING],
                    "story_count": stories,
                }
            ],
        )
        session.constraints["athletics_width_ft"] = 100
        mark_double_height(session, "Gymnasium")
        session.save()
        return session

    def test_taper_conserves_target_gsf(self) -> None:
        session = self._academic()
        set_floor_taper(session, "academic", 0.75)
        mass = solve_massing_study(session, config_path=str(CONFIG)).masses[0]
        self.assertAlmostEqual(mass.actual_gsf, mass.target_gsf, delta=1.0)
        self.assertTrue(mass.fit_pass)

    def test_taper_produces_the_requested_ratios(self) -> None:
        session = self._academic()
        set_floor_taper(session, "academic", 0.75)
        mass = solve_massing_study(session, config_path=str(CONFIG)).masses[0]
        self.assertTrue(mass.is_stepped)
        self.assertAlmostEqual(mass.step_ratios[1], 0.75, places=3)
        self.assertAlmostEqual(mass.step_ratios[2], 0.5625, places=3)

    def test_width_held_constant_while_length_steps(self) -> None:
        session = self._academic()
        set_floor_steps(session, "academic", [1.0, 0.8, 0.4])
        mass = solve_massing_study(session, config_path=str(CONFIG)).masses[0]
        self.assertEqual(len({round(f.width_ft, 4) for f in mass.floors}), 1)
        lengths = [f.length_ft for f in mass.floors]
        self.assertGreater(lengths[0], lengths[1])
        self.assertGreater(lengths[1], lengths[2])

    def test_uniform_mass_is_not_flagged_stepped(self) -> None:
        mass = solve_massing_study(self._academic(), config_path=str(CONFIG)).masses[0]
        self.assertFalse(mass.is_stepped)
        self.assertEqual(check_step_geometry(mass), [])

    def test_allocation_still_conserves_over_unequal_floors(self) -> None:
        session = self._academic()
        set_floor_taper(session, "academic", 0.7)
        result = solve_massing_study(session, config_path=str(CONFIG))
        mass = result.masses[0]

        allocated = sum(f.allocated_gsf for f in mass.floors)
        self.assertAlmostEqual(allocated, mass.target_gsf, delta=2.0)
        for floor in mass.floors:
            self.assertLessEqual(floor.allocated_gsf, floor.usable_area_sf + 1.0)

        failures = [
            v.message
            for v in result.validation
            if not v.passed and v.check.startswith("allocation")
        ]
        self.assertEqual(failures, [])

    def test_step_back_smaller_than_void_fails_with_guidance(self) -> None:
        """
        A hard set-back can leave the floor above a double-height room smaller
        than the void itself, which would silently clamp usable area to zero.
        """
        session = self._athletics()
        set_floor_taper(session, "athletics", 0.3)
        result = solve_massing_study(session, config_path=str(CONFIG))

        failures = [v for v in result.validation if v.check.startswith("step_void")]
        self.assertTrue(failures)
        self.assertFalse(failures[0].passed)
        self.assertIn("smaller than", failures[0].message)

    def test_gentle_step_back_over_a_void_is_clean(self) -> None:
        session = self._athletics()
        set_floor_taper(session, "athletics", 0.85)
        result = solve_massing_study(session, config_path=str(CONFIG))
        mass = result.masses[0]

        # Only this mass is grouped, so ignore the unassigned-department check
        relevant = [
            v.message
            for v in result.validation
            if not v.passed and not v.check.startswith("unassigned")
        ]
        self.assertEqual(relevant, [])
        self.assertAlmostEqual(mass.actual_gsf, mass.target_gsf, delta=1.0)

    def test_cantilever_is_reported_as_a_note(self) -> None:
        session = self._academic()
        set_floor_steps(session, "academic", [1.0, 1.3, 1.6])
        result = solve_massing_study(session, config_path=str(CONFIG))

        notes = [v for v in result.validation if v.check.startswith("step_cantilever")]
        self.assertEqual(len(notes), 2)
        self.assertTrue(all(n.passed for n in notes))
        self.assertIn("cantilever", notes[0].message)

    def test_length_check_uses_the_longest_floor(self) -> None:
        """With a cantilever the worst floor is not the ground floor."""
        session = self._academic()
        set_floor_steps(session, "academic", [1.0, 1.0, 2.0])
        session.constraints["max_building_length_ft"] = 150
        session.save()
        result = solve_massing_study(session, config_path=str(CONFIG))

        check = next(v for v in result.validation if v.check.startswith("site_length"))
        self.assertFalse(check.passed)
        self.assertIn("L2", check.message)

    def test_pairing_divides_frontage_by_ground_plates(self) -> None:
        session = StudySession(
            study_id="stepped_pair", program=self.program, config_path=str(CONFIG)
        )
        set_grouping(
            session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED],
                    "story_count": 3,
                },
                {
                    "id": "support",
                    "name": "Support",
                    "departments": [ADMIN, MEDIA, MEDICAL, ART, CUSTODIAL],
                    "story_count": 2,
                },
            ],
        )
        set_floor_taper(session, "academic", 0.8)
        pair_masses(session, ["academic", "support"], 300)
        result = solve_massing_study(session, config_path=str(CONFIG))

        widths = {round(m.fixed_dim_ft, 4) for m in result.masses}
        self.assertEqual(len(widths), 1, "paired masses must still share a width")
        ground = sum(m.floors[0].length_ft for m in result.masses)
        self.assertAlmostEqual(ground, 300.0, delta=1.0)


class TestSteppedSearch(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="stepped_search", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED, ART],
                    "story_count": 3,
                },
                {
                    "id": "community",
                    "name": "Community",
                    "departments": [HPE, DINING, MEDIA, ADMIN, MEDICAL, CUSTODIAL],
                    "story_count": 2,
                },
            ],
        )
        mark_double_height(self.session, "Gymnasium")
        self.session.save()
        self.envelope = SiteEnvelope(
            max_building_length_ft=220,
            max_building_width_ft=100,
            max_total_length_ft=400,
            max_stories=5,
        )

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_tapered_mass_still_searches_over_story_counts(self) -> None:
        set_floor_taper(self.session, "academic", 0.8)
        candidates, _ = search_schemes(
            self.session, self.envelope, top_n=25, config_path=str(CONFIG), verify=False
        )
        heights = {
            o.stories for c in candidates for o in c.options if o.mass_id == "academic"
        }
        self.assertGreater(len(heights), 1, "a taper must not pin the height")
        self.assertTrue(
            all(
                o.stepped
                for c in candidates
                for o in c.options
                if o.mass_id == "academic"
            )
        )

    def test_explicit_weights_pin_the_story_count(self) -> None:
        set_floor_steps(self.session, "academic", [1.0, 0.8, 0.5])
        candidates, _ = search_schemes(
            self.session, self.envelope, top_n=25, config_path=str(CONFIG), verify=False
        )
        heights = {
            o.stories for c in candidates for o in c.options if o.mass_id == "academic"
        }
        self.assertEqual(heights, {3})

    def test_stepped_candidates_all_pass_the_solver(self) -> None:
        """Verified stepped schemes must pass the solver (raw may be pruned)."""
        set_floor_taper(self.session, "academic", 0.8)
        set_floor_taper(self.session, "community", 0.9)
        candidates, _ = search_schemes(
            self.session, self.envelope, top_n=5, config_path=str(CONFIG)
        )
        self.assertTrue(candidates)
        for cand in candidates:
            self.assertTrue(cand.verified, cand.failed_checks)
            self.assertEqual(cand.failed_checks, [])

    def test_search_respects_length_cap_on_the_stepped_ground_floor(self) -> None:
        """A step-back enlarges the ground plate, so the cap binds harder."""
        set_floor_taper(self.session, "academic", 0.6)
        candidates, _ = search_schemes(
            self.session, self.envelope, top_n=30, config_path=str(CONFIG), verify=False
        )
        self.assertTrue(candidates)
        for cand in candidates:
            for option in cand.options:
                self.assertLessEqual(option.length_ft, 220.0 + 1e-6)


class TestStepTools(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="step_tools", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED, ART],
                    "story_count": 3,
                }
            ],
        )
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_rejects_unknown_mass(self) -> None:
        out = set_floor_steps(self.session, "nope", [1.0, 0.5])
        self.assertFalse(out["ok"])
        self.assertIn("academic", out["error"])

    def test_rejects_empty_weights(self) -> None:
        self.assertFalse(set_floor_steps(self.session, "academic", [])["ok"])

    def test_rejects_zero_weight(self) -> None:
        out = set_floor_steps(self.session, "academic", [1.0, 0.0])
        self.assertFalse(out["ok"])
        self.assertIn("> 0", out["error"])

    def test_notes_a_story_count_mismatch(self) -> None:
        out = set_floor_steps(self.session, "academic", [1.0, 0.5])
        self.assertTrue(out["ok"])
        self.assertIsNotNone(out["note"])
        self.assertIn("3 stories", out["note"])

    def test_no_note_when_counts_match(self) -> None:
        out = set_floor_steps(self.session, "academic", [1.0, 0.8, 0.5])
        self.assertIsNone(out["note"])

    def test_taper_range_is_enforced(self) -> None:
        for bad in (0.0, 0.1, 1.5, -0.5):
            out = set_floor_taper(self.session, "academic", bad)
            self.assertFalse(out["ok"], bad)
            self.assertIn("between", out["error"])
        self.assertTrue(set_floor_taper(self.session, "academic", 0.8)["ok"])

    def test_clear_all(self) -> None:
        set_floor_taper(self.session, "academic", 0.8)
        out = clear_floor_steps(self.session)
        self.assertEqual(out["stepped_masses"], [])

    def test_dispatch_through_execute_tool(self) -> None:
        out = json.loads(
            execute_tool(
                self.session, "set_floor_taper", {"mass_id": "academic", "ratio": 0.75}
            )
        )
        self.assertTrue(out["ok"])
        out = json.loads(
            execute_tool(
                self.session,
                "set_floor_steps",
                {"mass_id": "academic", "weights": [1, 0.6, 0.4]},
            )
        )
        self.assertTrue(out["ok"])
        out = json.loads(
            execute_tool(self.session, "clear_floor_steps", {"mass_id": "academic"})
        )
        self.assertTrue(out["ok"])
        self.assertEqual(resolve_step_weights(self.session, "academic", 3), [1.0] * 3)


if __name__ == "__main__":
    unittest.main()
