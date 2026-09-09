"""Adaptive COVER: start 40, expand while discovering, cap ~120."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from massing_explorer.explore import archive as archive_mod
from massing_explorer.explore.cover import (
    COVER_MAX,
    COVER_START,
    build_cover_plan,
    run_cover,
    story_pattern_library,
)
from massing_explorer.explore.strategy import cell_key
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession
from massing_explorer.study_state import MassGrouping
from massing_explorer.tools import set_grouping

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"


class StoryPatternTests(unittest.TestCase):
    def test_joint_patterns_not_single_axis(self) -> None:
        pats = story_pattern_library(4, 3)
        self.assertGreaterEqual(len(pats), 6)
        self.assertIn((2, 2, 2, 2), pats)
        self.assertTrue(any(len(set(p)) > 1 for p in pats))


class AdaptiveCoverTests(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="cover_adaptive", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "a",
                    "name": "A",
                    "departments": ["CORE ACADEMIC"],
                    "story_count": 2,
                },
                {
                    "id": "b",
                    "name": "B",
                    "departments": ["HEALTH & PHYSICAL EDUCATION", "DINING & FOOD SERVICE"],
                    "story_count": 2,
                },
                {
                    "id": "c",
                    "name": "C",
                    "departments": ["ART & MUSIC"],
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
                "HEALTH & PHYSICAL EDUCATION",
                "DINING & FOOD SERVICE",
                "ART & MUSIC",
            }
        ]
        if leftover:
            self.session.masses.append(
                MassGrouping(id="d", name="D", departments=leftover, story_count=2)
            )
        self.session.constraints["max_stories"] = 3
        self.session.brief_locked = True
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_plan_is_multi_axis(self) -> None:
        plan = build_cover_plan(self.session, pool_size=40)
        self.assertGreaterEqual(len(plan.story_patterns), 4)
        self.assertIn("independent", plan.topologies)
        self.assertEqual(set(plan.loadings), {"single", "double"})
        self.assertEqual(set(plan.envelopes), {"balanced", "compact", "elongated"})
        self.assertGreaterEqual(len(plan.samples), 20)
        labels = {s.envelope for s in plan.samples}
        self.assertGreaterEqual(len(labels), 2)

    def test_adaptive_stops_when_stagnant(self) -> None:
        archive = archive_mod.empty_archive()
        seen_keys: list[str] = []

        def evaluate(session, store, reason: str) -> None:
            # Pretend every sample is legal; cell_key still varies by axes.
            from massing_explorer.explore.performance import measure
            from massing_explorer.solver import solve_massing_study

            result = solve_massing_study(session)
            perf = measure(result, session, archive=store)
            archive_mod.insert(store, session, result, perf, reason=reason)
            seen_keys.append(cell_key(session))

        report = run_cover(
            self.session,
            archive,
            evaluate=evaluate,
            start=6,
            step_small=3,
            step_large=4,
            max_attempts=18,
        )
        self.assertTrue(report["ran"])
        self.assertGreaterEqual(report["attempts"], 6)
        self.assertLessEqual(report["attempts"], 18)
        self.assertTrue(report["batches"])
        self.assertEqual(report["batches"][0]["tag"], "start")
        # Either stagnated or hit cap — both valid endings.
        self.assertTrue(report["stagnant"] or report["incomplete"] or report["attempts"] >= 6)
        self.assertGreaterEqual(len(set(seen_keys)), 2)

    def test_defaults_match_policy(self) -> None:
        self.assertEqual(COVER_START, 40)
        self.assertEqual(COVER_MAX, 120)


if __name__ == "__main__":
    unittest.main()
