"""Phase 5 tests: floor-by-floor program allocation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from massing_explorer.allocate import allocate_programs, ground_affinity
from massing_explorer.load import load_program_file
from massing_explorer.massing_models import FloorPlate, VoidRegion
from massing_explorer.models import Room
from massing_explorer.report import format_massing_report
from massing_explorer.session import StudySession
from massing_explorer.solver import solve_massing_study
from massing_explorer.tools import (
    pin_department_to_floor,
    set_grouping,
    solve_dimensions,
    unpin_department,
)
from massing_explorer.visual import render_massing_visual

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"


def _floors(count: int, usable: float) -> list[FloorPlate]:
    return [
        FloorPlate(
            level=i, width_ft=80, length_ft=usable / 80, area_sf=usable, programs=[]
        )
        for i in range(count)
    ]


class TestAllocator(unittest.TestCase):
    def test_ground_affinity_defaults(self) -> None:
        self.assertGreater(ground_affinity("DINING & FOOD SERVICE"), 0)
        self.assertGreater(ground_affinity("MEDIA CENTER"), 0)
        self.assertLess(ground_affinity("CORE ACADEMIC"), 0)
        self.assertLess(ground_affinity("ART & MUSIC"), 0)
        self.assertEqual(ground_affinity("SOMETHING UNKNOWN"), 0)

    def test_config_overrides_affinity(self) -> None:
        config = {"floor_preferences": {"ground": ["academic"], "upper": ["dining"]}}
        self.assertGreater(ground_affinity("CORE ACADEMIC", config), 0)
        self.assertLess(ground_affinity("DINING & FOOD SERVICE", config), 0)

    def test_conservation_and_full_floors(self) -> None:
        floors = _floors(3, 10000)
        rooms = [
            Room("Classroom", 20, 500, "ACADEMIC"),
            Room("Office", 10, 100, "ADMINISTRATION"),
        ]
        # NFA 10,000 + 1,000; multiplier 1.0 keeps the math easy to read
        dept_gsf = {"ACADEMIC": 10000.0, "ADMINISTRATION": 1000.0}
        allocate_programs(
            floors=floors,
            departments=["ACADEMIC", "ADMINISTRATION"],
            dept_gsf=dept_gsf,
            rooms=rooms,
            multiplier=1.0,
        )
        total = sum(f.allocated_gsf for f in floors)
        self.assertAlmostEqual(total, 11000.0, delta=1)
        # No floor may exceed its usable area
        for f in floors:
            self.assertLessEqual(f.allocated_gsf, f.usable_area_sf + 1.0)

    def test_ground_seeking_department_lands_low(self) -> None:
        floors = _floors(2, 5000)
        rooms = [
            Room("Classroom", 10, 500, "CORE ACADEMIC"),
            Room("Servery", 1, 5000, "DINING & FOOD SERVICE"),
        ]
        allocate_programs(
            floors=floors,
            departments=["CORE ACADEMIC", "DINING & FOOD SERVICE"],
            dept_gsf={"CORE ACADEMIC": 5000.0, "DINING & FOOD SERVICE": 5000.0},
            rooms=rooms,
            multiplier=1.0,
        )
        self.assertEqual(floors[0].programs, ["DINING & FOOD SERVICE"])
        self.assertEqual(floors[1].programs, ["CORE ACADEMIC"])

    def test_department_stays_contiguous(self) -> None:
        """A department must not be scattered as slivers to back-fill gaps."""
        floors = _floors(3, 10000)
        rooms = [
            Room("Classroom", 40, 500, "CORE ACADEMIC"),  # 20,000
            Room("Resource Room", 20, 500, "SPECIAL EDUCATION"),  # 10,000
        ]
        allocate_programs(
            floors=floors,
            departments=["CORE ACADEMIC", "SPECIAL EDUCATION"],
            dept_gsf={"CORE ACADEMIC": 20000.0, "SPECIAL EDUCATION": 10000.0},
            rooms=rooms,
            multiplier=1.0,
        )
        levels = {
            dept: [f.level for f in floors if dept in f.programs]
            for dept in ("CORE ACADEMIC", "SPECIAL EDUCATION")
        }
        # Core fills L0-L1 exactly, SpEd gets L2 to itself - no interleaving
        self.assertEqual(levels["CORE ACADEMIC"], [0, 1])
        self.assertEqual(levels["SPECIAL EDUCATION"], [2])

    def test_boundary_split_is_reported(self) -> None:
        floors = _floors(2, 5000)
        rooms = [Room("Classroom", 16, 500, "CORE ACADEMIC")]  # 8,000 over 2x5,000
        notes = allocate_programs(
            floors=floors,
            departments=["CORE ACADEMIC"],
            dept_gsf={"CORE ACADEMIC": 8000.0},
            rooms=rooms,
            multiplier=1.0,
        )
        self.assertTrue(any("spans" in n for n in notes))
        self.assertTrue(floors[0].allocations[0].split)
        self.assertAlmostEqual(floors[0].allocated_gsf, 5000, delta=1)
        self.assertAlmostEqual(floors[1].allocated_gsf, 3000, delta=1)

    def test_quantity_is_expanded_not_atomic(self) -> None:
        """A row of qty 20 is 20 placeable rooms, not one indivisible block."""
        floors = _floors(2, 5000)
        rooms = [Room("Classroom", 20, 500, "CORE ACADEMIC")]
        notes = allocate_programs(
            floors=floors,
            departments=["CORE ACADEMIC"],
            dept_gsf={"CORE ACADEMIC": 10000.0},
            rooms=rooms,
            multiplier=1.0,
        )
        self.assertFalse(any("exceeds total usable area" in n for n in notes))
        self.assertAlmostEqual(floors[0].allocated_gsf, 5000, delta=1)
        self.assertAlmostEqual(floors[1].allocated_gsf, 5000, delta=1)

    def test_grossing_multiplier_applied(self) -> None:
        floors = _floors(1, 20000)
        rooms = [Room("Classroom", 10, 1000, "CORE ACADEMIC")]  # 10,000 NFA
        allocate_programs(
            floors=floors,
            departments=["CORE ACADEMIC"],
            dept_gsf={"CORE ACADEMIC": 17250.0},  # 10,000 x 1.725
            rooms=rooms,
            multiplier=1.725,
        )
        self.assertAlmostEqual(floors[0].allocated_gsf, 17250.0, delta=1)

    def test_pin_overrides_affinity(self) -> None:
        floors = _floors(2, 5000)
        rooms = [
            Room("Classroom", 10, 500, "CORE ACADEMIC"),
            Room("Servery", 10, 500, "DINING & FOOD SERVICE"),
        ]
        allocate_programs(
            floors=floors,
            departments=["CORE ACADEMIC", "DINING & FOOD SERVICE"],
            dept_gsf={"CORE ACADEMIC": 5000.0, "DINING & FOOD SERVICE": 5000.0},
            rooms=rooms,
            multiplier=1.0,
            pins={"DINING & FOOD SERVICE": 1},
        )
        # Dining normally seeks the ground floor; the pin sends it upstairs
        self.assertEqual(floors[1].programs, ["DINING & FOOD SERVICE"])
        self.assertEqual(floors[0].programs, ["CORE ACADEMIC"])

    def test_void_owner_forced_to_grade(self) -> None:
        floors = _floors(2, 5000)
        rooms = [
            Room("Gymnasium", 1, 5000, "HEALTH & PHYSICAL EDUCATION"),
            Room("Classroom", 10, 500, "CORE ACADEMIC"),
        ]
        allocate_programs(
            floors=floors,
            departments=["CORE ACADEMIC", "HEALTH & PHYSICAL EDUCATION"],
            dept_gsf={
                "CORE ACADEMIC": 5000.0,
                "HEALTH & PHYSICAL EDUCATION": 5000.0,
            },
            rooms=rooms,
            multiplier=1.0,
            ground_required={"HEALTH & PHYSICAL EDUCATION"},
        )
        self.assertIn("HEALTH & PHYSICAL EDUCATION", floors[0].programs)

    def test_token_fragment_avoided_when_area_can_go_elsewhere(self) -> None:
        floors = _floors(2, 5000)
        rooms = [
            Room("Servery", 49, 100, "DINING & FOOD SERVICE"),  # 4,900
            Room("Classroom", 10, 500, "CORE ACADEMIC"),  # 5,000
        ]
        allocate_programs(
            floors=floors,
            departments=["DINING & FOOD SERVICE", "CORE ACADEMIC"],
            dept_gsf={"DINING & FOOD SERVICE": 4900.0, "CORE ACADEMIC": 5000.0},
            rooms=rooms,
            multiplier=1.0,
        )
        # The 100 SF left on L0 is too small to be worth a slice of academic,
        # and L1 can take the whole department, so L0 is left slightly short.
        self.assertEqual(floors[0].programs, ["DINING & FOOD SERVICE"])
        self.assertEqual(floors[1].programs, ["CORE ACADEMIC"])
        self.assertFalse(floors[1].allocations[0].split)
        self.assertAlmostEqual(floors[0].allocated_gsf, 4900, delta=1)

    def test_token_fragment_kept_when_area_is_forced(self) -> None:
        floors = _floors(2, 5000)
        rooms = [
            Room("Servery", 49, 100, "DINING & FOOD SERVICE"),  # 4,900
            Room("Classroom", 51, 100, "CORE ACADEMIC"),  # 5,100
        ]
        allocate_programs(
            floors=floors,
            departments=["DINING & FOOD SERVICE", "CORE ACADEMIC"],
            dept_gsf={"DINING & FOOD SERVICE": 4900.0, "CORE ACADEMIC": 5100.0},
            rooms=rooms,
            multiplier=1.0,
        )
        # Now academic will not fit above, so the 100 SF sliver must be used
        # rather than losing area
        self.assertIn("CORE ACADEMIC", floors[0].programs)
        self.assertAlmostEqual(
            sum(f.allocated_gsf for f in floors), 10000.0, delta=1
        )

    def test_overflow_conserves_area_and_warns(self) -> None:
        floors = _floors(1, 1000)
        rooms = [Room("Classroom", 4, 1000, "CORE ACADEMIC")]
        notes = allocate_programs(
            floors=floors,
            departments=["CORE ACADEMIC"],
            dept_gsf={"CORE ACADEMIC": 4000.0},
            rooms=rooms,
            multiplier=1.0,
        )
        self.assertTrue(
            any(
                "exceeds" in n and "usable area" in n
                for n in notes
            )
        )
        # Area is never silently dropped
        self.assertAlmostEqual(floors[0].allocated_gsf, 4000.0, delta=1)

    def test_no_floors_is_safe(self) -> None:
        self.assertEqual(allocate_programs([], [], {}, [], 1.0), [])

    def test_pin_above_story_count_clamps_not_crash(self) -> None:
        """COVER may shrink to 1 story while a top-floor pin still says L1."""
        floors = _floors(1, 8000)
        allocate_programs(
            floors=floors,
            departments=["ADMIN", "MEDIA"],
            dept_gsf={"ADMIN": 3000.0, "MEDIA": 4000.0},
            rooms=[
                Room("Office", 1, 3000, "ADMIN"),
                Room("Library", 1, 4000, "MEDIA"),
            ],
            multiplier=1.0,
            pins={"ADMIN": 0, "MEDIA": 1},
        )
        self.assertAlmostEqual(floors[0].allocated_gsf, 7000.0, delta=1)
        depts = {a.department for a in floors[0].allocations}
        self.assertEqual(depts, {"ADMIN", "MEDIA"})

    def test_allocate_stays_contiguous_no_floor_gaps(self) -> None:
        """A department must not occupy L0 and L2 while skipping L1."""
        floors = _floors(3, 1000)
        allocate_programs(
            floors=floors,
            departments=["RED", "PURPLE"],
            dept_gsf={"RED": 1500.0, "PURPLE": 1000.0},
            rooms=[
                Room("R", 1, 1500, "RED"),
                Room("P", 1, 1000, "PURPLE"),
            ],
            multiplier=1.0,
        )
        by_dept: dict[str, list[int]] = {}
        for floor in floors:
            for alloc in floor.allocations:
                if alloc.gsf <= 0:
                    continue
                by_dept.setdefault(alloc.department, []).append(floor.level)
        for dept, levels in by_dept.items():
            ordered = sorted(set(levels))
            self.assertEqual(
                ordered[-1] - ordered[0] + 1,
                len(ordered),
                msg=f"{dept} has gap floors {ordered}",
            )

    def test_utilization_property(self) -> None:
        floor = FloorPlate(level=0, width_ft=80, length_ft=100, area_sf=8000)
        self.assertEqual(floor.utilization, 0.0)
        floor.voids = [VoidRegion("Gym", 40, 50, 2000)]
        floor.usable_area_sf = 6000
        allocate_programs(
            floors=[floor],
            departments=["X"],
            dept_gsf={"X": 3000.0},
            rooms=[Room("Room", 1, 3000, "X")],
            multiplier=1.0,
        )
        self.assertAlmostEqual(floor.utilization, 0.5, places=3)


class TestUnderwoodAllocation(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"

        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="phase5_demo", program=program, config_path=str(CONFIG)
        )
        depts = self.session.department_names()
        academic = [d for d in depts if "ACADEMIC" in d or "SPECIAL" in d]
        hpe = [d for d in depts if "HEALTH" in d or "DINING" in d]
        rest = [d for d in depts if d not in academic and d not in hpe]
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
        self.session.double_height_rooms.append("Gymnasium")
        self.session.constraints["academic_width_ft"] = 80
        self.session.constraints["hpe_dining_width_ft"] = 100
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_every_floor_fully_allocated(self) -> None:
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        for mass in result.masses:
            for floor in mass.floors:
                self.assertGreater(
                    floor.utilization, 0.90, f"{mass.id} L{floor.level} underfilled"
                )
                self.assertLessEqual(
                    floor.utilization, 1.01, f"{mass.id} L{floor.level} overfilled"
                )
                self.assertTrue(floor.allocations)

    def test_allocation_checks_pass(self) -> None:
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        for check in result.validation:
            if check.check.startswith(("allocation_conserved", "floor_capacity")):
                self.assertTrue(check.passed, check.message)

    def test_gym_department_at_grade(self) -> None:
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        hpe = next(m for m in result.masses if m.id == "hpe_dining")
        self.assertIn("HEALTH & PHYSICAL EDUCATION", hpe.floors[0].programs)
        # The void sits on the floor above the gym, not on the gym's own floor
        self.assertFalse(hpe.floors[0].voids)
        self.assertTrue(hpe.floors[1].voids)

    def test_report_shows_area_per_level(self) -> None:
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        report = format_massing_report(result)
        self.assertIn("% of usable area", report)
        academic = next(m for m in result.masses if m.id == "academic")
        top = academic.floors[-1]
        self.assertIn("SPECIAL EDUCATION", top.programs)

    def test_pin_tool_moves_department(self) -> None:
        before = solve_massing_study(self.session, config_path=str(CONFIG))
        support_before = next(m for m in before.masses if m.id == "support")
        self.assertIn("MEDIA CENTER", support_before.floors[0].programs)

        out = pin_department_to_floor(self.session, "MEDIA CENTER", 1)
        self.assertTrue(out["ok"])

        after = solve_massing_study(self.session, config_path=str(CONFIG))
        support_after = next(m for m in after.masses if m.id == "support")
        self.assertIn("MEDIA CENTER", support_after.floors[1].programs)
        self.assertNotIn("MEDIA CENTER", support_after.floors[0].programs)

    def test_pin_accepts_partial_name(self) -> None:
        out = pin_department_to_floor(self.session, "media", 1)
        self.assertTrue(out["ok"])
        self.assertEqual(self.session.floor_pins.get("MEDIA CENTER"), 1)

    def test_pin_rejects_nonexistent_level(self) -> None:
        out = pin_department_to_floor(self.session, "MEDIA CENTER", 5)
        self.assertFalse(out["ok"])
        self.assertIn("2 stories", out["error"])

    def test_pin_rejects_unknown_department(self) -> None:
        out = pin_department_to_floor(self.session, "SWIMMING POOL", 0)
        self.assertFalse(out["ok"])
        self.assertIn("known_departments", out)

    def test_unpin_restores_default(self) -> None:
        pin_department_to_floor(self.session, "MEDIA CENTER", 1)
        out = unpin_department(self.session, "MEDIA CENTER")
        self.assertTrue(out["ok"])
        self.assertEqual(self.session.floor_pins, {})
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        support = next(m for m in result.masses if m.id == "support")
        self.assertIn("MEDIA CENTER", support.floors[0].programs)

    def test_pins_persist(self) -> None:
        pin_department_to_floor(self.session, "MEDIA CENTER", 1)
        reloaded = StudySession.load("phase5_demo")
        self.assertEqual(reloaded.floor_pins, {"MEDIA CENTER": 1})

    def test_solve_tool_reports_program_per_level(self) -> None:
        out = solve_dimensions(self.session)
        academic = next(m for m in out["masses"] if m["id"] == "academic")
        self.assertEqual(len(academic["floors"]), 3)
        for floor in academic["floors"]:
            self.assertTrue(floor["programs"])
            self.assertGreaterEqual(floor["utilization_pct"], 90)
        total = sum(
            gsf for f in academic["floors"] for gsf in f["programs"].values()
        )
        self.assertAlmostEqual(total, academic["target_gsf"], delta=5)

    def test_visual_renders_with_allocations(self) -> None:
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        png = Path(self.tmp.name) / "alloc.png"
        render_massing_visual(result, png)
        self.assertTrue(png.exists())
        self.assertGreater(png.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
