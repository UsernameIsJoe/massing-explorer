"""Regression for the urban-village mixed-intent brief."""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.brief import assign_open_departments, parse_brief
from massing_explorer.load import load_program_file

ROOT = Path(__file__).resolve().parents[1]
M = 3.280839895

BRIEF = (
    "Think of this as a little urban village rather than a campus: 7 or 8 structures "
    "would be nice, though I care more about keeping the pedestrian route clear than "
    "hitting that number exactly. Food, student life, and fitness should form a loose "
    "cluster, but the service/loading area must not touch the main plaza, and the "
    "gallery should be visible from the entrance even if it is not the closest "
    "building; housing should be quieter and preferably farther south. The site is "
    "about 130 m by 360 ft, at least one-third should remain unbuilt, no occupied "
    "layer should rise above level 5, and if you can keep most boxes to only 2–3 "
    "floors without sacrificing the required program area, do that."
)


class TestUrbanVillageBrief(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        program = load_program_file(
            ROOT / "examples" / "underwood_elementary_space_summary.xlsx",
            config_path=str(ROOT / "config" / "project.example.yaml"),
        )
        cls.names = [d.name for d in program.departments]

    def test_site_level_cluster_and_preferences(self) -> None:
        parsed = parse_brief(BRIEF, self.names)
        assign_open_departments(parsed)

        # Site envelope 130 m × 360 ft → longer side is length
        self.assertAlmostEqual(
            parsed.constraints["max_total_length_ft"], 130 * M, places=2
        )
        self.assertAlmostEqual(parsed.constraints["max_building_width_ft"], 360.0)
        self.assertAlmostEqual(parsed.constraints["min_open_space_pct"], 100 / 3, places=1)

        # Hard level-5 cap; soft 2–3 preference does not override it
        self.assertEqual(parsed.max_stories, 5)
        self.assertEqual(parsed.constraints["hard_max_stories"], 5.0)
        self.assertEqual(parsed.constraints["stories_min"], 2.0)
        self.assertEqual(parsed.constraints["stories_max"], 3.0)

        self.assertEqual(parsed.mass_count_min, 7)
        self.assertEqual(parsed.mass_count_max, 8)
        self.assertEqual(parsed.preference, "low_rise")

        cluster = {d for pair in parsed.keep_together for d in pair}
        self.assertIn("DINING & FOOD SERVICE", cluster)
        self.assertIn("HEALTH & PHYSICAL EDUCATION", cluster)
        self.assertTrue(any("ADMINISTRATION" in d for d in cluster))

        self.assertTrue(
            any("CUSTODIAL" in a or "CUSTODIAL" in b for a, b in parsed.keep_apart)
            or any("plaza" in n for n in parsed.notes)
        )
        self.assertTrue(any("visible from the entrance" in n for n in parsed.notes))
        self.assertTrue(any("farther south" in n for n in parsed.notes))
        self.assertIn("housing", parsed.unknown_programs)


if __name__ == "__main__":
    unittest.main()
