"""Expanding P pool: statuses, expansion, demand bias."""

from __future__ import annotations

import unittest
from unittest import mock

from massing_explorer.explore.p_pool import (
    STATUS_FEASIBLE,
    STATUS_IMPOSSIBLE,
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

    def test_fail_at_two_succeed_at_three_marks_feasible(self) -> None:
        """Same organization: 2-floor miss stays unresolved; 3-floor legal → feasible."""
        archive: dict = {}
        groups_2 = [
            {"id": "a", "name": "A", "departments": ["CORE ACADEMIC"], "story_count": 2},
            {"id": "b", "name": "B", "departments": ["ART & MUSIC"], "story_count": 2},
        ]
        groups_3 = [
            {"id": "a", "name": "A", "departments": ["CORE ACADEMIC"], "story_count": 3},
            {"id": "b", "name": "B", "departments": ["ART & MUSIC"], "story_count": 1},
        ]
        self.assertEqual(partition_key(groups_2), partition_key(groups_3))
        seed_p_pool(archive, [{"groups": groups_2}], source="initial")

        class _M:
            def __init__(self, mid: str, depts: list[str], stories: int) -> None:
                self.id = mid
                self.name = mid
                self.departments = depts
                self.story_count = stories

        class _S:
            constraints: dict = {"max_edge_ft": 196.85, "max_stories": 3}

            def __init__(self, stories: dict[str, int]) -> None:
                self.masses = [
                    _M("a", ["CORE ACADEMIC"], stories["a"]),
                    _M("b", ["ART & MUSIC"], stories["b"]),
                ]

        fail = record_p_outcome(
            archive,
            _S({"a": 2, "b": 2}),
            {"fits_limitations": False, "failed_kinds": ["site_length"]},
            groups=groups_2,
        )
        self.assertEqual(fail["status"], STATUS_UNRESOLVED)
        self.assertEqual(fail["attempts"], 1)

        ok = record_p_outcome(
            archive,
            _S({"a": 3, "b": 1}),
            {"fits_limitations": True, "program_coherence": 0.7},
            groups=groups_3,
        )
        self.assertEqual(ok["status"], STATUS_FEASIBLE)
        self.assertEqual(ok["legal_hits"], 1)
        self.assertEqual(ok["attempts"], 2)

    def test_feasible_not_downgraded_by_later_fail(self) -> None:
        archive: dict = {}
        groups = [
            {"id": "a", "name": "A", "departments": ["CORE ACADEMIC"], "story_count": 3},
        ]
        seed_p_pool(archive, [{"groups": groups}], source="initial")

        class _M:
            id = "a"
            name = "A"
            departments = ["CORE ACADEMIC"]
            story_count = 3

        class _S:
            masses = [_M()]
            constraints: dict = {}

        record_p_outcome(archive, _S(), {"fits_limitations": True})
        later = record_p_outcome(
            archive,
            _S(),
            {"fits_limitations": False, "failed_kinds": ["ratio_band"]},
        )
        self.assertEqual(later["status"], STATUS_FEASIBLE)
        self.assertEqual(later["legal_hits"], 1)
        self.assertEqual(later["attempts"], 2)
        self.assertIn("ratio_band", later["failure_kinds"])

    def test_legal_overrides_prior_impossible(self) -> None:
        archive: dict = {}
        groups = [
            {"id": "a", "name": "A", "departments": ["CORE ACADEMIC"], "story_count": 2},
        ]
        seed_p_pool(archive, [{"groups": groups}], source="initial")
        key = partition_key(groups)
        entry = archive["p_pool"]["entries"][key]
        entry["status"] = STATUS_IMPOSSIBLE
        entry["impossible_reason"] = "forced config miss"

        class _M:
            id = "a"
            name = "A"
            departments = ["CORE ACADEMIC"]
            story_count = 3

        class _S:
            masses = [_M()]
            constraints: dict = {}

        out = record_p_outcome(archive, _S(), {"fits_limitations": True})
        self.assertEqual(out["status"], STATUS_FEASIBLE)
        self.assertEqual(out["legal_hits"], 1)
        self.assertNotIn("impossible_reason", out)

    def test_structural_ignores_current_story_length_miss(self) -> None:
        """Plate/width/length at the current floor count is not org-impossible."""

        class _M:
            id = "gym"
            name = "Gym"
            departments = ["HEALTH & PHYSICAL EDUCATION"]
            story_count = 1

        class _S:
            masses = [_M()]
            constraints = {
                "max_edge_ft": 50.0,
                "department_required_width_ft": {
                    "HEALTH & PHYSICAL EDUCATION": 80.0,
                },
            }

        groups = [
            {
                "id": "gym",
                "departments": ["HEALTH & PHYSICAL EDUCATION"],
                "story_count": 1,
            }
        ]
        self.assertIsNone(structural_impossible(_S(), groups))


class ArchiveInsertSyncTests(unittest.TestCase):
    """Every stage insert must update P-pool; COVER must not double-count."""

    def _session(self, stories: int = 3):
        class _M:
            def __init__(self) -> None:
                self.id = "academic_support"
                self.name = "Academic Support"
                self.departments = ["CORE ACADEMIC"]
                self.story_count = stories

        class _S:
            masses = [_M()]
            constraints: dict = {"max_stories": 3}
            floor_pins: dict = {}
            pairings: list = []
            program = object()  # truthy so MCTS/BO would realize if called

        return _S()

    def test_insert_legal_marks_org_feasible(self) -> None:
        from massing_explorer.explore import archive as archive_mod
        from massing_explorer.explore.p_pool import (
            STATUS_FEASIBLE,
            ensure_p_pool,
            partition_key,
            seed_p_pool,
        )

        session = self._session()
        groups = [
            {
                "id": "academic_support",
                "name": "Academic Support",
                "departments": ["CORE ACADEMIC"],
                "story_count": 3,
            }
        ]
        archive = archive_mod.empty_archive()
        seed_p_pool(archive, [{"groups": groups}], source="initial")
        key = partition_key(groups)
        self.assertEqual(archive["p_pool"]["entries"][key]["status"], "unresolved")

        with mock.patch(
            "massing_explorer.explore.archive.cell_key", return_value="cell-legal"
        ), mock.patch(
            "massing_explorer.explore.archive.idea_key", return_value="idea"
        ), mock.patch(
            "massing_explorer.explore.archive.partition_id", return_value="part"
        ), mock.patch(
            "massing_explorer.explore.archive.read_strategy", return_value={}
        ), mock.patch(
            "massing_explorer.explore.archive._snapshot", return_value={}
        ), mock.patch(
            "massing_explorer.explore.archive._plates_from_result", return_value=[]
        ):
            archive_mod.insert(
                archive,
                session,
                result=None,
                performance={"fits_limitations": True, "program_coherence": 0.8},
                reason="mcts APPLY_PARTITION",
            )

        entry = ensure_p_pool(archive)["entries"][key]
        self.assertEqual(entry["status"], STATUS_FEASIBLE)
        self.assertEqual(entry["legal_hits"], 1)
        self.assertEqual(entry["attempts"], 1)

    def test_cover_evaluate_does_not_double_count_attempts(self) -> None:
        from massing_explorer.explore import archive as archive_mod
        from massing_explorer.explore.controller import _evaluate
        from massing_explorer.explore.p_pool import ensure_p_pool, partition_key, seed_p_pool

        session = self._session()
        groups = [
            {
                "id": "academic_support",
                "name": "Academic Support",
                "departments": ["CORE ACADEMIC"],
                "story_count": 3,
            }
        ]
        archive = archive_mod.empty_archive()
        seed_p_pool(archive, [{"groups": groups}], source="initial")
        key = partition_key(groups)

        with mock.patch(
            "massing_explorer.explore.controller.realize",
            return_value=(None, {"fits_limitations": True}),
        ), mock.patch(
            "massing_explorer.explore.archive.cell_key", return_value="cell-cover"
        ), mock.patch(
            "massing_explorer.explore.archive.idea_key", return_value="idea"
        ), mock.patch(
            "massing_explorer.explore.archive.partition_id", return_value="part"
        ), mock.patch(
            "massing_explorer.explore.archive.read_strategy", return_value={}
        ), mock.patch(
            "massing_explorer.explore.archive._snapshot", return_value={}
        ), mock.patch(
            "massing_explorer.explore.archive._plates_from_result", return_value=[]
        ):
            _evaluate(session, archive, "COVER start: P0")

        entry = ensure_p_pool(archive)["entries"][key]
        self.assertEqual(entry["attempts"], 1)
        self.assertEqual(entry["legal_hits"], 1)

    def test_each_stage_reason_marks_feasible(self) -> None:
        from massing_explorer.explore import archive as archive_mod
        from massing_explorer.explore.p_pool import (
            STATUS_FEASIBLE,
            ensure_p_pool,
            partition_key,
            seed_p_pool,
        )

        session = self._session()
        groups = [
            {
                "id": "academic_support",
                "name": "Academic Support",
                "departments": ["CORE ACADEMIC"],
                "story_count": 3,
            }
        ]
        reasons = (
            "COVER expand+10: P1",
            "mcts SET_STORIES academic_support 3fl",
            "COVER repair: stories+1",
            "bayes APPLY_PARTITION",
        )
        for i, reason in enumerate(reasons):
            archive = archive_mod.empty_archive()
            seed_p_pool(archive, [{"groups": groups}], source="initial")
            key = partition_key(groups)
            with mock.patch(
                "massing_explorer.explore.archive.cell_key", return_value=f"cell-{i}"
            ), mock.patch(
                "massing_explorer.explore.archive.idea_key", return_value="idea"
            ), mock.patch(
                "massing_explorer.explore.archive.partition_id", return_value="part"
            ), mock.patch(
                "massing_explorer.explore.archive.read_strategy", return_value={}
            ), mock.patch(
                "massing_explorer.explore.archive._snapshot", return_value={}
            ), mock.patch(
                "massing_explorer.explore.archive._plates_from_result", return_value=[]
            ):
                archive_mod.insert(
                    archive,
                    session,
                    result=None,
                    performance={"fits_limitations": True},
                    reason=reason,
                )
            entry = ensure_p_pool(archive)["entries"][key]
            self.assertEqual(
                entry["status"],
                STATUS_FEASIBLE,
                msg=f"{reason} should mark organization feasible",
            )
            self.assertEqual(entry["legal_hits"], 1)
            self.assertEqual(entry["attempts"], 1)
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

    def test_prefer_stories_raises_taller_patterns(self) -> None:
        patterns = [(1, 1, 1), (2, 2, 2), (3, 2, 1), (3, 3, 3), (1, 3, 2)]
        plain = bias_story_index_order(patterns, {"prefer_tall": 0.3})
        pref = bias_story_index_order(
            patterns, {"prefer_tall": 0.3}, preferred_stories=3
        )
        # Prefer-3 lifts all-3s above mid-height all-2s (plain ranks 2s first).
        self.assertLess(pref.index(3), pref.index(1))
        self.assertGreater(plain.index(3), plain.index(1))
        self.assertLess(pref.index(4), pref.index(0))

    def test_deepen_vs_expand_split(self) -> None:
        d, e = deepen_vs_expand_counts(10)
        self.assertEqual(d + e, 10)
        self.assertGreaterEqual(d, e)

    def test_deepen_bias_when_feasible(self) -> None:
        archive: dict = {
            "p_pool": {
                "entries": {
                    "k": {"status": "feasible", "groups": [], "depth_saturated": False}
                }
            }
        }
        d0, e0 = deepen_vs_expand_counts(10)
        d1, e1 = deepen_vs_expand_counts(10, archive)
        self.assertEqual(d1 + e1, 10)
        self.assertGreater(d1, d0)
        self.assertLess(e1, e0)

    def test_deepen_bias_relaxes_when_depth_saturated(self) -> None:
        from massing_explorer.explore.p_pool import (
            DEEPEN_FRAC,
            deepen_vs_expand_counts,
        )

        archive: dict = {
            "p_pool": {
                "entries": {
                    "k": {"status": "feasible", "groups": [], "depth_saturated": True}
                }
            }
        }
        d, e = deepen_vs_expand_counts(10, archive)
        self.assertEqual(d + e, 10)
        self.assertEqual(d, int(round(10 * DEEPEN_FRAC)))

    def test_depth_counts_distinct_configs_only(self) -> None:
        from massing_explorer.explore.p_pool import (
            _record_depth_progress,
            depth_config_key,
        )

        class _M:
            def __init__(self, mid: str, stories: int) -> None:
                self.id = mid
                self.story_count = stories
                self.name = mid
                self.departments = ["A"]

        class _S:
            def __init__(self) -> None:
                self.masses = [_M("a", 2), _M("b", 3)]
                self.constraints = {
                    "loading": "double",
                    "cover_envelope": "balanced",
                    "cover_plate_profile": "uniform",
                    "explore": {},
                }

        entry: dict = {"status": "feasible", "best_reward": 0.5}
        session = _S()
        # Same config twice → only first counts.
        _record_depth_progress(entry, session, {"fits_limitations": True})
        _record_depth_progress(entry, session, {"fits_limitations": True})
        self.assertEqual(len(entry["depth_configs"]), 1)
        self.assertEqual(entry.get("depth_legal_configs"), 1)
        # New distinct config without legal/quality → stale ticks.
        session.constraints["loading"] = "single"
        _record_depth_progress(entry, session, {"fits_limitations": False})
        session.constraints["cover_envelope"] = "compact"
        _record_depth_progress(entry, session, {"fits_limitations": False})
        session.constraints["cover_envelope"] = "elongated"
        _record_depth_progress(entry, session, {"fits_limitations": False})
        # 1 legal + 3 non-progress distinct → saturate (MIN=4 configs, STALE=3).
        self.assertTrue(entry.get("depth_saturated"))
        self.assertEqual(len(entry["depth_configs"]), 4)
        self.assertIn("Lsingle", depth_config_key(session))

    def test_allocate_cover_step_runs_discovery_beside_deepen(self) -> None:
        from massing_explorer.explore.p_pool import allocate_cover_step

        d, z, e = allocate_cover_step(12, None, discovery_needed=8, deepen_needed=3)
        self.assertEqual(d + z + e, 12)
        self.assertGreaterEqual(d, 1)
        self.assertGreaterEqual(z, 1)

    def test_probe_floor_and_pause(self) -> None:
        from massing_explorer.explore.p_pool import (
            PROBE_FLOOR,
            _record_probe_progress,
            p_needs_discovery_probe,
        )

        class _M:
            def __init__(self, mid: str) -> None:
                self.id = mid
                self.story_count = 2
                self.name = mid
                self.departments = ["A"]

        class _S:
            def __init__(self) -> None:
                self.masses = [_M("a"), _M("b")]
                self.constraints = {
                    "loading": "double",
                    "cover_envelope": "balanced",
                    "cover_plate_profile": "uniform",
                    "explore": {},
                }

        entry: dict = {"status": "unresolved"}
        session = _S()
        self.assertTrue(p_needs_discovery_probe(entry))
        # Flat high-distance probes fill the floor then pause.
        for i, env in enumerate(("balanced", "compact", "elongated", "balanced")):
            session.constraints["cover_envelope"] = env
            session.constraints["loading"] = "double" if i < 2 else "single"
            session.constraints["cover_plate_profile"] = "uniform" if i % 2 == 0 else "step"
            _record_probe_progress(
                entry,
                session,
                {"fits_limitations": False, "feasibility_distance": 0.9},
            )
        self.assertGreaterEqual(len(entry.get("probe_configs") or []), PROBE_FLOOR)
        self.assertTrue(entry.get("probe_paused"))
        self.assertFalse(p_needs_discovery_probe(entry))

    def test_balance_indices_by_mass_count(self) -> None:
        from massing_explorer.explore.p_pool import balance_indices_by_mass_count

        partitions = [
            {"groups": [{"departments": ["A"]}, {"departments": ["B"]}, {"departments": ["C"]}]},
            {"groups": [{"departments": ["A"]}, {"departments": ["B"]}, {"departments": ["C"]}, {"departments": ["D"]}]},
            {"groups": [{"departments": ["A"]}, {"departments": ["B"]}, {"departments": ["C"]}]},
            {"groups": [{"departments": ["A"]}, {"departments": ["B"]}, {"departments": ["C"]}, {"departments": ["D"]}]},
        ]

        class _S:
            constraints = {"p_constraints": {"mass_count_min": 3, "mass_count_max": 4}}

        out = balance_indices_by_mass_count([0, 1, 2, 3], partitions, _S())
        # Round-robin 3,4,3,4
        self.assertEqual(out[0], 0)
        self.assertEqual(out[1], 1)
        self.assertEqual(out[2], 2)
        self.assertEqual(out[3], 3)



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
