"""Near-feasible frontier, violation distance, and COVER repair."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from massing_explorer.explore.archive import (
    empty_archive,
    frontier_entries,
    insert,
    legal_cells,
    refresh_frontier,
)
from massing_explorer.explore.diagnose import should_diagnose
from massing_explorer.explore.feasibility import (
    FRONTIER_DISTANCE,
    feasibility_distance,
    violations_from_checks,
)
from massing_explorer.explore.mcts import cover_roots
from massing_explorer.explore.preference import schemes_from_archive
from massing_explorer.explore.repair import pick_repair_action, run_repair
from massing_explorer.explore.saturate import search_reward
from massing_explorer.explore.strategy import partition_id
from massing_explorer.massing_models import ValidationCheck
from massing_explorer.models import Department, GrossingConfig, ProgramStudy, Room
from massing_explorer.session import StudySession
from massing_explorer.study_state import MassGrouping
from massing_explorer.tools import set_grouping

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "project.example.yaml"
CORE = "CORE ACADEMIC"


def _check(name: str, message: str) -> ValidationCheck:
    return ValidationCheck(check=name, passed=False, message=message)


class TestViolationDistance(unittest.TestCase):
    def test_slight_edge_miss_ranks_closer_than_garbage(self) -> None:
        near = violations_from_checks(
            [_check("site_length:mass_1", "Mass 1: length 197.2 ft vs max 196.0 ft")]
        )
        far = violations_from_checks(
            [
                _check("site_length:mass_1", "Mass 1: length 280.0 ft vs max 196.0 ft"),
                _check(
                    "program_split:mass_1:CORE ACADEMIC",
                    "Mass 1 / CORE ACADEMIC (L0+L1): split slice under 100 m²",
                ),
                _check(
                    "ratio_band:mass_1",
                    "Mass 1: length/width 3.400 (transpose 0.294) vs allowed 0.4–0.625 either way",
                ),
            ],
            awkward=True,
        )
        self.assertLess(feasibility_distance(near), feasibility_distance(far))
        self.assertLess(near["edge_overrun"], 0.02)
        self.assertGreater(far["edge_overrun"], 0.3)
        self.assertEqual(far["split"], 1.0)

    def test_search_reward_stays_zero_for_near_feasible(self) -> None:
        self.assertEqual(
            search_reward(
                {
                    "fits_limitations": False,
                    "feasibility_distance": 0.01,
                    "program_coherence": 1.0,
                    "preference_alignment": 1.0,
                }
            ),
            0.0,
        )


class TestArchiveFrontier(unittest.TestCase):
    def test_closer_illegal_replaces_occupant(self) -> None:
        session = SimpleNamespace(
            masses=[
                MassGrouping(id="academic", name="Academic", departments=[CORE], story_count=2)
            ],
            pairings=[],
            floor_pins={},
            floor_steps={},
            floor_tapers={},
            double_height_rooms=[],
            brief_locked=True,
            constraints={"loading": "double", "cover_envelope": "balanced"},
        )
        archive = empty_archive()
        dummy = SimpleNamespace(validation=[], masses=[])
        insert(
            archive,
            session,
            dummy,
            {"fits_limitations": False, "feasibility_distance": 0.40, "failed_checks": 2},
            reason="far",
        )
        insert(
            archive,
            session,
            dummy,
            {"fits_limitations": False, "feasibility_distance": 0.08, "failed_checks": 1},
            reason="near",
        )
        cell = next(iter(archive["cells"].values()))
        self.assertEqual(cell["reason"], "near")
        self.assertAlmostEqual(cell["performance"]["feasibility_distance"], 0.08)

    def test_frontier_one_per_idea_and_drops_garbage(self) -> None:
        archive = empty_archive()
        archive["cells"] = {
            "a": {
                "cell": "a",
                "idea": "idea-a",
                "fits_limitations": False,
                "performance": {
                    "feasibility_distance": 0.10,
                    "failed_kinds": ["site_length"],
                },
            },
            "a2": {
                "cell": "a2",
                "idea": "idea-a",
                "fits_limitations": False,
                "performance": {"feasibility_distance": 0.22, "failed_kinds": ["site_length"]},
            },
            "far": {
                "cell": "far",
                "idea": "idea-far",
                "fits_limitations": False,
                "performance": {"feasibility_distance": 0.80, "failed_kinds": ["site_length"]},
            },
            "legal": {
                "cell": "legal",
                "idea": "idea-c",
                "fits_limitations": True,
                "performance": {"feasibility_distance": 0.0},
            },
            "twin": {
                "cell": "twin",
                "idea": "idea-c",
                "fits_limitations": False,
                "performance": {"feasibility_distance": 0.05, "failed_kinds": ["ratio_band"]},
            },
        }
        refresh_frontier(archive)
        ideas = {row["idea"] for row in archive["frontier"]}
        self.assertEqual(ideas, {"idea-a"})
        self.assertAlmostEqual(archive["frontier"][0]["distance"], 0.10)
        self.assertLess(archive["frontier"][0]["distance"], FRONTIER_DISTANCE)
        self.assertEqual(len(frontier_entries(archive)), 1)

    def test_learn_pairs_stay_legal_only(self) -> None:
        archive = empty_archive()
        archive["cells"] = {
            "ok": {
                "cell": "ok",
                "fits_limitations": True,
                "performance": {"fits_limitations": True, "feasible": True, "failed_checks": 0},
            },
            "no": {
                "cell": "no",
                "fits_limitations": False,
                "performance": {
                    "fits_limitations": False,
                    "feasible": False,
                    "failed_checks": 1,
                    "feasibility_distance": 0.05,
                },
            },
        }
        ids = {s["id"] for s in schemes_from_archive(archive)}
        self.assertEqual(ids, {"ok"})


class TestDiagnoseTrigger(unittest.TestCase):
    def test_skips_when_plenty_legal(self) -> None:
        archive = empty_archive()
        archive["cells"] = {
            f"c{i}": {"cell": f"c{i}", "fits_limitations": True, "performance": {}}
            for i in range(8)
        }
        archive["frontier"] = [{"cell": "x"}] * 6
        self.assertFalse(should_diagnose(archive))

    def test_runs_when_zero_legal(self) -> None:
        archive = empty_archive()
        archive["cells"] = {
            "c0": {"cell": "c0", "fits_limitations": False, "performance": {}}
        }
        self.assertTrue(should_diagnose(archive))

    def test_runs_on_low_yield_fat_frontier(self) -> None:
        archive = empty_archive()
        archive["cells"] = {
            "a": {"cell": "a", "fits_limitations": True, "performance": {}},
            "b": {"cell": "b", "fits_limitations": True, "performance": {}},
        }
        archive["frontier"] = [{"cell": f"f{i}"} for i in range(5)]
        self.assertTrue(should_diagnose(archive))


class TestCoverRootsFrontier(unittest.TestCase):
    def test_mixes_legal_and_frontier(self) -> None:
        session = SimpleNamespace(
            masses=[
                MassGrouping(id="academic", name="Academic", departments=[CORE], story_count=2)
            ],
            pairings=[],
            floor_pins={},
            floor_steps={},
            floor_tapers={},
            double_height_rooms=[],
            brief_locked=True,
            constraints={"loading": "double", "cover_envelope": "balanced"},
        )
        from massing_explorer.explore.archive import capture

        snap = capture(session)
        archive = empty_archive()
        archive["cells"] = {
            "legal": {
                "cell": "legal",
                "idea": "idea-legal",
                "fits_limitations": True,
                "snapshot": snap,
                "stories": {"academic": 2},
                "partition": "p-legal",
                "reason": "legal",
                "strategy": {"T": {"kind": "independent"}, "G": {"envelope": "balanced"}},
                "performance": {"fits_limitations": True, "preference_distance": 0.1},
            },
            "near": {
                "cell": "near",
                "idea": "idea-near",
                "fits_limitations": False,
                "snapshot": snap,
                "stories": {"academic": 2},
                "partition": "p-near",
                "reason": "near",
                "strategy": {"T": {"kind": "independent"}, "G": {"envelope": "compact"}},
                "performance": {
                    "fits_limitations": False,
                    "feasibility_distance": 0.08,
                    "failed_kinds": ["site_length"],
                },
            },
        }
        refresh_frontier(archive)
        roots = cover_roots(session, archive, cap=4)
        labels = [r["label"] for r in roots]
        self.assertTrue(any(lab == "legal" for lab in labels))
        self.assertTrue(any(str(lab).startswith("frontier") for lab in labels))


class TestRepair(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"

        program = ProgramStudy(
            rooms=[Room("Classroom", 1, 10000, CORE)],
            departments=[Department(name=CORE, nfa_sf=10000, target_gsf=10000, room_count=1)],
            grossing=GrossingConfig(area_adjustment=1.0, grossing_factor=1.0),
        )
        self.session = StudySession(
            study_id="repair_toy", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [{"id": "mass_1", "name": "Mass 1", "departments": [CORE], "story_count": 1}],
        )
        self.session.constraints["max_edge_ft"] = 120.0
        self.session.constraints["max_building_length_ft"] = 120.0
        self.session.constraints["max_building_width_ft"] = 200.0
        self.session.constraints["mass_1_width_ft"] = 80.0
        self.session.constraints["explore_budget"] = {"repair": 6}
        self.session.brief_locked = True

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_projects_slight_length_overrun_to_legal(self) -> None:
        from massing_explorer.explore.performance import measure
        from massing_explorer.solver import solve_massing_study

        result = solve_massing_study(self.session)
        perf = measure(result, self.session)
        self.assertFalse(perf.get("fits_limitations"), perf.get("failed_kinds"))
        archive = empty_archive()
        insert(archive, self.session, result, perf, reason="COVER: stated scheme")
        before_p = partition_id(self.session)
        report = run_repair(self.session, archive)
        self.assertGreaterEqual(report["tried"], 1)
        self.assertEqual(partition_id(self.session), before_p)
        self.assertTrue(
            legal_cells(archive) or report["legalized"] >= 1,
            report,
        )

    def test_pick_repair_never_regroups(self) -> None:
        perf = {"failed_kinds": ["site_length"], "fits_limitations": False}
        result = SimpleNamespace(resize_suggestions=[], masses=[])
        action = pick_repair_action(self.session, result, perf, set())
        self.assertIsNotNone(action)
        self.assertNotEqual(action.get("op"), "APPLY_PARTITION")
        self.assertIn(action.get("op"), {"SET_WIDTH", "SET_STORIES", "CLEAR_PAIRINGS"})


if __name__ == "__main__":
    unittest.main()
