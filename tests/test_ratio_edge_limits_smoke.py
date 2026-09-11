"""
Smoke tests: ratio-band + cross-mass edge-sum limitations.

Covers wording variants, briefing levers, number accounting, and solver checks.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from massing_explorer.brief import (
    _extract_edge_sum_limits,
    _extract_ratio_band,
    briefing_from_parsed,
    parse_brief,
)
from massing_explorer.modality import accounted_magnitudes, clause_was_captured, find_brief_questions
from massing_explorer.solver import check_edge_sum_limits, check_ratio_band


DEPTS = [
    "CORE ACADEMIC",
    "HEALTH & PHYSICAL EDUCATION",
    "DINING & FOOD SERVICE",
    "ADMINISTRATION & GUIDANCE",
    "ART & MUSIC",
]


def _mass(mid: str, name: str, length: float, width: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        name=name,
        floors=[SimpleNamespace(length_ft=length, width_ft=width, level=0)],
    )


class TestRatioBandWording(unittest.TestCase):
    CASES = [
        "all masses should be between 2:5 and 3:4",
        "All masses must stay between 2:5 and 3:4.",
        "each mass between 2:5 and 3:4",
        "mass ratio between 2:5 and 3:4",
        "proportion between 2:5 and 3:4 for every mass",
        "keep between 2:5 and 3:4",
        "remain between 2:5 and 3:4",
        "aspect ratio from 2:5 to 3:4",
        "from 2:5 to 3:4 ratio",
        "buildings between 2:5 and 3:4",
        "wings should be between 3:4 and 2:5",  # reversed order
        "Every mass aspect between 2:5 and 3:4 please.",
    ]

    def test_extracts_band_for_wording_variants(self) -> None:
        lo_want = 2.0 / 5.0
        hi_want = 3.0 / 4.0
        for text in self.CASES:
            with self.subTest(text=text):
                band = _extract_ratio_band(text)
                self.assertIsNotNone(band, msg=text)
                assert band is not None
                self.assertAlmostEqual(band["lo"], min(lo_want, hi_want), places=5)
                self.assertAlmostEqual(band["hi"], max(lo_want, hi_want), places=5)

    def test_parse_brief_and_briefing_limitation(self) -> None:
        text = "3 masses. All masses should be between 2:5 and 3:4. Max 3 floors."
        parsed = parse_brief(text, DEPTS)
        self.assertIn("ratio_band", parsed.constraints)
        lo, hi = parsed.constraints["ratio_band"]
        self.assertAlmostEqual(lo, 0.4, places=5)
        self.assertAlmostEqual(hi, 0.75, places=5)
        briefing = briefing_from_parsed(parsed)
        levers = {c["lever"] for c in briefing["limitations"]}
        self.assertIn("ratio_band", levers)
        clause = next(c for c in briefing["limitations"] if c["lever"] == "ratio_band")
        self.assertEqual(clause["kind"], "limitation")
        self.assertIn("2:5", clause["text"].replace(" ", ""))

    def test_numbers_accounted_no_ask(self) -> None:
        text = "all masses should be between 2:5 and 3:4"
        qs, parsed = find_brief_questions(text, use_llm=False, department_names=DEPTS)
        self.assertEqual(qs, [], msg=qs)
        self.assertTrue(clause_was_captured(text, parsed))
        known = accounted_magnitudes(parsed)
        self.assertTrue(any(abs(k - 2.0) < 0.01 for k in known))
        self.assertTrue(any(abs(k - 5.0) < 0.01 for k in known))
        self.assertTrue(any(abs(k - 3.0) < 0.01 for k in known))
        self.assertTrue(any(abs(k - 4.0) < 0.01 for k in known))

    def test_band_beats_single_prefer_ratio_in_same_clause(self) -> None:
        text = "all masses should be between 2:5 and 3:4"
        parsed = parse_brief(text, DEPTS)
        self.assertIn("ratio_band", parsed.constraints)
        # Midpoint soft target for sizing is fine; band is the hard limit.
        self.assertIsNotNone(parsed.length_over_width)


class TestRatioBandLogic(unittest.TestCase):
    def test_check_passes_inside_band(self) -> None:
        session = SimpleNamespace(constraints={"ratio_band": [0.4, 0.75]}, masses=[])
        # L/W = 60/100 = 0.6 inside [0.4, 0.75]
        result = SimpleNamespace(masses=[_mass("m1", "Mass 1", 60.0, 100.0)])
        checks = check_ratio_band(session, result)
        self.assertTrue(checks)
        self.assertTrue(all(c.passed for c in checks))

    def test_check_passes_transposed_aspect(self) -> None:
        # Band 2:5–5:8 → [0.4, 0.625]. L/W=1.787 fails directed, but
        # W/L≈0.56 is inside — same proportion, rotated axes.
        session = SimpleNamespace(constraints={"ratio_band": [0.4, 0.625]}, masses=[])
        result = SimpleNamespace(masses=[_mass("m4", "Mass 4", 178.7, 100.0)])
        checks = check_ratio_band(session, result)
        self.assertTrue(checks)
        self.assertTrue(checks[0].passed)
        self.assertIn("either way", checks[0].message)

    def test_single_prefer_ratio_transpose_is_zero_distance(self) -> None:
        from massing_explorer.aspect import aspect_target_distance
        from massing_explorer.explore.performance import preference_distance

        self.assertAlmostEqual(aspect_target_distance(5.0 / 3.0, 3.0 / 5.0), 0.0, places=5)
        session = SimpleNamespace(
            constraints={"length_over_width": 3.0 / 5.0},
            floor_pins={},
            masses=[],
        )
        result = SimpleNamespace(masses=[_mass("m1", "Mass 1", 100.0, 60.0)])  # 5:3
        self.assertAlmostEqual(preference_distance(result, session), 0.0, places=5)

    def test_check_fails_outside_band_both_ways(self) -> None:
        session = SimpleNamespace(constraints={"ratio_band": [0.4, 0.75]}, masses=[])
        # L/W = 200/50 = 4.0; transpose 0.25 — both outside [0.4, 0.75]
        result = SimpleNamespace(masses=[_mass("m1", "Mass 1", 200.0, 50.0)])
        checks = check_ratio_band(session, result)
        self.assertTrue(checks)
        self.assertFalse(checks[0].passed)
        self.assertTrue(str(checks[0].check).startswith("ratio_band:"))


class TestEdgeSumWording(unittest.TestCase):
    MIXED = [
        "long edge from mass A and short edge from mass B should be less than 200 ft",
        "long edge of mass 1 and short edge of mass 2 under 200 feet",
        "the long edge from mass one and the short edge from mass two must be below 200 ft",
        "mass A's long edge and mass B's short edge less than 200 ft",
        "mass 1's long edge and mass 2's short edge at most 200 ft",
        "short edge from mass B and long edge from mass A no more than 200 ft",
        "long edge of wing A and short edge of wing B should be under 60 meters",
    ]

    TOGETHER = [
        "long edges of mass A and B should be less than 300 ft together",
        "long edges of mass 1 and mass 2 under 300 feet",
        "the long edges of masses A and B must be below 300 ft together",
        "short edges of mass A and mass B together under 150 ft",
        "combined long edges of mass A and B less than 300 ft",
        "together long edges of wing one and wing two under 300 ft",
        "long edges of mass A and B should be less than 90 m together",
    ]

    def test_mixed_long_short_wording(self) -> None:
        for text in self.MIXED:
            with self.subTest(text=text):
                rules = _extract_edge_sum_limits(text)
                self.assertTrue(rules, msg=text)
                parts = {(p["ref"], p["edge"]) for p in rules[0]["parts"]}
                self.assertEqual(len(parts), 2)
                self.assertIn("long", {e for _, e in parts})
                self.assertIn("short", {e for _, e in parts})
                self.assertGreater(rules[0]["max_ft"], 100.0)

    def test_together_same_edge_wording(self) -> None:
        for text in self.TOGETHER:
            with self.subTest(text=text):
                rules = _extract_edge_sum_limits(text)
                self.assertTrue(rules, msg=text)
                edges = {p["edge"] for p in rules[0]["parts"]}
                self.assertEqual(len(edges), 1)
                self.assertGreater(rules[0]["max_ft"], 100.0)

    def test_parse_brief_stores_edge_sum_limits(self) -> None:
        text = (
            "4 masses. Long edge from mass A and short edge from mass B "
            "should be less than 200 ft. Long edges of mass A and B should be "
            "less than 300 together."
        )
        parsed = parse_brief(text, DEPTS)
        rules = parsed.constraints.get("edge_sum_limits") or []
        self.assertGreaterEqual(len(rules), 2)
        briefing = briefing_from_parsed(parsed)
        edge_clauses = [c for c in briefing["limitations"] if c["lever"] == "edge_sum"]
        self.assertGreaterEqual(len(edge_clauses), 2)

    def test_meters_convert_to_feet(self) -> None:
        rules = _extract_edge_sum_limits(
            "long edges of mass A and B less than 100 meters together"
        )
        self.assertTrue(rules)
        self.assertAlmostEqual(rules[0]["max_ft"], 100 * 3.280839895, delta=0.5)

    def test_numbers_accounted(self) -> None:
        text = "long edges of mass A and B should be less than 300 ft together"
        qs, parsed = find_brief_questions(text, use_llm=False, department_names=DEPTS)
        self.assertEqual(qs, [], msg=qs)
        self.assertTrue(clause_was_captured(text, parsed))


class TestEdgeSumLogic(unittest.TestCase):
    def _session(self, rules: list[dict]) -> SimpleNamespace:
        masses = [
            SimpleNamespace(id="m1", name="Mass 1", departments=[]),
            SimpleNamespace(id="m2", name="Mass 2", departments=[]),
        ]
        return SimpleNamespace(constraints={"edge_sum_limits": rules}, masses=masses)

    def test_long_plus_short_pass_and_fail(self) -> None:
        rules = [
            {
                "parts": [
                    {"ref": "1", "edge": "long"},
                    {"ref": "2", "edge": "short"},
                ],
                "max_ft": 200.0,
                "note": "test",
            }
        ]
        session = self._session(rules)
        # m1 120x40 → long 120; m2 90x50 → short 50; sum 170 <= 200
        ok_result = SimpleNamespace(
            masses=[_mass("m1", "Mass 1", 120.0, 40.0), _mass("m2", "Mass 2", 90.0, 50.0)]
        )
        checks = check_edge_sum_limits(session, ok_result)
        self.assertTrue(checks[0].passed)

        # sum 120+90 = 210 > 200
        bad_result = SimpleNamespace(
            masses=[_mass("m1", "Mass 1", 120.0, 40.0), _mass("m2", "Mass 2", 180.0, 90.0)]
        )
        checks = check_edge_sum_limits(session, bad_result)
        self.assertFalse(checks[0].passed)
        self.assertTrue(str(checks[0].check).startswith("edge_sum:"))

    def test_letter_refs_resolve_to_ordinals(self) -> None:
        rules = [
            {
                "parts": [
                    {"ref": "a", "edge": "long"},
                    {"ref": "b", "edge": "long"},
                ],
                "max_ft": 250.0,
                "note": "longs together",
            }
        ]
        session = self._session(rules)
        result = SimpleNamespace(
            masses=[_mass("m1", "Mass 1", 100.0, 40.0), _mass("m2", "Mass 2", 100.0, 50.0)]
        )
        checks = check_edge_sum_limits(session, result)
        self.assertTrue(checks[0].passed)

    def test_hard_gate_flag(self) -> None:
        from massing_explorer.explore.performance import _is_hard_gate_check

        self.assertTrue(_is_hard_gate_check("ratio_band:m1"))
        self.assertTrue(_is_hard_gate_check("edge_sum:0"))


class TestCombinedBriefSmoke(unittest.TestCase):
    def test_full_brief_with_new_limits(self) -> None:
        text = (
            "I want 3 masses, max 3 floors. "
            "All masses should be between 2:5 and 3:4. "
            "Long edge from mass A and short edge from mass B should be less than 220 ft. "
            "Long edges of mass A and B should be less than 360 ft together. "
            "Gym and dining together."
        )
        qs, parsed = find_brief_questions(text, use_llm=False, department_names=DEPTS)
        self.assertEqual(qs, [], msg=qs)
        self.assertIn("ratio_band", parsed.constraints)
        self.assertGreaterEqual(len(parsed.constraints.get("edge_sum_limits") or []), 2)
        briefing = briefing_from_parsed(parsed)
        levers = {c["lever"] for c in briefing["limitations"]}
        self.assertIn("ratio_band", levers)
        self.assertIn("edge_sum", levers)


if __name__ == "__main__":
    unittest.main()
