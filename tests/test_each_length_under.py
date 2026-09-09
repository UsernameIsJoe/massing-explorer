"""Per-mass length under N — digits and spelled; no height steal."""

from __future__ import annotations

import unittest

from massing_explorer.brief import parse_brief
from massing_explorer.modality import clause_was_captured, find_brief_questions


class EachLengthUnderTests(unittest.TestCase):
    def test_digit_meters(self) -> None:
        parsed = parse_brief("each length should be under 40 m", [])
        self.assertIn("max_building_length_ft", parsed.constraints)
        self.assertAlmostEqual(
            parsed.constraints["max_building_length_ft"],
            40 * 3.280839895,
            places=3,
        )
        self.assertNotIn("max_height_ft", parsed.constraints)

    def test_spelled_meters(self) -> None:
        parsed = parse_brief("each length should be under forty meters", [])
        self.assertIn("max_building_length_ft", parsed.constraints)
        self.assertAlmostEqual(
            parsed.constraints["max_building_length_ft"],
            40 * 3.280839895,
            places=3,
        )
        self.assertNotIn("max_height_ft", parsed.constraints)

    def test_lengths_stay_under(self) -> None:
        parsed = parse_brief("lengths should stay under 120 ft", [])
        self.assertAlmostEqual(parsed.constraints["max_building_length_ft"], 120.0)

    def test_captured_after_robust_read(self) -> None:
        text = "each length should be under 40 m"
        parsed = parse_brief(text, [])
        self.assertTrue(clause_was_captured(text, parsed))
        qs, _ = find_brief_questions(text, use_llm=False, department_names=[])
        self.assertEqual(qs, [])


if __name__ == "__main__":
    unittest.main()
