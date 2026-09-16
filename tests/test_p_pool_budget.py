"""Equal-budget comparison: fixed P shortlist vs expanding P pool."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from massing_explorer.explore import archive as archive_mod
from massing_explorer.explore.cover import run_cover
from massing_explorer.explore.p_pool import p_pool_summary
from massing_explorer.explore.saturate import architectural_reward
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession
from massing_explorer.tools import set_grouping

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"


def _metrics(archive: dict[str, Any]) -> dict[str, Any]:
    cells = list((archive.get("cells") or {}).values())
    legal = [e for e in cells if e.get("fits_limitations")]
    partitions = {str(e.get("partition") or "") for e in legal if e.get("partition")}
    rewards = [architectural_reward(e.get("performance") or {}) for e in legal]
    improvements = 0
    best = -1.0
    for r in sorted(rewards):
        if r > best + 1e-6:
            improvements += 1
            best = r
    summary = p_pool_summary(archive)
    return {
        "attempts": int(archive.get("attempts") or 0),
        "legal": len(legal),
        "distinct_feasible_partitions": len(partitions),
        "quality_improvements": improvements,
        "p_pool_size": summary.get("size"),
        "p_expansions": summary.get("expansions"),
        "best_reward": max(rewards) if rewards else 0.0,
    }


class EqualBudgetPPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="p_pool_budget", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "a",
                    "name": "A",
                    "departments": ["CORE ACADEMIC", "SPECIAL EDUCATION"],
                    "story_count": 2,
                },
                {
                    "id": "b",
                    "name": "B",
                    "departments": [
                        "HEALTH & PHYSICAL EDUCATION",
                        "DINING & FOOD SERVICE",
                    ],
                    "story_count": 2,
                },
                {
                    "id": "c",
                    "name": "C",
                    "departments": ["ART & MUSIC", "MEDIA CENTER"],
                    "story_count": 2,
                },
            ],
        )
        leftover = [
            d
            for d in self.session.department_names()
            if d
            not in {
                "CORE ACADEMIC",
                "SPECIAL EDUCATION",
                "HEALTH & PHYSICAL EDUCATION",
                "DINING & FOOD SERVICE",
                "ART & MUSIC",
                "MEDIA CENTER",
            }
        ]
        if leftover:
            self.session.masses[0].departments.extend(leftover)
        self.session.constraints["max_total_length_ft"] = 400
        self.session.constraints["max_stories"] = 4
        self.session.constraints["p_constraints"] = {
            "together": [],
            "apart": [],
            "alone": [],
            "mass_count_min": 3,
            "mass_count_max": 4,
        }
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def _run(self, *, expand: bool, budget: int = 24) -> dict[str, Any]:
        archive = archive_mod.empty_archive()

        def evaluate(session, store, reason):
            from massing_explorer.explore.realize import realize

            result, perf = realize(session)
            # insert alone updates P-pool status (no separate record_p_outcome).
            archive_mod.insert(store, session, result, perf, reason=reason)

        if expand:
            report = run_cover(
                self.session,
                archive,
                evaluate=evaluate,
                start=min(12, budget),
                max_attempts=budget,
            )
        else:
            # Freeze expansion: should_expand always false.
            with patch(
                "massing_explorer.explore.cover.should_expand_p_pool",
                return_value=False,
            ):
                report = run_cover(
                    self.session,
                    archive,
                    evaluate=evaluate,
                    start=min(12, budget),
                    max_attempts=budget,
                )
        metrics = _metrics(archive)
        metrics["cover"] = {
            "stagnant": report.get("stagnant"),
            "partitions_planned": (archive.get("cover_plan") or {}).get("partitions"),
        }
        return metrics

    def test_expanding_pool_not_worse_under_equal_budget(self) -> None:
        """
        Under the same evaluate budget, expanding P should find at least as many
        distinct feasible organizations or equal quality progress — documented
        in assert messages for tuning when stochastic.
        """
        fixed = self._run(expand=False, budget=20)
        expanding = self._run(expand=True, budget=20)

        self.assertEqual(fixed["attempts"], expanding["attempts"])
        # Expanding may grow the pool; fixed stays at the initial band.
        self.assertGreaterEqual(
            int(expanding["p_pool_size"] or 0),
            int(fixed["p_pool_size"] or 0),
        )
        # Primary yield: distinct feasible partitions or quality ladder.
        better_or_tie = (
            expanding["distinct_feasible_partitions"]
            >= fixed["distinct_feasible_partitions"]
            or expanding["quality_improvements"] >= fixed["quality_improvements"]
            or expanding["best_reward"] + 1e-9 >= fixed["best_reward"]
        )
        self.assertTrue(
            better_or_tie,
            msg=(
                f"expanding={expanding} should not lose to fixed={fixed} "
                f"on partitions, quality ladder, or best reward"
            ),
        )


if __name__ == "__main__":
    unittest.main()
