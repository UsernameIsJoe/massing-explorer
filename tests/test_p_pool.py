"""Expanding P pool: statuses, expansion, demand bias."""

from __future__ import annotations

import unittest

from massing_explorer.explore.p_pool import (
    STATUS_FEASIBLE,
    STATUS_UNRESOLVED,
    bias_story_index_order,
    deepen_vs_expand_counts,
    demand_profile,
    partition_key,
    record_p_outcome,
    seed_p_pool,
    should_expand_p_pool,
    structural_impossible,
)


class PartitionKeyTests(unittest.TestCase):
    def test_stable_key(self) -> None:
        a = [
            {"id": "m0", "departments": ["B", "A"], "story_count": 2},
            {"id": "m1", "departments": ["C"], "story_count": 2},
        ]
        b = [
            {"id": "x", "departments": ["C"], "story_count": 3},
            {"id": "y", "departments": ["A", "B"], "story_count": 1},
        ]
        self.assertEqual(partition_key(a), partition_key(b))


class StatusTests(unittest.TestCase):
    def test_failed_realize_stays_unresolved(self) -> None:
        archive: dict = {}
        groups = [
            {"id": "a", "name": "A", "departments": ["CORE ACADEMIC"], "story_count": 2},
            {
                "id": "b",
                "name": "B",
                "departments": ["HEALTH & PHYSICAL EDUCATION"],
                "story_count": 2,
            },
        ]
        seed_p_pool(archive, [{"groups": groups, "reason": "t"}], source="initial")

        class _S:
            masses = []
            constraints = {}

        class _M:
            id = "a"
            name = "A"
            departments = ["CORE ACADEMIC"]
            story_count = 2

        class _M2:
            id = "b"
            name = "B"
            departments = ["HEALTH & PHYSICAL EDUCATION"]
            story_count = 2

        session = _S()
        session.masses = [_M(), _M2()]
        entry = record_p_outcome(
            archive,
            session,
            {"fits_limitations": False, "failed_kinds": ["site_total_length"]},
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], STATUS_UNRESOLVED)
        self.assertEqual(entry["attempts"], 1)
        self.assertIn("site_total_length", entry["failure_kinds"])

    def test_legal_marks_feasible(self) -> None:
        archive: dict = {}
        groups = [
            {"id": "a", "name": "A", "departments": ["CORE ACADEMIC"], "story_count": 2},
        ]
        seed_p_pool(archive, [{"groups": groups}], source="initial")

        class _M:
            id = "a"
            name = "A"
            departments = ["CORE ACADEMIC"]
            story_count = 2

        class _S:
            masses = [_M()]
            constraints = {}

        entry = record_p_outcome(
            archive, _S(), {"fits_limitations": True, "program_coherence": 0.8}
        )
        self.assertEqual(entry["status"], STATUS_FEASIBLE)
        self.assertEqual(entry["legal_hits"], 1)

    def test_mass_count_out_of_bounds_is_impossible(self) -> None:
        class _S:
            constraints = {
                "p_constraints": {"mass_count_min": 3, "mass_count_max": 3},
            }
            masses = []

        groups = [
            {"id": "a", "departments": ["A"], "story_count": 2},
            {"id": "b", "departments": ["B"], "story_count": 2},
        ]
        reason = structural_impossible(_S(), groups)
        self.assertIsNotNone(reason)
        self.assertIn("|P|", reason or "")


class DemandBiasTests(unittest.TestCase):
    def test_overloaded_prefers_taller_patterns(self) -> None:
        overloaded = demand_profile(
            [
                {
                    "departments": [
                        "HEALTH & PHYSICAL EDUCATION",
                        "DINING & FOOD SERVICE",
                        "MEDIA CENTER",
                        "ADMINISTRATION",
                    ]
                },
                {"departments": ["CORE ACADEMIC"]},
            ]
        )
        balanced = demand_profile(
            [
                {"departments": ["CORE ACADEMIC", "SPECIAL EDUCATION"]},
                {"departments": ["ART & MUSIC", "MEDIA CENTER"]},
            ]
        )
        self.assertGreater(overloaded["ground_pressure"], balanced["ground_pressure"])
        patterns = [(1, 1), (2, 2), (3, 2), (4, 3)]
        order = bias_story_index_order(patterns, overloaded)
        # First pick should be among the taller patterns.
        self.assertIn(order[0], (2, 3))

    def test_deepen_vs_expand_split(self) -> None:
        d, e = deepen_vs_expand_counts(10)
        self.assertEqual(d + e, 10)
        self.assertGreaterEqual(d, e)


class ExpandTriggerTests(unittest.TestCase):
    def test_gap_in_mass_count_triggers_expand(self) -> None:
        archive: dict = {}
        # Only |P|=2 seeded while bounds allow 2–3.
        seed_p_pool(
            archive,
            [
                {
                    "groups": [
                        {"id": "a", "departments": ["A"], "story_count": 2},
                        {"id": "b", "departments": ["B"], "story_count": 2},
                    ]
                }
            ],
            source="initial",
        )
        # Mark attempts so starved/gap logic can fire via gap.
        for e in archive["p_pool"]["entries"].values():
            e["attempts"] = 2
            e["ground_pressure"] = 0.7

        class _S:
            constraints = {
                "p_constraints": {"mass_count_min": 2, "mass_count_max": 3},
            }
            masses = []

        self.assertTrue(should_expand_p_pool(archive, _S()))


if __name__ == "__main__":
    unittest.main()
