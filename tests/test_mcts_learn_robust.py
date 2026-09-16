"""MCTS deepen / PUCT, LEARN refine loop, robustness shortlist probes."""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from massing_explorer.explore.mcts import (
    _Node,
    _balance_action_categories,
    _planner_prior,
    _puct,
    _select_child,
)
from massing_explorer.explore.performance import _robustness_score
from massing_explorer.explore.robustness import attach_probe_to_entry


class PuctDeepenTests(unittest.TestCase):
    def test_visited_strong_child_can_beat_unvisited(self) -> None:
        """Without +1000 lockout, a good visited child can deepen."""
        parent_visits = 10
        visited = _Node(action={"op": "SET_STORIES"}, prior=0.2)
        visited.visits = 4
        visited.value = 3.2  # q = 0.8
        visited.kind = "applied"
        unvisited = _Node(action={"op": "APPLY_PARTITION"}, prior=0.3)
        # Old formula gave unvisited ~1000; new formula is exploration-scaled.
        self.assertLess(_puct(unvisited, parent_visits), 100)
        self.assertGreater(_puct(visited, parent_visits), _puct(unvisited, parent_visits) * 0.5)

    def test_illegal_child_never_wins_puct(self) -> None:
        dead = _Node(action={"op": "FIX_PARTITION"}, prior=0.7)
        dead.kind = "illegal"
        dead.visits = 8
        dead.value = 0.0
        alive = _Node(action={"op": "APPLY_PARTITION"}, prior=0.03)
        self.assertEqual(_puct(dead, 16), float("-inf"))
        self.assertGreater(_puct(alive, 16), _puct(dead, 16))

    def test_select_prefers_unvisited_until_accepted(self) -> None:
        root = _Node()
        mediocre = _Node(action={"op": "PIN_GROUND"}, prior=0.14, parent=root)
        mediocre.kind = "applied"
        mediocre.visits = 9
        mediocre.value = 2.25  # q = 0.25 (illegal ceiling)
        pending = _Node(action={"op": "APPLY_PARTITION"}, prior=0.03, parent=root)
        dead = _Node(action={"op": "REQUIREMENT"}, prior=0.7, parent=root)
        dead.kind = "illegal"
        dead.visits = 5
        root.children = [mediocre, pending, dead]
        root.visits = 14
        chosen = _select_child(root)
        self.assertIs(chosen, pending)

    def test_select_deepens_accepted_over_unvisited(self) -> None:
        root = _Node()
        strong = _Node(action={"op": "SET_ENVELOPE"}, prior=0.2, parent=root)
        strong.kind = "applied"
        strong.visits = 4
        strong.value = 3.2  # q = 0.8
        pending = _Node(action={"op": "APPLY_PARTITION"}, prior=0.05, parent=root)
        root.children = [strong, pending]
        root.visits = 10
        chosen = _select_child(root)
        self.assertIs(chosen, strong)

    def test_planner_prior_drops_unknown_ops(self) -> None:
        session = SimpleNamespace(masses=[], constraints={}, floor_pins={}, pairings=[])
        plan = {
            "actions": [
                {"op": "FIX_PARTITION"},
                {"op": "REQUIREMENT"},
                {"op": "SET_LOADING", "loading": "single"},
                {"op": "SET_DOUBLE_HEIGHT"},
            ]
        }
        kept = _planner_prior(session, client=None, plan=plan)
        ops = [str(a.get("op") or "").upper() for a in kept]
        self.assertEqual(ops, ["SET_LOADING"])

    def test_mcts_skips_unknown_planner_ops(self) -> None:
        """Regression: high-prior illegal planner ops must not consume the budget."""
        from massing_explorer.explore.mcts import run_mcts

        mass = SimpleNamespace(id="m1", name="M1", story_count=2, departments=["CORE ACADEMIC"])
        session = SimpleNamespace(
            masses=[mass],
            constraints={
                "max_stories": 3,
                "explore_budget": {"mcts_sims": 12, "mcts_depth": 3, "mcts_roots": 1},
                "explore": {"archive": {"cells": {}, "attempts": 0}},
            },
            floor_pins={},
            pairings=[],
            program=None,
        )
        plan = {"actions": [{"op": "REQUIREMENT"}, {"op": "FIX_PARTITION"}]}
        played: list[str] = []

        def _fake_apply(sess, action):
            op = str((action or {}).get("op") or "").upper()
            played.append(op)
            if op in {"REQUIREMENT", "FIX_PARTITION"}:
                return {"ok": False, "op": op, "reason": "Not a known design action."}
            return {"ok": True, "op": op, "reason": f"{op} applied."}

        with patch("massing_explorer.explore.mcts.apply_action", side_effect=_fake_apply), patch(
            "massing_explorer.explore.mcts.catalog_actions",
            return_value=[
                {"op": "SET_STORIES", "mass": "m1", "stories": 3},
                {"op": "APPLY_PARTITION", "groups": [["CORE ACADEMIC"]]},
            ],
        ), patch(
            "massing_explorer.explore.mcts.cover_roots",
            return_value=[
                {
                    "snapshot": {"masses": [], "constraints": {}, "stories": {"m1": 2}},
                    "stories": {"m1": 2},
                    "label": "frontier d=0.000",
                    "entry": None,
                }
            ],
        ), patch("massing_explorer.explore.archive.restore_snapshot"), patch(
            "massing_explorer.explore.archive.capture", return_value={}
        ):
            report = run_mcts(session, simulations=12, plan=plan, root_cap=1, max_depth=3)
        self.assertGreater(report.get("applied") or 0, 0)
        self.assertNotIn("REQUIREMENT", played)
        self.assertNotIn("FIX_PARTITION", played)
        self.assertTrue({"SET_STORIES", "APPLY_PARTITION"} & set(played))

    def test_apply_partition_scored_without_chained_followup(self) -> None:
        """APPLY_PARTITION must be evaluated as its own leaf (53c legal-yield)."""
        from massing_explorer.explore.mcts import run_mcts

        mass = SimpleNamespace(
            id="m1", name="M1", story_count=2, departments=["CORE ACADEMIC"]
        )
        archive = {"cells": {}, "attempts": 0}
        session = SimpleNamespace(
            masses=[mass],
            constraints={
                "max_stories": 3,
                "explore_budget": {"mcts_sims": 8, "mcts_depth": 4, "mcts_roots": 1},
                "explore": {"archive": archive},
            },
            floor_pins={},
            pairings=[],
            program=object(),
        )
        scored_ops: list[str] = []

        def _fake_apply(sess, action):
            return {
                "ok": True,
                "op": str((action or {}).get("op") or "").upper(),
                "reason": "ok",
            }

        def _fake_score(sess, arch, node, weights=None):
            op = str((node.action or {}).get("op") or "root").upper()
            scored_ops.append(op)
            return 0.2

        with patch("massing_explorer.explore.mcts.apply_action", side_effect=_fake_apply), patch(
            "massing_explorer.explore.mcts.catalog_actions",
            return_value=[
                {"op": "SET_STORIES", "mass": "m1", "stories": 3},
                {"op": "APPLY_PARTITION", "groups": [["CORE ACADEMIC"]]},
                {"op": "SET_LOADING", "loading": "single"},
            ],
        ), patch(
            "massing_explorer.explore.mcts.cover_roots",
            return_value=[
                {
                    "snapshot": {"masses": [], "constraints": {}, "stories": {"m1": 2}},
                    "stories": {"m1": 2},
                    "label": "frontier d=0.000",
                    "entry": None,
                }
            ],
        ), patch("massing_explorer.explore.mcts._score", side_effect=_fake_score), patch(
            "massing_explorer.explore.archive.restore_snapshot"
        ), patch("massing_explorer.explore.archive.capture", return_value={}):
            run_mcts(session, archive=archive, simulations=8, root_cap=1, max_depth=4)

        self.assertIn("APPLY_PARTITION", scored_ops)
        # At least one simulation scored APPLY as the leaf (not only as a parent
        # of a chained SET_STORIES / SET_LOADING).
        self.assertGreaterEqual(scored_ops.count("APPLY_PARTITION"), 1)

    def test_apply_partition_clears_stale_widths(self) -> None:
        """Regrouping must drop prior feet so realize can refill (ratio_band miss)."""
        from massing_explorer.explore.actions import apply_action

        m1 = SimpleNamespace(
            id="athletics_dining_support",
            name="Ath",
            story_count=2,
            departments=["DINING & FOOD SERVICE", "HEALTH & PHYSICAL EDUCATION"],
        )
        m2 = SimpleNamespace(
            id="arts_support",
            name="Arts",
            story_count=1,
            departments=["ART & MUSIC"],
        )
        session = SimpleNamespace(
            masses=[m1, m2],
            constraints={
                "athletics_dining_support_width_ft": 196.85,
                "arts_support_width_ft": 66.0,
                "explore": {"realize_cache": {"x": 1}},
            },
            floor_pins={},
            pairings=[],
        )
        groups = [
            {
                "id": "athletics_dining_support",
                "departments": ["DINING & FOOD SERVICE", "HEALTH & PHYSICAL EDUCATION"],
                "story_count": 2,
            },
            {
                "id": "arts_support",
                "departments": ["ADMINISTRATION & GUIDANCE", "ART & MUSIC"],
                "story_count": 1,
            },
        ]
        with patch(
            "massing_explorer.explore.partitions.apply_partition",
            return_value=None,
        ):
            out = apply_action(session, {"op": "APPLY_PARTITION", "groups": groups})
        self.assertTrue(out.get("ok"))
        self.assertNotIn("athletics_dining_support_width_ft", session.constraints)
        self.assertNotIn("arts_support_width_ft", session.constraints)
        self.assertNotIn("realize_cache", session.constraints.get("explore") or {})

    def test_balance_action_categories_round_robin(self) -> None:
        actions = (
            [{"op": "SET_STORIES", "stories": i} for i in range(6)]
            + [{"op": "APPLY_PARTITION", "groups": i} for i in range(2)]
            + [{"op": "SET_ENVELOPE", "envelope": "compact"}]
        )
        out = _balance_action_categories(actions, per_op=2)
        ops = [a["op"] for a in out]
        self.assertEqual(ops.count("SET_STORIES"), 2)
        self.assertEqual(ops.count("APPLY_PARTITION"), 2)
        self.assertEqual(ops.count("SET_ENVELOPE"), 1)
        # Round-robin: first slots are not all SET_STORIES.
        self.assertNotEqual(ops[:3], ["SET_STORIES", "SET_STORIES", "SET_STORIES"])


class RobustnessLabelTests(unittest.TestCase):
    def test_untested_proxy_labeled(self) -> None:
        vector = {"leftover_area": 0.2}
        score = _robustness_score(vector)
        self.assertTrue(0.0 <= score <= 1.0)
        self.assertEqual(vector.get("robustness_status"), "untested")

    def test_attach_probe_marks_probed(self) -> None:
        entry = {"performance": {"leftover_area": 0.1, "fits_limitations": True}}
        probe = {
            "tested": True,
            "score": 0.75,
            "survived": 3,
            "collapsed": 1,
            "departments": ["CORE ACADEMIC"],
            "factors": [0.85, 1.15],
            "dimensions_may_adapt": True,
            "strategy_fixed": True,
        }
        attach_probe_to_entry(entry, probe)
        self.assertEqual(entry["performance"]["robustness_status"], "probed")
        self.assertEqual(entry["performance"]["_robustness_probe"], 0.75)
        self.assertEqual(entry["robustness"]["status"], "probed")


class LearnRefineHookTests(unittest.TestCase):
    def test_apply_choice_triggers_targeted_refine(self) -> None:
        from massing_explorer.explore import preference as pref

        entry_a = {
            "cell": "a1",
            "fits_limitations": True,
            "partition": "pA",
            "performance": {
                "fits_limitations": True,
                "program_coherence": 0.9,
                "preference_alignment": 0.4,
                "performance_efficiency": 0.5,
                "robustness": 0.5,
            },
            "snapshot": {"masses": []},
            "stories": {"m0": 2},
        }
        entry_b = {
            "cell": "b1",
            "fits_limitations": True,
            "partition": "pB",
            "performance": {
                "fits_limitations": True,
                "program_coherence": 0.4,
                "preference_alignment": 0.9,
                "performance_efficiency": 0.5,
                "robustness": 0.5,
            },
            "snapshot": {"masses": []},
            "stories": {"m0": 3},
        }
        archive = {
            "cells": {"a1": entry_a, "b1": entry_b},
            "attempts": 2,
            "legal": 2,
        }
        session = SimpleNamespace(
            constraints={
                "explore": {
                    "archive": archive,
                    "learning": {
                        "weights": {},
                        "comparisons": [],
                        "pending_pair": {"a": "a1", "b": "b1", "kind": "contrast"},
                    },
                }
            },
            masses=[],
            save=lambda: None,
        )

        with patch.object(pref, "_learn_targeted_refine", return_value={"ran": True, "tuned": 2, "improved": True, "note": "ok"}) as refine:
            with patch("massing_explorer.explore.archive.restore_entry"):
                out = pref.apply_choice(session, "a")
        self.assertTrue(out and out.get("ok"))
        refine.assert_called_once()
        self.assertTrue((out.get("refine") or {}).get("ran"))


if __name__ == "__main__":
    unittest.main()
