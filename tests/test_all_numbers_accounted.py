"""Every stated number (digit or spelled) must be accounted for."""

from __future__ import annotations

import unittest

from massing_explorer.brief import ParsedBrief, parse_brief
from massing_explorer.modality import (
    apply_content_interpretation,
    clause_was_captured,
    find_brief_questions,
    stated_numbers,
    unaccounted_numbers,
)


class AllNumbersAccountedTests(unittest.TestCase):
    def test_stated_digit_and_spelled(self) -> None:
        nums = stated_numbers("each length under forty meters and prefer 3 stories")
        values = {round(n["value"], 3) for n in nums}
        self.assertIn(40.0, values)
        self.assertIn(3.0, values)

    def test_skip_mass_labels(self) -> None:
        nums = stated_numbers("mass 1 is gym; mass two is dining under 200 ft")
        values = {round(n["value"], 3) for n in nums}
        self.assertIn(200.0, values)
        # labels 1 / two should not appear as free sizes
        self.assertNotIn(1.0, values)
        self.assertNotIn(2.0, values)

    def test_partial_capture_is_not_enough(self) -> None:
        # Only length lands; story number must still be flagged.
        text = "each mass no more than 3 stories and no longer than 210 ft"
        parsed = ParsedBrief(text=text)
        parsed.constraints["max_building_length_ft"] = 210.0
        parsed.constraints["length_limit_is_cap"] = 1
        missed = unaccounted_numbers(text, parsed)
        self.assertTrue(any(abs(m["value"] - 3) < 0.01 for m in missed))
        self.assertFalse(clause_was_captured(text, parsed))

    def test_full_brief_both_numbers(self) -> None:
        text = "each mass can be no more than 3 stories and no longer than 210 ft"
        parsed = parse_brief(text, [])
        self.assertTrue(clause_was_captured(text, parsed), parsed.constraints)
        qs, _ = find_brief_questions(text, use_llm=False, department_names=[])
        self.assertEqual(qs, [])

    def test_unplaced_asks_without_llm(self) -> None:
        # Nonsense size cue the robust parser will miss.
        text = "keep the frobulator under 77 zorks beside the mass length"
        qs, parsed = find_brief_questions(text, use_llm=False, department_names=[])
        # Must ask (modality and/or unresolved) — never silently ignore 77.
        self.assertFalse(clause_was_captured(text, parsed))
        self.assertTrue(qs)
        self.assertTrue(
            any(q.get("reason") in {"modality", "unresolved"} for q in qs)
        )
        # After a modality answer, number placement must still be demanded.
        qs2, parsed2 = find_brief_questions(
            text,
            answers=[{"text": text, "kind": "limitation"}],
            use_llm=False,
            department_names=[],
        )
        self.assertTrue(qs2)
        self.assertTrue(any(q.get("reason") == "unresolved" for q in qs2))
        self.assertIn("77", (qs2[0].get("unplaced_numbers") or qs2[0].get("rationale") or ""))

    def test_apply_multi_items(self) -> None:
        parsed = ParsedBrief(text="")
        ok = apply_content_interpretation(
            parsed,
            {
                "kind": "limitation",
                "items": [
                    {"lever": "max_stories", "value": 3, "unit": "stories"},
                    {"lever": "max_length", "value": "forty", "unit": "m"},
                ],
            },
        )
        self.assertTrue(ok)
        self.assertEqual(parsed.max_stories, 3)
        self.assertAlmostEqual(
            parsed.constraints["max_building_length_ft"], 40 * 3.280839895, places=3
        )


if __name__ == "__main__":
    unittest.main()
