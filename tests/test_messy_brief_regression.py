"""Regression: messy typed brief still fills requirements / limitations / preferences."""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.brief import briefing_from_parsed, parse_brief
from massing_explorer.load import load_program_file


UNDERWOOD = Path(r"c:\Users\tu\Downloads\Underwood_Elementary_Space_Summary_TEST_Structured.xlsx")
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "underwood_elementary_space_summary.xlsx"


class MessyBriefRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = UNDERWOOD if UNDERWOOD.exists() else EXAMPLE
        cls.names = [d.name for d in load_program_file(str(path)).departments]

    def test_typos_ground_ratio_double_height(self) -> None:
        brief = (
            "3-4 masses, gyma nd dining together and double heights. "
            "art and music better on ground floor. all masses max story is 3, "
            "and mass ratio prefer to be 3:5"
        )
        parsed = parse_brief(brief, self.names)
        self.assertEqual(parsed.mass_count_min, 3)
        self.assertEqual(parsed.mass_count_max, 4)
        self.assertTrue(
            any("HEALTH" in a and "DINING" in b for a, b in parsed.keep_together)
            or any("DINING" in a and "HEALTH" in b for a, b in parsed.keep_together)
        )
        self.assertTrue(any("HEALTH" in d for d in parsed.double_height_departments))
        self.assertTrue(any("DINING" in d for d in parsed.double_height_departments))
        self.assertTrue(any("ART" in p for p in parsed.pin_ground))
        self.assertEqual(parsed.max_stories, 3)
        self.assertEqual(parsed.constraints.get("preferred_ratio"), [3.0, 5.0])

        briefing = briefing_from_parsed(parsed)
        levers_req = {c["lever"] for c in briefing["requirements"]}
        levers_pref = {c["lever"] for c in briefing["preferences"]}
        self.assertIn("mass_count", levers_req)
        self.assertIn("keep_together", levers_req)
        self.assertIn("double_height", levers_req)
        self.assertIn("pin_ground", levers_pref)
        self.assertIn("ratio", levers_pref)
        self.assertTrue(any(c.get("text") == "3:5" for c in briefing["preferences"] if c["lever"] == "ratio"))

    def test_required_mass_count_accepts_range_list(self) -> None:
        from types import SimpleNamespace
        from massing_explorer.explore.strategy import required_mass_count

        session = SimpleNamespace(
            constraints={
                "briefing": {
                    "requirements": [
                        {"lever": "mass_count", "value": [3, 4], "text": "range"}
                    ]
                }
            }
        )
        self.assertEqual(required_mass_count(session), 4)


if __name__ == "__main__":
    unittest.main()
