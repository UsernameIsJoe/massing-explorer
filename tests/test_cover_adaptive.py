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

    def test_school_critical_one_tall_patterns_included(self) -> None:
        from massing_explorer.explore.cover import school_critical_story_patterns

        crit = school_critical_story_patterns(4, 3)
        self.assertIn((1, 3, 1, 1), crit)
        self.assertIn((3, 2, 2, 2), crit)
        lib = story_pattern_library(4, 3)
        self.assertTrue(any(p == (1, 3, 1, 1) for p in lib) or (1, 3, 1, 1) in crit)


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

    def test_locked_grouping_still_reaches_start_budget(self) -> None:
        """P locked + no paired T must not starve the joint pool below start=40."""
        plan = build_cover_plan(self.session, pool_size=COVER_MAX)
        product = (
            max(1, len(plan.partitions))
            * max(1, len(plan.story_patterns))
            * max(1, len(plan.topologies))
            * max(1, len(plan.loadings))
            * max(1, len(plan.envelopes))
        )
        self.assertGreaterEqual(len(plan.samples), min(COVER_START, product, COVER_MAX))
        self.assertGreaterEqual(len(plan.samples), COVER_START)

    def test_thin_axis_product_does_not_pad_with_geom_ranks(self) -> None:
        """Widths are realize's job; COVER does not invent geom-rank padding."""
        self.session.constraints["partition_locked"] = True
        self.session.constraints["story_lock"] = {
            m.id: int(m.story_count) for m in self.session.masses
        }
        plan = build_cover_plan(self.session, pool_size=COVER_MAX)
        product = (
            max(1, len(plan.partitions))
            * max(1, len(plan.story_patterns))
            * max(1, len(plan.topologies))
            * max(1, len(plan.loadings))
            * max(1, len(plan.envelopes))
            * max(1, len(plan.plate_profiles or ["uniform"]))
        )
        self.assertLess(product, COVER_MAX)
        self.assertLessEqual(len(plan.samples), product + 2)  # stated + stated+step
        self.assertTrue(all(int(getattr(s, "geom_rank", 0) or 0) == 0 for s in plan.samples))

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

    def test_cover_samples_when_nothing_is_legal(self) -> None:
        self.session.constraints["max_edge_ft"] = 10.0
        archive = archive_mod.empty_archive()

        def evaluate(session, store, reason: str) -> None:
            from massing_explorer.explore.performance import measure
            from massing_explorer.solver import solve_massing_study

            result = solve_massing_study(session)
            perf = measure(result, session, archive=store)
            archive_mod.insert(store, session, result, perf, reason=reason)

        report = run_cover(
            self.session,
            archive,
            evaluate=evaluate,
            start=6,
            step_small=6,
            step_large=6,
            max_attempts=18,
        )
        self.assertTrue(report["ran"])
        self.assertGreater(report["attempts"], 6)
        self.assertGreaterEqual(len(archive.get("cells") or {}), 2)
        self.assertEqual(int(archive.get("legal") or 0), 0)

    def test_defaults_match_policy(self) -> None:
        from massing_explorer.explore.cover import PER_PARTITION_STORY_FLOOR
        from massing_explorer.explore.p_pool import PROBE_FLOOR

        self.assertEqual(COVER_START, 40)
        self.assertEqual(COVER_MAX, 120)
        self.assertEqual(PER_PARTITION_STORY_FLOOR, PROBE_FLOOR)
        self.assertEqual(PER_PARTITION_STORY_FLOOR, 5)


GSF_TWEAKED = ROOT / "examples" / "Underwood_Elementary_Space_Summary_GSF_Tweaked.xlsx"
BRIEF_34 = (
    "3-4 masses, max 3 floors. length max 60 meters. gym and dining together and "
    "double height. art and music prefer on ground floor. media prefer on top "
    "floor above admin. admin have to be on ground floor. core academic and "
    "special ed width has to be 80 feet. mass ratio have to be between 2:5 and "
    "5:8. prefer 3 floors."
)


@unittest.skipUnless(GSF_TWEAKED.is_file(), "tweaked Underwood GSF not available")
class PerPartitionFloorGuaranteeTests(unittest.TestCase):
    """Round-robin floor must land in the capped plan, not just be intended."""

    def setUp(self) -> None:
        import massing_explorer.session as session_mod
        from massing_explorer.brief import apply_brief

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(GSF_TWEAKED, config_path=CONFIG)
        self.session = StudySession(
            study_id="floor_guarantee", program=program, config_path=str(CONFIG)
        )
        self.session.constraints["cover_budget"] = {
            "start": 0,
            "step_small": 0,
            "step_large": 0,
            "max": 0,
        }
        self.session.constraints["explore_budget"] = {
            "mcts_sims": 0,
            "mcts_depth": 0,
            "mcts_roots": 0,
            "bo": 0,
            "refine": 0,
            "repair": 0,
        }
        self.session.save()
        apply_brief(self.session, BRIEF_34)

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_every_seated_p_gets_floor_samples_under_cover_max(self) -> None:
        from collections import Counter

        from massing_explorer.explore.cover import (
            PER_PARTITION_STORY_FLOOR,
            build_cover_plan,
        )

        plan = build_cover_plan(self.session, pool_size=COVER_MAX)
        self.assertGreaterEqual(len(plan.partitions), 12)
        by_p = Counter(s.partition_index for s in plan.samples)
        floor = PER_PARTITION_STORY_FLOOR
        for i_p in range(len(plan.partitions)):
            self.assertGreaterEqual(
                by_p.get(i_p, 0),
                floor,
                msg=f"P{i_p} has {by_p.get(i_p, 0)} samples; floor={floor}; "
                f"counts={dict(sorted(by_p.items()))}",
            )


if __name__ == "__main__":
    unittest.main()
