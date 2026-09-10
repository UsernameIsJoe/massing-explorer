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

    def test_height_is_not_the_number_eight(self) -> None:
        nums = stated_numbers("gym and dining together and double height")
        self.assertFalse(nums)

    def test_meters_not_swallowed_by_ratio_parts(self) -> None:
        text = (
            "3 masses, max 3 floors. length max 40 meters. "
            "mass ratio prefer to be 4:7"
        )
        parsed = parse_brief(text, [])
        self.assertAlmostEqual(
            parsed.constraints.get("max_building_length_ft") or 0,
            40 * 3.280839895,
            places=3,
        )
        missed = unaccounted_numbers("length max 40 meters", parsed)
        self.assertEqual(missed, [])
        qs, _ = find_brief_questions(text, use_llm=False, department_names=[])
        self.assertFalse(any("eight" in (q.get("unplaced_numbers") or "") for q in qs))

    def test_partial_capture_is_not_enough(self) -> None:
        # Only length lands; story number must still be flagged.
        text = "each mass no more than 3 stories and no longer than 210 ft"
        parsed = ParsedBrief(text=text)
        parsed.constraints["max_building_length_ft"] = 210.0
        parsed.constraints["length_limit_is_cap"] = 1
        missed = unaccounted_numbers(text, parsed)
        self.assertTrue(any(abs(m["value"] - 3) < 0.01 for m in missed))
        self.assertFalse(clause_was_captured(text, parsed))

    def test_max_min_meters_and_prefer_story_range_do_not_reask(self) -> None:
        """min edge + prefer 2-3 floors are parsed; do not block generate."""
        text = (
            "3 masses, max 3 floors. length max 70 meters and min 20 meters. "
            "Prefer 2-3 floors. Prefer mass ratio 4:7."
        )
        parsed = parse_brief(text, [])
        self.assertIsNotNone(parsed.constraints.get("min_edge_ft"))
        self.assertAlmostEqual(
            float(parsed.constraints["min_edge_ft"]),
            20 * 3.280839895,
            places=2,
        )
        self.assertEqual(parsed.constraints.get("stories_min"), 2.0)
        self.assertEqual(parsed.constraints.get("stories_max"), 3.0)
        self.assertEqual(
            unaccounted_numbers("length max 70 meters and min 20 meters", parsed),
            [],
        )
        self.assertEqual(unaccounted_numbers("Prefer 2-3 floors", parsed), [])
        nums = stated_numbers("Prefer 2-3 floors")
        self.assertTrue(all(n["kind"] == "stories" for n in nums))
        qs, _ = find_brief_questions(text, use_llm=False, department_names=[])
        self.assertFalse(
            any("20" in (q.get("unplaced_numbers") or "") for q in qs),
            qs,
        )
        self.assertFalse(
            any(
                "prefer 2-3" in (q.get("text") or "").lower()
                or "2-3 floors" in (q.get("text") or "").lower()
                for q in qs
            ),
            qs,
        )

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
        # After a modality answer, do not nag the same clause again.
        qs2, parsed2 = find_brief_questions(
            text,
            answers=[{"text": text, "kind": "limitation"}],
            use_llm=False,
            department_names=[],
        )
        self.assertFalse(any((q.get("text") or "") == text for q in qs2))
        qs3, _ = find_brief_questions(
            text,
            answers=[{"text": text, "kind": "limitation", "reason": "unresolved"}],
            use_llm=False,
            department_names=[],
        )
        self.assertEqual(qs3, [])

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
