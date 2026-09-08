"""Rhino export extrudes checked floor plates; it does not invent sizes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from massing_explorer.massing_models import (
    FloorPlate,
    FootprintRect,
    MassingStudyResult,
    ProgramAllocation,
    SolvedMass,
    VoidRegion,
)
from massing_explorer.rhino_export import export_rhino


def _mass(
    mass_id: str,
    name: str,
    floors: list[FloorPlate],
    pairing_id: str = "",
) -> SolvedMass:
    return SolvedMass(
        id=mass_id,
        name=name,
        departments=list(floors[0].programs),
        floors=floors,
        target_gsf=sum(f.area_sf for f in floors),
        actual_gsf=sum(f.usable_area_sf for f in floors),
        fit_delta_sf=0.0,
        fit_pass=True,
        pairing_id=pairing_id,
    )


def _objects(path: Path):
    import rhino3dm

    model = rhino3dm.File3dm.Read(str(path))
    return model, list(model.Objects), list(model.Layers)


class TestRhinoExport(unittest.TestCase):
    def test_box_matches_reported_floor_and_story_height(self) -> None:
        floor = FloorPlate(
            level=0,
            width_ft=80,
            length_ft=120,
            area_sf=9600,
            programs=["CORE ACADEMIC"],
        )
        result = MassingStudyResult(
            study_id="box",
            masses=[_mass("academic", "Academic", [floor])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = export_rhino(result, Path(tmp) / "box.3dm", story_height_ft=14)
            model, objects, layers = _objects(path)
            self.assertEqual(str(model.Settings.ModelUnitSystem), "UnitSystem.Feet")
            names = {obj.Attributes.Name for obj in objects}
            self.assertIn("CORE ACADEMIC / Academic L0", names)
            floor_obj = next(o for o in objects if o.Attributes.GetUserString("role") == "program")
            box = floor_obj.Geometry.GetBoundingBox()
            self.assertAlmostEqual(box.Min.X, 0, places=2)
            self.assertAlmostEqual(box.Min.Z, 0, places=2)
            self.assertAlmostEqual(box.Max.X - box.Min.X, 120, places=2)
            self.assertAlmostEqual(box.Max.Y - box.Min.Y, 80, places=2)
            self.assertAlmostEqual(box.Max.Z, 14, places=2)
            self.assertEqual(floor_obj.Attributes.GetUserString("width_ft"), "80.00")
            self.assertTrue(any(layer.FullPath.startswith("masses") or layer.Name == "Academic" for layer in layers))

    def test_paired_masses_share_frontage_and_stack_stories(self) -> None:
        a0 = FloorPlate(0, 60, 200, 12000, programs=["GYM"])
        a1 = FloorPlate(1, 60, 140, 8400, programs=["GYM"])
        b0 = FloorPlate(0, 60, 300, 18000, programs=["DINING"])
        result = MassingStudyResult(
            study_id="pair",
            masses=[
                _mass("m2", "Mass 2", [b0], pairing_id="pair"),
                _mass("m1", "Mass 1", [a0, a1], pairing_id="pair"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = export_rhino(result, Path(tmp) / "pair.3dm")
            _, objects, _ = _objects(path)
            plates = [
                o for o in objects if o.Attributes.GetUserString("role") == "program"
            ]
            by_name = {o.Attributes.Name: o for o in plates}
            self.assertIn("GYM / Mass 1 L0", by_name)
            self.assertIn("GYM / Mass 1 L1", by_name)
            self.assertIn("DINING / Mass 2 L0", by_name)
            first = by_name["GYM / Mass 1 L0"].Geometry.GetBoundingBox()
            second = by_name["DINING / Mass 2 L0"].Geometry.GetBoundingBox()
            upper = by_name["GYM / Mass 1 L1"].Geometry.GetBoundingBox()
            self.assertAlmostEqual(first.Min.X, 0, places=2)
            self.assertAlmostEqual(first.Min.Z, 0, places=2)
            self.assertAlmostEqual(first.Max.Z, 14, places=2)
            self.assertAlmostEqual(second.Min.X, 200, places=2)
            self.assertAlmostEqual(upper.Min.Z, 14, places=2)
            self.assertAlmostEqual(upper.Max.Z, 28, places=2)
            self.assertAlmostEqual(upper.Max.X - upper.Min.X, 140, places=2)
            self.assertAlmostEqual(second.Max.X, 500, places=2)

    def test_void_is_cut_not_filled(self) -> None:
        upper = FloorPlate(
            level=1,
            width_ft=100,
            length_ft=200,
            area_sf=20000,
            programs=["CORE ACADEMIC"],
            voids=[VoidRegion("Gymnasium", 60, 100, 6000, x_ft=0, y_ft=0)],
        )
        upper.usable_area_sf = 14000
        ground = FloorPlate(
            level=0,
            width_ft=100,
            length_ft=200,
            area_sf=20000,
            programs=["HEALTH & PHYSICAL EDUCATION"],
            allocations=[
                ProgramAllocation(
                    department="HEALTH & PHYSICAL EDUCATION",
                    gsf=20000,
                    footprints=[],
                )
            ],
        )
        result = MassingStudyResult(
            study_id="void",
            masses=[_mass("hpe", "HPE", [ground, upper])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = export_rhino(result, Path(tmp) / "void.3dm")
            _, objects, _layers = _objects(path)
            upper_parts = [
                o
                for o in objects
                if o.Attributes.GetUserString("role") == "program"
                and o.Attributes.GetUserString("level") == "1"
            ]
            self.assertTrue(upper_parts)
            self.assertEqual(upper_parts[0].Attributes.GetUserString("voids"), "1")
            # The corner void is open: no upper solid covers its interior.
            void_cx, void_cy = 30.0, 50.0
            for obj in upper_parts:
                box = obj.Geometry.GetBoundingBox()
                # Local width x -> world Y, local length y -> world X.
                covers = (
                    box.Min.X <= void_cy <= box.Max.X
                    and box.Min.Y <= void_cx <= box.Max.Y
                )
                self.assertFalse(covers, obj.Attributes.Name)

    def test_programs_are_separate_colored_cubes_in_one_mass(self) -> None:
        floor = FloorPlate(
            level=0,
            width_ft=40,
            length_ft=100,
            area_sf=4000,
            programs=["GYM", "CORE ACADEMIC"],
            allocations=[
                ProgramAllocation(
                    department="GYM",
                    gsf=3000,
                    footprints=[FootprintRect(0, 0, 40, 75)],
                ),
                ProgramAllocation(
                    department="CORE ACADEMIC",
                    gsf=1000,
                    footprints=[FootprintRect(0, 75, 40, 25)],
                ),
            ],
        )
        result = MassingStudyResult(
            study_id="split",
            masses=[_mass("m1", "Mass 1", [floor])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = export_rhino(result, Path(tmp) / "split.3dm")
            _, objects, layers = _objects(path)
            programs = [
                o for o in objects if o.Attributes.GetUserString("role") == "program"
            ]
            self.assertEqual(len(programs), 2)
            by_dept = {o.Attributes.GetUserString("department"): o for o in programs}
            gym = by_dept["GYM"].Geometry.GetBoundingBox()
            academic = by_dept["CORE ACADEMIC"].Geometry.GetBoundingBox()
            self.assertAlmostEqual(gym.Max.X - gym.Min.X, 75, places=2)
            self.assertAlmostEqual(academic.Min.X, 75, places=2)
            self.assertAlmostEqual(academic.Max.X, 100, places=2)
            colors = {
                layer.Name: layer.Color
                for layer in layers
                if layer.Name in {"GYM", "CORE ACADEMIC"}
            }
            self.assertEqual(set(colors), {"GYM", "CORE ACADEMIC"})
            self.assertNotEqual(colors["GYM"], colors["CORE ACADEMIC"])

    def test_floors_stack_flush_to_one_end(self) -> None:
        ground = FloorPlate(0, 40, 200, 8000, programs=["GYM"])
        upper = FloorPlate(1, 40, 120, 4800, programs=["CORE ACADEMIC"])
        short = FloorPlate(2, 40, 80, 3200, programs=["CORE ACADEMIC"])
        result = MassingStudyResult(
            study_id="align",
            masses=[_mass("m1", "Mass 1", [ground, upper, short])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = export_rhino(result, Path(tmp) / "align.3dm")
            _, objects, _ = _objects(path)
            boxes = {
                o.Attributes.GetUserString("level"): o.Geometry.GetBoundingBox()
                for o in objects
            }
            self.assertAlmostEqual(boxes["0"].Max.X, 200, places=2)
            self.assertAlmostEqual(boxes["1"].Max.X, 200, places=2)
            self.assertAlmostEqual(boxes["2"].Max.X, 200, places=2)
            self.assertAlmostEqual(boxes["1"].Min.X, 80, places=2)
            self.assertAlmostEqual(boxes["2"].Min.X, 120, places=2)
            self.assertAlmostEqual(boxes["0"].Min.Y, 0, places=2)
            self.assertAlmostEqual(boxes["1"].Min.Y, 0, places=2)
            self.assertAlmostEqual(boxes["2"].Min.Y, 0, places=2)

    def test_double_height_program_is_two_stories_tall(self) -> None:
        floor = FloorPlate(
            level=0,
            width_ft=60,
            length_ft=100,
            area_sf=6000,
            programs=["HEALTH & PHYSICAL EDUCATION"],
            allocations=[
                ProgramAllocation(
                    department="HEALTH & PHYSICAL EDUCATION",
                    gsf=6000,
                    double_height=True,
                    footprints=[FootprintRect(0, 0, 60, 100)],
                )
            ],
        )
        result = MassingStudyResult(
            study_id="dh",
            masses=[_mass("m1", "Mass 1", [floor])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = export_rhino(result, Path(tmp) / "dh.3dm", story_height_ft=14)
            _, objects, _ = _objects(path)
            gym = objects[0].Geometry.GetBoundingBox()
            self.assertAlmostEqual(gym.Min.Z, 0, places=2)
            self.assertAlmostEqual(gym.Max.Z, 28, places=2)
            self.assertEqual(objects[0].Attributes.GetUserString("double_height"), "true")

    def test_double_height_void_does_not_fill_the_l(self) -> None:
        """The gym is the void, not the whole ground plate, so the wrapping L stays clear."""
        hpe = "HEALTH & PHYSICAL EDUCATION"
        dining = "DINING & FOOD SERVICE"
        ground = FloorPlate(
            level=0,
            width_ft=100,
            length_ft=132,
            area_sf=13200,
            programs=[hpe, dining],
            allocations=[
                ProgramAllocation(
                    department=hpe,
                    gsf=10868,
                    double_height=True,
                    footprints=[FootprintRect(0, 0, 100, 109)],
                ),
                ProgramAllocation(
                    department=dining,
                    gsf=2300,
                    footprints=[FootprintRect(0, 109, 100, 23)],
                ),
            ],
        )
        upper = FloorPlate(
            level=1,
            width_ft=100,
            length_ft=132,
            area_sf=13200,
            programs=[dining],
            voids=[VoidRegion("Gymnasium", 60, 100, 6000, x_ft=0, y_ft=0)],
            allocations=[
                ProgramAllocation(
                    department=dining,
                    gsf=7215,
                    shape="L",
                    footprints=[
                        FootprintRect(60, 0, 40, 132),
                        FootprintRect(0, 100, 60, 32),
                    ],
                )
            ],
        )
        result = MassingStudyResult(
            study_id="l_overlap",
            masses=[_mass("community", "Community", [ground, upper])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = export_rhino(result, Path(tmp) / "l.3dm", story_height_ft=14)
            _, objects, _ = _objects(path)
            boxes = []
            for obj in objects:
                if obj.Attributes.GetUserString("role") != "program":
                    continue
                box = obj.Geometry.GetBoundingBox()
                boxes.append((obj.Attributes.Name, box))
            gyms = [b for name, b in boxes if "HEALTH" in name and b.Max.Z > 20]
            self.assertTrue(gyms)
            self.assertAlmostEqual(gyms[0].Max.Z, 28, places=2)
            self.assertAlmostEqual(gyms[0].Max.Y - gyms[0].Min.Y, 60, places=1)
            self.assertAlmostEqual(gyms[0].Max.X - gyms[0].Min.X, 100, places=1)
            for i, (name_a, a) in enumerate(boxes):
                for name_b, b in boxes[i + 1 :]:
                    overlap = (
                        min(a.Max.X, b.Max.X) - max(a.Min.X, b.Min.X) > 0.5
                        and min(a.Max.Y, b.Max.Y) - max(a.Min.Y, b.Min.Y) > 0.5
                        and min(a.Max.Z, b.Max.Z) - max(a.Min.Z, b.Min.Z) > 0.5
                    )
                    self.assertFalse(overlap, f"{name_a} overlaps {name_b}")
