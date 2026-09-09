"""Regression for compact 4–6 volumes / footprint / story-exception brief."""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.brief import assign_open_departments, parse_brief
from massing_explorer.load import load_program_file

ROOT = Path(__file__).resolve().parents[1]
SQM = 10.76391041671

BRIEF = (
    "Please keep the scheme compact without making it dense-looking: maybe 4 to 6 volumes, "
    "definitely not fewer than four, and only use six if that gives you noticeably more open space. "
    "The art studios need north light, the auditorium must have direct ground-floor access, and the "
    "gym should be near dining but not share the same entrance; administration can go almost anywhere "
    "except the quiet residential edge. I’m targeting about 22,000 sqm total, no single footprint "
    "should exceed 75,000 sq ft, and most of the massing should stay at three storeys with one building "
    "allowed to go up another two levels."
)


class TestCompactVolumesBrief(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        program = load_program_file(
            ROOT / "examples" / "underwood_elementary_space_summary.xlsx",
            config_path=str(ROOT / "config" / "project.example.yaml"),
        )
        cls.names = [d.name for d in program.departments]

    def test_compact_brief_intents(self) -> None:
        parsed = parse_brief(BRIEF, self.names)
        assign_open_departments(parsed)

        self.assertEqual(parsed.mass_count_min, 4)
        self.assertEqual(parsed.mass_count_max, 6)
        self.assertEqual(parsed.preference, "compact")
        self.assertEqual(parsed.max_stories, 3)
        self.assertEqual(parsed.constraints["max_stories_exception"], 5.0)
        self.assertAlmostEqual(parsed.constraints["target_gfa_sf"], 22000 * SQM, places=0)
        self.assertAlmostEqual(parsed.constraints["max_footprint_sf"], 75000.0)

        keep = {" ".join(sorted(p)).upper() for p in parsed.keep_together}
        self.assertTrue(any("HEALTH" in k and "DINING" in k for k in keep))
        self.assertIn("auditorium", parsed.unknown_programs)
        self.assertTrue(any("north light" in n for n in parsed.notes))
        self.assertTrue(any("same entrance" in n for n in parsed.notes))
        self.assertTrue(any("residential edge" in n for n in parsed.notes))
        self.assertTrue(any("use 6 only" in n for n in parsed.notes))


if __name__ == "__main__":
    unittest.main()
