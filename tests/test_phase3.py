"""Phase 3 tests: footprint solver, voids, anchor fit, GSF tolerance."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession
from massing_explorer.solver import (
    check_anchor_fit,
    gsf_fit_pass,
    rectangle_fits_room,
    solve_mass_footprint,
    solve_massing_study,
    solve_other_side,
)
from massing_explorer.massing_models import FloorPlate, SolvedMass, VoidRegion
from massing_explorer.tools import set_grouping, mark_double_height
from massing_explorer.report import format_massing_report
from massing_explorer.visual import render_massing_visual

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"


class TestPhase3(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_fixed_width_academic(self) -> None:
        # 24,000 GSF / 3 stories = 8,000 plate; 80 ft wide → L = 100
        length = solve_other_side(8000, 80)
        self.assertAlmostEqual(length, 100.0, places=5)
        floors, actual = solve_mass_footprint(24000, 3, 80)
        self.assertEqual(len(floors), 3)
        self.assertAlmostEqual(floors[0].width_ft, 80)
        self.assertAlmostEqual(floors[0].length_ft, 100)
        self.assertAlmostEqual(actual, 24000, delta=1)

    def test_gym_fit(self) -> None:
        self.assertFalse(rectangle_fits_room(72, 90, 60, 100))
        self.assertTrue(rectangle_fits_room(100, 120, 60, 100))
        self.assertTrue(rectangle_fits_room(100, 60, 60, 100))  # rotation

    def test_double_height_void(self) -> None:
        voids = [VoidRegion("Gymnasium", 60, 100, 6000)]
        floors, actual = solve_mass_footprint(20000, 2, 100, voids=voids)
        self.assertEqual(len(floors), 2)
        self.assertTrue(floors[1].voids)
        # Plate = (20000 + 6000) / 2 = 13000; usable = 13000 + (13000-6000) = 20000
        self.assertAlmostEqual(floors[0].usable_area_sf, 13000, delta=1)
        self.assertAlmostEqual(floors[1].usable_area_sf, 7000, delta=1)
        self.assertAlmostEqual(actual, 20000, delta=1)

    def test_higher_floors_do_not_close_over_the_gym_void(self) -> None:
        voids = [VoidRegion("Gymnasium", 60, 100, 6000)]
        floors, actual = solve_mass_footprint(36000, 3, 80, voids=voids)
        self.assertGreaterEqual(len(floors), 3)
        self.assertFalse(floors[0].voids)
        for floor in floors[1:]:
            self.assertTrue(floor.voids, f"L{floor.level} closed over the gym")
            self.assertLessEqual(floor.length_ft, floors[0].length_ft + 0.1)
        # No higher plate is a full lid longer than the voided floor under it.
        voided = [f for f in floors if f.voids]
        for lower, upper in zip(voided, voided[1:]):
            self.assertLessEqual(upper.length_ft, lower.length_ft + 0.1)
        self.assertAlmostEqual(actual, 36000, delta=1)
        self.assertTrue(gsf_fit_pass(9750, 10000, 0.03))  # 2.5% under
        self.assertFalse(gsf_fit_pass(9500, 10000, 0.03))  # 5% under

    def test_compromise_list(self) -> None:
        mass = SolvedMass(
            id="hpe",
            name="HPE",
            departments=["HEALTH & PHYSICAL EDUCATION"],
            floors=[
                FloorPlate(level=0, width_ft=72, length_ft=90, area_sf=6480, programs=[])
            ],
            target_gsf=10000,
            actual_gsf=6480,
            fit_delta_sf=-3520,
            fit_pass=False,
        )
        compromised = check_anchor_fit(
            mass,
            [
                {
                    "room_name": "Gymnasium",
                    "min_width_ft": 60,
                    "min_length_ft": 100,
                }
            ],
        )
        self.assertEqual(len(compromised), 1)
        self.assertEqual(compromised[0].room, "Gymnasium")

    def test_underwood_solve_and_visual(self) -> None:
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        session = StudySession(
            study_id="phase3_demo",
            program=program,
            config_path=str(CONFIG),
        )
        depts = session.department_names()
        academic = [d for d in depts if "ACADEMIC" in d or "SPECIAL" in d]
        hpe = [d for d in depts if "HEALTH" in d or "DINING" in d]
        rest = [d for d in depts if d not in academic and d not in hpe]
        set_grouping(
            session,
            [
                {
                    "id": "academic",
                    "name": "Academic Wing",
                    "departments": academic,
                    "story_count": 3,
                },
                {
                    "id": "hpe_dining",
                    "name": "HPE / Dining",
                    "departments": hpe,
                    "story_count": 2,
                },
                {
                    "id": "support",
                    "name": "Support",
                    "departments": rest,
                    "story_count": 2,
                },
            ],
        )
        mark_double_height(session, "Gymnasium")
        session.constraints["academic_width_ft"] = 80
        session.constraints["hpe_dining_width_ft"] = 100

        result = solve_massing_study(session, config_path=str(CONFIG))
        self.assertEqual(len(result.masses), 3)
        academic_mass = next(m for m in result.masses if m.id == "academic")
        self.assertAlmostEqual(academic_mass.fixed_dim_ft, 80)
        self.assertTrue(academic_mass.fit_pass)

        report = format_massing_report(result)
        self.assertIn("DIMENSION STUDY REPORT", report)
        self.assertIn("Academic Wing", report)

        png = Path(self.tmp.name) / "checkpoint.png"
        render_massing_visual(result, png)
        self.assertTrue(png.exists())
        self.assertGreater(png.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
