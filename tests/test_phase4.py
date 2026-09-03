"""Phase 4 tests: paired masses, site limits, resize loop."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from massing_explorer.load import load_program_file
from massing_explorer.massing_models import FloorPlate, SolvedMass
from massing_explorer.report import format_massing_report
from massing_explorer.session import StudySession
from massing_explorer.solver import (
    check_site_limits,
    plate_area,
    solve_massing_study,
    solve_paired_masses,
    solve_paired_width,
)
from massing_explorer.tools import (
    clear_pairings,
    pair_masses,
    resize_mass,
    set_grouping,
    solve_dimensions,
)
from massing_explorer.visual import render_site_plan

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"


def _ground(width: float, length: float) -> SolvedMass:
    return SolvedMass(
        id="m1",
        name="Mass 1",
        departments=[],
        floors=[
            FloorPlate(
                level=0,
                width_ft=width,
                length_ft=length,
                area_sf=width * length,
                programs=[],
            )
        ],
        target_gsf=width * length,
        actual_gsf=width * length,
        fit_delta_sf=0.0,
        fit_pass=True,
        fixed_dim_ft=width,
    )


class TestPairedSolver(unittest.TestCase):
    def test_paired_width_and_lengths(self) -> None:
        # Manual workflow: 12,000 + 8,000 SF plates in 280 ft total
        width, lengths = solve_paired_masses([12000, 8000], 280)
        self.assertAlmostEqual(width, 20000 / 280, places=6)
        self.assertAlmostEqual(width, 71.428571, places=5)
        self.assertAlmostEqual(lengths[0], 168.0, places=4)
        self.assertAlmostEqual(lengths[1], 112.0, places=4)
        # Lengths must sum back to the stated total
        self.assertAlmostEqual(sum(lengths), 280.0, places=4)

    def test_paired_areas_preserved(self) -> None:
        width, lengths = solve_paired_masses([12000, 8000], 280)
        self.assertAlmostEqual(width * lengths[0], 12000, delta=0.01)
        self.assertAlmostEqual(width * lengths[1], 8000, delta=0.01)

    def test_three_masses(self) -> None:
        width, lengths = solve_paired_masses([9000, 6000, 3000], 300)
        self.assertAlmostEqual(width, 60.0, places=6)
        self.assertAlmostEqual(sum(lengths), 300.0, places=4)

    def test_invalid_total_length(self) -> None:
        with self.assertRaises(ValueError):
            solve_paired_width([1000], 0)

    def test_plate_area_with_void(self) -> None:
        self.assertAlmostEqual(plate_area(20000, 1), 20000)
        self.assertAlmostEqual(plate_area(20000, 2), 10000)
        self.assertAlmostEqual(plate_area(20000, 2, 6000), 13000)


class TestSiteLimits(unittest.TestCase):
    def test_length_over_limit_suggests_resize(self) -> None:
        mass = _ground(80, 300)  # 24,000 SF plate
        checks, suggestions = check_site_limits(
            mass, {"max_building_length_ft": 200}, target_gsf=24000
        )
        self.assertFalse(checks[0].passed)
        self.assertEqual(len(suggestions), 1)
        s = suggestions[0]
        # 24,000 SF footprint / (80 x 200 cap) = 1.5 -> 2 stories
        self.assertEqual(s.option_stories, 2)
        # or widen so the 24,000 SF plate fits in 200 ft
        self.assertAlmostEqual(s.option_width_ft, 120.0, places=4)

    def test_story_suggestion_always_adds_a_floor(self) -> None:
        # A 2-story mass barely over the limit must be told to go to 3, not 2
        mass = SolvedMass(
            id="hpe",
            name="HPE",
            departments=[],
            floors=[
                FloorPlate(level=0, width_ft=100, length_ft=132, area_sf=13200, programs=[]),
                FloorPlate(level=1, width_ft=100, length_ft=132, area_sf=13200, programs=[]),
            ],
            target_gsf=20400,
            actual_gsf=20400,
            fit_delta_sf=0.0,
            fit_pass=True,
            fixed_dim_ft=100,
        )
        _, suggestions = check_site_limits(
            mass, {"max_building_length_ft": 130}, target_gsf=20400
        )
        self.assertEqual(suggestions[0].option_stories, 3)

    def test_length_within_limit_no_suggestion(self) -> None:
        mass = _ground(80, 150)
        checks, suggestions = check_site_limits(
            mass, {"max_building_length_ft": 200}, target_gsf=12000
        )
        self.assertTrue(checks[0].passed)
        self.assertEqual(suggestions, [])

    def test_width_over_limit(self) -> None:
        mass = _ground(140, 100)  # 14,000 SF
        checks, suggestions = check_site_limits(
            mass, {"max_building_width_ft": 100}, target_gsf=14000
        )
        self.assertFalse(checks[0].passed)
        self.assertAlmostEqual(suggestions[0].option_width_ft, 100.0)

    def test_no_limits_no_checks(self) -> None:
        checks, suggestions = check_site_limits(_ground(80, 150), {}, 12000)
        self.assertEqual(checks, [])
        self.assertEqual(suggestions, [])


class TestSessionIntegration(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"

        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="phase4_demo",
            program=program,
            config_path=str(CONFIG),
        )
        depts = self.session.department_names()
        academic = [d for d in depts if "ACADEMIC" in d or "SPECIAL" in d]
        rest = [d for d in depts if d not in academic]
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic Wing",
                    "departments": academic,
                    "story_count": 3,
                },
                {
                    "id": "support",
                    "name": "Support Wing",
                    "departments": rest,
                    "story_count": 2,
                },
            ],
        )

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_pair_masses_shares_width(self) -> None:
        out = pair_masses(self.session, ["academic", "support"], 280)
        self.assertTrue(out["ok"])
        shared = out["shared_width_ft"]

        result = solve_massing_study(self.session, config_path=str(CONFIG))
        widths = {m.id: m.fixed_dim_ft for m in result.masses}
        self.assertAlmostEqual(widths["academic"], widths["support"], places=4)
        self.assertAlmostEqual(widths["academic"], shared, delta=0.1)

        combined = sum(m.floors[0].length_ft for m in result.masses)
        self.assertAlmostEqual(combined, 280.0, delta=1.0)

        for m in result.masses:
            self.assertEqual(m.pairing_id, "academic+support")
            self.assertTrue(m.fit_pass, f"{m.id} GSF fit failed")

        check = next(
            v for v in result.validation if v.check == "pairing_length:academic+support"
        )
        self.assertTrue(check.passed)
        self.assertIn("Paired: academic+support", format_massing_report(result))

    def test_unknown_mass_id_rejected(self) -> None:
        out = pair_masses(self.session, ["academic", "nope"], 280)
        self.assertFalse(out["ok"])
        self.assertIn("nope", out["error"])
        self.assertEqual(self.session.pairings, [])

    def test_clear_pairings_restores_fixed_width(self) -> None:
        pair_masses(self.session, ["academic", "support"], 280)
        self.session.constraints["academic_width_ft"] = 80
        clear_pairings(self.session)
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        self.assertAlmostEqual(
            next(m for m in result.masses if m.id == "academic").fixed_dim_ft, 80
        )

    def test_tight_pairing_triggers_resize_suggestion(self) -> None:
        # Force a long, thin pairing so per-mass length blows the limit
        self.session.constraints["max_building_length_ft"] = 120
        pair_masses(self.session, ["academic", "support"], 600)
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        self.assertTrue(result.resize_suggestions)
        report = format_massing_report(result)
        self.assertIn("RESIZE SUGGESTIONS", report)

    def test_resize_width_scales_length_inversely(self) -> None:
        self.session.constraints["academic_width_ft"] = 80
        first = solve_massing_study(self.session, config_path=str(CONFIG))
        before = next(m for m in first.masses if m.id == "academic")

        out = resize_mass(self.session, "academic", width_ft=160)
        self.assertTrue(out["ok"])
        self.assertIn("width -> 160 ft", out["changes"])

        after = next(m for m in out["masses"] if m["id"] == "academic")
        self.assertAlmostEqual(after["width_ft"], 160)
        # Same GSF and stories, double the width -> half the length
        self.assertAlmostEqual(after["target_gsf"], before.target_gsf, delta=1)
        self.assertEqual(after["stories"], len(before.floors))
        self.assertAlmostEqual(
            after["length_ft"], before.floors[0].length_ft / 2, delta=0.5
        )
        self.assertTrue(after["gsf_fit_pass"])

    def test_resize_stories_repartitions_gsf(self) -> None:
        self.session.constraints["academic_width_ft"] = 80
        first = solve_massing_study(self.session, config_path=str(CONFIG))
        before = next(m for m in first.masses if m.id == "academic")
        self.assertEqual(len(before.floors), 3)

        out = resize_mass(self.session, "academic", story_count=2)
        after = next(m for m in out["masses"] if m["id"] == "academic")
        self.assertEqual(after["stories"], 2)
        # Same width and GSF over fewer floors -> longer plate
        self.assertAlmostEqual(after["width_ft"], 80)
        self.assertGreater(after["length_ft"], before.floors[0].length_ft)
        self.assertAlmostEqual(after["actual_gsf"], before.actual_gsf, delta=1)
        self.assertTrue(after["gsf_fit_pass"])

    def test_resize_mass_requires_a_change(self) -> None:
        out = resize_mass(self.session, "academic")
        self.assertFalse(out["ok"])
        out = resize_mass(self.session, "ghost", width_ft=90)
        self.assertFalse(out["ok"])

    def test_pairings_survive_save_load(self) -> None:
        pair_masses(self.session, ["academic", "support"], 280)
        self.session.save()
        reloaded = StudySession.load("phase4_demo")
        self.assertEqual(len(reloaded.pairings), 1)
        self.assertEqual(reloaded.pairings[0].mass_ids, ["academic", "support"])
        self.assertAlmostEqual(reloaded.pairings[0].total_length_ft, 280)

    def test_solve_tool_surfaces_failures(self) -> None:
        """The LLM must not be able to miss a failed check."""
        clean = solve_dimensions(self.session)
        self.assertTrue(clean["all_checks_passed"])
        self.assertEqual(clean["failed_checks"], [])
        # No nested per-mass floor dump to cherry-pick from
        self.assertNotIn("massing", clean)

        self.session.constraints["max_building_length_ft"] = 100
        failing = solve_dimensions(self.session)
        self.assertFalse(failing["all_checks_passed"])
        self.assertTrue(failing["failed_checks"])
        self.assertTrue(any("length" in m for m in failing["failed_checks"]))
        self.assertTrue(failing["resize_suggestions"])
        self.assertIn("verbatim", failing["instruction"])

    def test_site_plan_visual(self) -> None:
        pair_masses(self.session, ["academic", "support"], 280)
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        png = Path(self.tmp.name) / "site.png"
        render_site_plan(result, png, 280)
        self.assertTrue(png.exists())
        self.assertGreater(png.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
