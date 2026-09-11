"""
Smoke: user-style brief wording that previously mis-parsed.

Covers:
- length max N, min M (comma / gap, not only 'and')
- prefer ratio band → preference (not hard limitation)
- media prefer top floor above admin → pin_floor + stack_above
"""

from __future__ import annotations

import unittest

from massing_explorer.brief import briefing_from_parsed, parse_brief
from massing_explorer.modality import find_brief_questions, unaccounted_numbers


DEPTS = [
    "CORE ACADEMIC",
    "SPECIAL EDUCATION",
    "HEALTH & PHYSICAL EDUCATION",
    "DINING & FOOD SERVICE",
    "ART & MUSIC",
    "MEDIA CENTER",
    "ADMINISTRATION & GUIDANCE",
    "CUSTODIAL & MAINTENANCE",
    "MEDICAL",
]

USER_BRIEF = (
    "4 masses, max 3 floors. length max 70 meters, min 10 meters . "
    "gym and dining together and double height. art and music prefer on ground floor. "
    "media prefer on top floor above admin. admin have to be on ground floor. "
    "core academic and special ed width has to be 100 feet. "
    "mass ratio prefer to be between 5:8 and 2:5. prefer 3 floors."
)


def _levers(briefing: dict, bucket: str) -> set[str]:
    return {str(c.get("lever") or "") for c in briefing.get(bucket) or []}


class TestUserBriefRegression(unittest.TestCase):
    def test_canonical_user_brief(self) -> None:
        qs, parsed = find_brief_questions(USER_BRIEF, use_llm=False, department_names=DEPTS)
        self.assertEqual(qs, [], msg=qs)
        briefing = briefing_from_parsed(parsed)

        # min length from "length max 70 meters, min 10 meters"
        self.assertIsNotNone(parsed.constraints.get("min_edge_ft"))
        self.assertAlmostEqual(
            float(parsed.constraints["min_edge_ft"]), 10 * 3.280839895, places=2
        )
        self.assertIn("min_edge", _levers(briefing, "limitations"))

        # prefer ratio band → preferences, not hard limitation
        self.assertEqual(parsed.constraints.get("ratio_band_role"), "preference")
        self.assertIn("ratio_band", _levers(briefing, "preferences"))
        self.assertNotIn("ratio_band", _levers(briefing, "limitations"))

        # media top above admin
        pins = parsed.constraints.get("floor_pins") or {}
        self.assertIn("MEDIA CENTER", pins)
        self.assertEqual(int(pins["MEDIA CENTER"]), -1)
        stacks = parsed.constraints.get("stack_above") or []
        self.assertTrue(
            any(
                s.get("above") == "MEDIA CENTER"
                and s.get("below") == "ADMINISTRATION & GUIDANCE"
                for s in stacks
            ),
            msg=stacks,
        )
        self.assertIn("pin_floor", _levers(briefing, "preferences"))
        self.assertIn("stack_above", _levers(briefing, "preferences"))

        # admin still ground requirement; art ground preference
        self.assertIn("ADMINISTRATION & GUIDANCE", parsed.pin_ground)
        self.assertEqual(
            (parsed.constraints.get("pin_ground_kind") or {}).get(
                "ADMINISTRATION & GUIDANCE"
            ),
            "requirement",
        )
        self.assertIn("ART & MUSIC", parsed.pin_ground)

    def test_min_length_wording_variants(self) -> None:
        cases = [
            "length max 70 meters, min 10 meters",
            "length max 70 meters; min 10 meters",
            "length max 70 meters and min 10 meters",
            "length maximum 40 m, minimum 12 m",
            "lengths max 200 ft, min 40 ft",
            "length max 70 meters. min 10 meters",
        ]
        for text in cases:
            with self.subTest(text=text):
                parsed = parse_brief(text, DEPTS)
                self.assertIsNotNone(parsed.constraints.get("min_edge_ft"), msg=text)
                self.assertGreater(float(parsed.constraints["min_edge_ft"]), 20.0)

    def test_prefer_ratio_band_wording_variants(self) -> None:
        cases = [
            "mass ratio prefer to be between 5:8 and 2:5",
            "prefer mass ratio between 2:5 and 5:8",
            "preferably the mass proportion between 3:4 and 1:2",
            "I'd prefer aspect ratio between 2:5 and 5:8",
            "mass ratio preferably between 5:8 and 2:5",
        ]
        for text in cases:
            with self.subTest(text=text):
                parsed = parse_brief(text, DEPTS)
                briefing = briefing_from_parsed(parsed)
                self.assertEqual(parsed.constraints.get("ratio_band_role"), "preference")
                self.assertIn("ratio_band", _levers(briefing, "preferences"))
                self.assertNotIn("ratio_band", _levers(briefing, "limitations"))

    def test_hard_ratio_band_still_limitation(self) -> None:
        text = "all masses should be between 2:5 and 3:4"
        parsed = parse_brief(text, DEPTS)
        briefing = briefing_from_parsed(parsed)
        self.assertEqual(parsed.constraints.get("ratio_band_role"), "limitation")
        self.assertIn("ratio_band", _levers(briefing, "limitations"))

    def test_top_floor_above_wording_variants(self) -> None:
        cases = [
            "media prefer on top floor above admin",
            "media preferably on the top floor above administration",
            "Media should sit on the uppermost floor above admin.",
            "prefer media on top floor above admin",
            "media must be on the highest floor above administration",
            "media should go above admin",
            "media prefer above administration",
        ]
        for text in cases:
            with self.subTest(text=text):
                parsed = parse_brief(text, DEPTS)
                stacks = parsed.constraints.get("stack_above") or []
                self.assertTrue(
                    any(
                        s.get("above") == "MEDIA CENTER"
                        and "ADMIN" in str(s.get("below") or "").upper()
                        for s in stacks
                    ),
                    msg=(text, stacks, parsed.constraints.get("floor_pins")),
                )

    def test_numbers_accounted_for_comma_min(self) -> None:
        text = "length max 70 meters, min 10 meters"
        parsed = parse_brief(text, DEPTS)
        self.assertEqual(unaccounted_numbers(text, parsed), [])


if __name__ == "__main__":
    unittest.main()
