"""In-mass L-shaped footprints — only when dimensions are usable."""

from __future__ import annotations

import unittest

from massing_explorer.allocate import allocate_programs
from massing_explorer.layout import (
    DimRequirement,
    Rect,
    classify_shape,
    clear_dims_for_departments,
    layout_floor,
    layout_programs,
    place_voids,
    region_is_l,
    remaining_region,
    subtract,
)
from massing_explorer.massing_models import FloorPlate, ProgramAllocation, VoidRegion
from massing_explorer.models import Room

DINING = "DINING & FOOD SERVICE"
HPE = "HEALTH & PHYSICAL EDUCATION"
CONFIG = {
    "layout": {"min_arm_depth_ft": 20},
    "anchor_rooms": {
        "cafeteria": {"min_width_ft": 40, "min_length_ft": 60},
        "gym": {"min_width_ft": 60, "min_length_ft": 100, "double_height": True},
    },
}


class TestSubtract(unittest.TestCase):
    def test_corner_void_leaves_an_l(self) -> None:
        leftover = subtract(Rect(0, 0, 100, 130), Rect(0, 0, 60, 100))
        self.assertTrue(region_is_l(leftover))
        self.assertAlmostEqual(sum(r.area for r in leftover), 7000, delta=1)

    def test_full_width_void_leaves_a_bar(self) -> None:
        leftover = subtract(Rect(0, 0, 100, 130), Rect(0, 0, 100, 60))
        self.assertEqual(len(leftover), 1)
        self.assertFalse(region_is_l(leftover))


class TestDimGatedLayout(unittest.TestCase):
    def test_dining_around_gym_ok_when_clear_dims_fit(self) -> None:
        """
        100x130 plate, 60x100 gym: leftover L has a 40x100 arm — cafeteria
        40x60 fits there, so dining may wrap as an L.
        """
        floor = FloorPlate(
            level=1,
            width_ft=100,
            length_ft=130,
            area_sf=13000,
            voids=[VoidRegion("Gymnasium", 60, 100, 6000)],
        )
        floor.usable_area_sf = 7000
        floor.allocations = [ProgramAllocation(department=DINING, gsf=7000)]
        fails = layout_floor(floor, config=CONFIG)
        dining = floor.allocations[0]
        self.assertEqual(fails, [], fails)
        self.assertTrue(dining.layout_ok)
        self.assertEqual(dining.shape, "L")
        self.assertTrue(
            any(
                min(p.width_ft, p.length_ft) >= 20 - 1e-6
                for p in dining.footprints
            )
        )
        # Clear cafeteria block exists as one rectangle piece
        self.assertTrue(
            any(
                (p.width_ft + 1e-6 >= 40 and p.length_ft + 1e-6 >= 60)
                or (p.width_ft + 1e-6 >= 60 and p.length_ft + 1e-6 >= 40)
                for p in dining.footprints
            ),
            dining.footprints,
        )

    def test_skinny_leftover_rejected_not_stuffed(self) -> None:
        """
        70x110 plate with 60x100 gym leaves a ~10 ft strip — too skinny for a
        20 ft min arm. Must FAIL, not paint an L. (Dining GSF here is below the
        cafeteria clear area, so this is purely an arm-depth rejection.)
        """
        floor = FloorPlate(
            level=1,
            width_ft=70,
            length_ft=110,
            area_sf=7700,
            voids=[VoidRegion("Gymnasium", 60, 100, 6000)],
        )
        floor.usable_area_sf = 1700
        floor.allocations = [ProgramAllocation(department=DINING, gsf=1700)]
        fails = layout_floor(floor, config=CONFIG)
        dining = floor.allocations[0]
        self.assertFalse(dining.layout_ok)
        self.assertEqual(dining.footprints, [])
        self.assertTrue(fails)
        self.assertTrue(
            any("skinny" in f or "usable leftover" in f or "arm" in f for f in fails),
            fails,
        )

    def test_clear_dims_block_l_without_a_fitting_rectangle(self) -> None:
        """
        Leftover arms are deep enough (>=20) but neither arm can hold 40x60 —
        e.g. 25 ft and 25 ft arms. Area may be enough; dims are not.
        """
        floor = FloorPlate(
            level=1,
            width_ft=85,
            length_ft=125,
            area_sf=10625,
            voids=[VoidRegion("Gymnasium", 60, 100, 6000)],
        )
        # leftover ~ 25x100 + 85x25 = 2500+2125 = 4625; arms 25 ft deep
        floor.usable_area_sf = 4625
        floor.allocations = [ProgramAllocation(department=DINING, gsf=4500)]
        fails = layout_floor(floor, config=CONFIG)
        dining = floor.allocations[0]
        self.assertFalse(dining.layout_ok, dining.layout_issue)
        self.assertEqual(dining.footprints, [])
        self.assertTrue(any("40" in f and "60" in f for f in fails), fails)

    def test_clear_block_absorbs_skinny_tip_not_dead_alley(self) -> None:
        """
        105x120 plate, 60x100 gym: side arm is 45 ft. Cafeteria needs 40x60 —
        carving exactly 40 would leave a 5 ft alley. The clear block must widen
        to the full 45 ft arm so dining still meets dims as a real rectangle L.
        """
        floor = FloorPlate(
            level=1,
            width_ft=105,
            length_ft=120,
            area_sf=12600,
            voids=[VoidRegion("Gymnasium", 60, 100, 6000)],
        )
        floor.usable_area_sf = 6600
        floor.allocations = [ProgramAllocation(department=DINING, gsf=6598)]
        fails = layout_floor(floor, config=CONFIG)
        dining = floor.allocations[0]
        self.assertEqual(fails, [], fails)
        self.assertTrue(dining.layout_ok, dining.layout_issue)
        self.assertEqual(dining.shape, "L")
        self.assertTrue(
            any(
                (p.width_ft + 1e-6 >= 40 and p.length_ft + 1e-6 >= 60)
                or (p.width_ft + 1e-6 >= 60 and p.length_ft + 1e-6 >= 40)
                for p in dining.footprints
            ),
            dining.footprints,
        )
        # No footprint piece thinner than min arm depth
        self.assertTrue(
            all(min(p.width_ft, p.length_ft) >= 20 - 1e-6 for p in dining.footprints),
            dining.footprints,
        )

    def test_no_void_rectangle_still_ok(self) -> None:
        floor = FloorPlate(level=0, width_ft=80, length_ft=100, area_sf=8000)
        floor.usable_area_sf = 8000
        floor.allocations = [
            ProgramAllocation(department="CORE ACADEMIC", gsf=5000),
            ProgramAllocation(department="SPECIAL EDUCATION", gsf=3000),
        ]
        fails = layout_floor(floor, config=CONFIG)
        self.assertEqual(fails, [])
        for alloc in floor.allocations:
            self.assertTrue(alloc.layout_ok)
            self.assertEqual(alloc.shape, "rectangle")
            self.assertEqual(len(alloc.footprints), 1)


class TestLayoutRetry(unittest.TestCase):
    def test_narrow_mass_retries_wider_before_fail(self) -> None:
        """
        A plate too narrow for cafeteria clear dims beside the gym should not
        hard-fail immediately — the solver widens (or adds stories) first.
        """
        from massing_explorer.layout import plate_can_host_void_layout, DimRequirement
        from massing_explorer.massing_models import VoidRegion as VR

        # 80x120 with 60x100 gym: side arm 20 ft, end arm 20 ft — neither holds 40x60
        self.assertFalse(
            plate_can_host_void_layout(
                80,
                120,
                [VR("Gymnasium", 60, 100, 6000)],
                [DimRequirement(DINING, 40, 60, "cafeteria")],
                20,
            )
        )
        # 100x160 leaves a 40 ft side arm — can hold 40x60
        self.assertTrue(
            plate_can_host_void_layout(
                100,
                160,
                [VR("Gymnasium", 60, 100, 6000)],
                [DimRequirement(DINING, 40, 60, "cafeteria")],
                20,
            )
        )

    def test_solver_widens_when_void_l_fails(self) -> None:
        """Unpaired mass that starts too narrow gets auto-widened on solve."""
        import tempfile
        from pathlib import Path

        import massing_explorer.session as session_mod
        from massing_explorer.load import load_program_file
        from massing_explorer.session import StudySession
        from massing_explorer.solver import solve_massing_study
        from massing_explorer.tools import set_grouping

        root = Path(__file__).resolve().parents[1]
        underwood = root / "examples" / "underwood_elementary_space_summary.xlsx"
        config = root / "config" / "project.example.yaml"
        tmp = tempfile.TemporaryDirectory()
        orig = session_mod.STUDIES_DIR
        session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
        try:
            program = load_program_file(underwood, config_path=config)
            session = StudySession(
                study_id="layout_retry", program=program, config_path=str(config)
            )
            set_grouping(
                session,
                [
                    {
                        "id": "community",
                        "name": "Community",
                        "departments": [HPE, DINING],
                        "story_count": 2,
                    }
                ],
            )
            session.double_height_rooms.append("Gymnasium")
            session.constraints["community_width_ft"] = 75
            result = solve_massing_study(session, config_path=str(config))
            mass = result.masses[0]
            layout_fails = [
                v
                for v in result.validation
                if not v.passed and v.check.startswith("layout_dims:")
            ]
            retries = [
                v for v in result.validation if v.check.startswith("layout_retry:")
            ]
            # Either auto-retry found a working width/stories, or every option
            # within limits failed and we still surface layout_dims (not silent).
            if layout_fails:
                self.assertFalse(retries)
            else:
                self.assertTrue(retries, "expected layout_retry note when dims pass after adjust")
                self.assertGreaterEqual(mass.fixed_dim_ft, 75.0)
        finally:
            session_mod.STUDIES_DIR = orig
            tmp.cleanup()


class TestAllocateThenLayout(unittest.TestCase):
    def test_void_floor_l_when_dims_work(self) -> None:
        floors = [
            FloorPlate(level=0, width_ft=100, length_ft=130, area_sf=13000),
            FloorPlate(
                level=1,
                width_ft=100,
                length_ft=130,
                area_sf=13000,
                voids=[VoidRegion("Gymnasium", 60, 100, 6000)],
            ),
        ]
        floors[0].usable_area_sf = 13000
        floors[1].usable_area_sf = 7000
        rooms = [
            Room("Gymnasium", 1, 6000, HPE, 6000),
            Room("Cafeteria", 1, 4000, DINING, 4000),
        ]
        allocate_programs(
            floors,
            [HPE, DINING],
            {HPE: 13000, DINING: 7000},
            rooms,
            multiplier=1.0,
            ground_required={HPE},
        )
        ok, fails = layout_programs(floors, config=CONFIG, departments=[HPE, DINING])
        self.assertEqual(fails, [], fails)
        dining = next(a for a in floors[1].allocations if a.department == DINING)
        self.assertEqual(dining.shape, "L")
        self.assertTrue(any("L-shaped" in n for n in ok))


class TestClearDimMapping(unittest.TestCase):
    def test_cafeteria_maps_to_dining(self) -> None:
        reqs = clear_dims_for_departments([DINING, HPE], CONFIG)
        self.assertIn(DINING, reqs)
        self.assertEqual(reqs[DINING].min_width_ft, 40)
        self.assertEqual(reqs[DINING].min_length_ft, 60)


class TestClassify(unittest.TestCase):
    def test_two_adjacent_rects_are_l(self) -> None:
        self.assertEqual(
            classify_shape([Rect(0, 0, 40, 100), Rect(0, 100, 100, 30)]),
            "L",
        )


class TestPlaceVoids(unittest.TestCase):
    def test_prefers_placement_with_usable_arms(self) -> None:
        floor = FloorPlate(
            level=1,
            width_ft=100,
            length_ft=130,
            area_sf=13000,
            voids=[VoidRegion("Gymnasium", 60, 100, 6000)],
        )
        place_voids(floor, min_depth=20)
        leftover = remaining_region(floor)
        usable = [r for r in leftover if r.depth >= 20 - 1e-6]
        self.assertGreaterEqual(sum(r.area for r in usable), 7000 - 1)


class TestLengthCapIsNotATarget(unittest.TestCase):
    def test_stack_above_uses_functional_width_not_the_cap(self) -> None:
        import tempfile
        from pathlib import Path

        import massing_explorer.session as session_mod
        from massing_explorer.brief import apply_parsed_brief, parse_brief
        from massing_explorer.load import load_program_file
        from massing_explorer.reading import validate_reading
        from massing_explorer.session import StudySession
        from massing_explorer.solver import solve_massing_study
        from massing_explorer.tools import mark_double_height

        root = Path(__file__).resolve().parents[1]
        tmp = tempfile.TemporaryDirectory()
        orig = session_mod.STUDIES_DIR
        session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
        text = (
            "mass one is gym and core academic, mass two is dining and media. "
            "the length of these two mass together should be under 500ft."
        )
        try:
            program = load_program_file(
                root / "examples" / "underwood_elementary_space_summary.xlsx",
                config_path=root / "config" / "project.example.yaml",
            )
            session = StudySession(
                study_id="cap_not_target",
                program=program,
                config_path=str(root / "config" / "project.example.yaml"),
            )
            names = session.department_names()
            reading = validate_reading(
                {
                    "masses": [
                        {"name": "Mass 1", "departments": ["gym", "core academic"]},
                        {"name": "Mass 2", "departments": ["dining", "media"]},
                    ],
                    "pair_length_ft": 500,
                    "loading": "double",
                    "corridor_ft": 8,
                    "classroom_depth_ft": 30,
                    "reasons": ["together under 500 is a cap"],
                },
                names,
            )
            apply_parsed_brief(session, parse_brief(text, names), reading=reading)
            mark_double_height(session, "Gymnasium")
            result = solve_massing_study(session)
            gym = next(m for m in result.masses if "HEALTH" in " ".join(m.departments))
            dining = next(
                m
                for m in result.masses
                if any("DINING" in d for d in m.departments)
            )
            self.assertGreaterEqual(gym.fixed_dim_ft, 68.0 - 0.1)
            self.assertLess(gym.floors[0].length_ft + dining.floors[0].length_ft, 500)
            self.assertLess(gym.floors[0].length_ft, 250)
            ground = gym.floors[0]
            self.assertTrue(any(a.double_height for a in ground.allocations))
            self.assertTrue(all(f.level != 1 for f in gym.floors))
            self.assertTrue(any(f.level >= 2 for f in gym.floors))
            academic = [
                a.department
                for f in gym.floors
                if f.level == 0
                for a in f.allocations
            ]
            self.assertNotIn("CORE ACADEMIC", academic)
        finally:
            session_mod.STUDIES_DIR = orig
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
