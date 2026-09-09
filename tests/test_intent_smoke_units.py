"""Regression cases from the units/numbers intent smoke brief."""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.brief import assign_open_departments, parse_brief
from massing_explorer.load import load_program_file

ROOT = Path(__file__).resolve().parents[1]
M = 3.280839895
SQM = 10.76391041671


class TestIntentSmokeUnits(unittest.TestCase):
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

    def test_gym_dining_same_building_art_ground_site_ft(self) -> None:
        parsed = self._parse(
            "I want 4 separate masses, with the gym and dining hall in the same "
            "building and the art spaces on the ground floor. The site should "
            "not stretch beyond 400 ft, and I’d prefer the overall scheme to "
            "stay fairly low-rise if possible."
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertEqual(parsed.preference, "low_rise")
        self.assertAlmostEqual(parsed.constraints["max_total_length_ft"], 400.0)
        self.assertTrue(any("ART" in p for p in parsed.pin_ground))
        blob = " ".join(d for _, ds in parsed.named_masses for d in ds).upper()
        self.assertIn("HEALTH", blob)
        self.assertIn("DINING", blob)
        together = any(
            "HEALTH" in " ".join(ds).upper() and "DINING" in " ".join(ds).upper()
            for _, ds in parsed.named_masses
        ) or any(
            "HEALTH" in f"{a} {b}".upper() and "DINING" in f"{a} {b}".upper()
            for a, b in parsed.keep_together
        )
        self.assertTrue(together)

    def test_word_height_and_three_buildings(self) -> None:
        parsed = self._parse(
            "Please use three main buildings and keep the library near the "
            "primary entrance, with the café close to it. Nothing should exceed "
            "sixty feet in height."
        )
        self.assertEqual(parsed.mass_count, 3)
        self.assertAlmostEqual(parsed.constraints["max_height_ft"], 60.0)

    def test_meters_width_and_stories(self) -> None:
        parsed = self._parse(
            "I’m imagining 5 masses. Make sure the site is no wider than 90 m, "
            "and try to keep most of the project to three stories."
        )
        self.assertEqual(parsed.mass_count, 5)
        self.assertEqual(parsed.max_stories, 3)
        self.assertAlmostEqual(
            parsed.constraints["max_building_width_ft"], 90 * M, places=3
        )

    def test_mass_range_and_soft_stories(self) -> None:
        parsed = self._parse(
            "I’d rather have 2 or 3 larger buildings than a lot of small ones, "
            "and try to stay low-rise even though one building could go up to "
            "6 stories if needed."
        )
        self.assertEqual(parsed.mass_count_min, 2)
        self.assertEqual(parsed.mass_count_max, 3)
        self.assertEqual(parsed.max_stories, 6)
        self.assertEqual(parsed.preference, "low_rise")

    def test_prime_feet_and_sqm_gfa(self) -> None:
        parsed = self._parse(
            "The scheme needs exactly 5 masses. The site depth must stay under "
            "420', and total GFA should stay under 28,000 sqm, and the tallest "
            "point must stay below sixty-five feet."
        )
        self.assertEqual(parsed.mass_count, 5)
        self.assertAlmostEqual(parsed.constraints["max_total_length_ft"], 420.0)
        self.assertAlmostEqual(parsed.constraints["max_gfa_sf"], 28000 * SQM, places=0)
        self.assertAlmostEqual(parsed.constraints["max_height_ft"], 65.0)

    def test_main_masses_plus_pavilion(self) -> None:
        parsed = self._parse(
            "I want four main masses arranged around a rectangular lawn, plus 1 "
            "smaller pavilion near the entrance."
        )
        self.assertEqual(parsed.mass_count, 5)

    def test_main_masses_and_standalone_pavilion(self) -> None:
        parsed = self._parse(
            "Please use three main masses and one smaller standalone pavilion "
            "for art exhibitions. The gym and dining should share the largest "
            "building, that mass should stay under 220 feet long."
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertAlmostEqual(parsed.constraints["max_building_length_ft"], 220.0)

    def test_m2_program_and_meter_height(self) -> None:
        parsed = self._parse(
            "I’d like four masses and about 16,500 m² of total program. Keep "
            "the tallest one below 18 meters."
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertAlmostEqual(parsed.constraints["target_gfa_sf"], 16500 * SQM, places=0)
        self.assertAlmostEqual(parsed.constraints["max_height_ft"], 18 * M, places=3)

    def test_short_tokens_do_not_false_match(self) -> None:
        from massing_explorer.group import match_department

        self.assertIsNone(match_department("i", self.names))
        self.assertIsNone(match_department("and", self.names))
        self.assertIsNone(match_department("want", self.names))
        self.assertEqual(match_department("art spaces", self.names), "ART & MUSIC")
        self.assertEqual(
            match_department("gym", self.names), "HEALTH & PHYSICAL EDUCATION"
        )


if __name__ == "__main__":
    unittest.main()
