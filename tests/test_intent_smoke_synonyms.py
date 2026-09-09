"""Regression cases from synonyms / units / vertical-level smoke briefs."""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.brief import assign_open_departments, parse_brief
from massing_explorer.load import load_program_file

ROOT = Path(__file__).resolve().parents[1]
M = 3.280839895
SQM = 10.76391041671


class TestIntentSmokeSynonyms(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        program = load_program_file(
            ROOT / "examples" / "underwood_elementary_space_summary.xlsx",
            config_path=str(ROOT / "config" / "project.example.yaml"),
        )
        cls.names = [d.name for d in program.departments]

    def _parse(self, text: str):
        parsed = parse_brief(text, self.names)
        assign_open_departments(parsed)
        return parsed

    def test_same_one_and_around_three_stories(self) -> None:
        parsed = self._parse(
            "I want 4 separate buildings, with the gym and dining hall in the "
            "same one and the art spaces on the ground floor. The site should "
            "not stretch beyond 400 ft, and I’d prefer the buildings to stay "
            "around 3 stories if possible."
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertEqual(parsed.max_stories, 3)
        self.assertTrue(any("ART" in p for p in parsed.pin_ground))
        together = any(
            "HEALTH" in " ".join(ds).upper() and "DINING" in " ".join(ds).upper()
            for _, ds in parsed.named_masses
        )
        self.assertTrue(together)

    def test_boxes_layers_and_open_pct(self) -> None:
        parsed = self._parse(
            "Use four boxes, but they do not need to be the same size. At least "
            "25% of the site should remain unbuilt, and I’d rather keep the "
            "boxes to about three layers above grade."
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertEqual(parsed.max_stories, 3)
        self.assertEqual(parsed.constraints["min_open_space_pct"], 25.0)

    def test_g_plus_and_exception(self) -> None:
        parsed = self._parse(
            "Think of the project as four large boxes rather than one continuous "
            "building. Keep the gallery at grade, and make the boxes mostly "
            "ground + 2 with one allowed to reach ground + 4."
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertEqual(parsed.max_stories, 3)
        self.assertEqual(parsed.constraints["max_stories_exception"], 5.0)
        self.assertTrue(any("ART" in p for p in parsed.pin_ground))

    def test_storey_range_blocks(self) -> None:
        parsed = self._parse(
            "The campus should have six blocks. Most blocks should be 2–4 storeys."
        )
        self.assertEqual(parsed.mass_count, 6)
        self.assertEqual(parsed.constraints["stories_min"], 2.0)
        self.assertEqual(parsed.constraints["stories_max"], 4.0)
        self.assertEqual(parsed.max_stories, 4)

    def test_massing_pieces_range(self) -> None:
        parsed = self._parse(
            "Four or five massing pieces would both be acceptable, and do not "
            "go above 5 stories."
        )
        self.assertEqual(parsed.mass_count_min, 4)
        self.assertEqual(parsed.mass_count_max, 5)
        self.assertEqual(parsed.max_stories, 5)

    def test_bars_and_meter_length(self) -> None:
        parsed = self._parse(
            "Try a loose cluster of around five to eight bars. No bar should be "
            "longer than 55 m, and most of them should stay around three stories."
        )
        self.assertEqual(parsed.mass_count_min, 5)
        self.assertEqual(parsed.mass_count_max, 8)
        self.assertAlmostEqual(
            parsed.constraints["max_building_length_ft"], 55 * M, places=3
        )
        self.assertEqual(parsed.max_stories, 3)

    def test_first_floor_pin(self) -> None:
        parsed = self._parse(
            "Make 4 building forms with a combined area of roughly 180,000 sf. "
            "Art should stay on the first floor, and the full site length cannot "
            "exceed 400 feet."
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertTrue(any("ART" in p for p in parsed.pin_ground))
        self.assertAlmostEqual(parsed.constraints["max_total_length_ft"], 400.0)

    def test_dual_storey_blocks(self) -> None:
        parsed = self._parse(
            "I’d like four massing blocks and about 16,500 m² of total program. "
            "Keep the tallest one below 18 meters, and try to organize the scheme "
            "as two 3-storey blocks and two 4-storey blocks."
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertEqual(parsed.max_stories, 4)
        self.assertAlmostEqual(parsed.constraints["target_gfa_sf"], 16500 * SQM, places=0)

    def test_towers_podium_and_height(self) -> None:
        parsed = self._parse(
            "The design can have 3 towers on top of a shared low-rise base. Keep "
            "the podium to 3 floors, hold the tallest tower below 120 ft."
        )
        self.assertEqual(parsed.mass_count, 3)
        self.assertEqual(parsed.max_stories, 3)
        self.assertAlmostEqual(parsed.constraints["max_height_ft"], 120.0)

    def test_cap_project_sqm(self) -> None:
        parsed = self._parse(
            "I want 5 chunky masses. Cap the project at about 24,000 square "
            "meters, and keep every mass to 5 storeys or fewer."
        )
        self.assertEqual(parsed.mass_count, 5)
        self.assertEqual(parsed.max_stories, 5)
        self.assertAlmostEqual(parsed.constraints["max_gfa_sf"], 24000 * SQM, places=0)


if __name__ == "__main__":
    unittest.main()
