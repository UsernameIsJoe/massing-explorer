"""Phase 2 tests: study state, tools, persistence (no Ollama required)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from massing_explorer.load import load_program_file
from massing_explorer.session import STUDIES_DIR, StudySession, slugify_study_id
from massing_explorer.tools import (
    execute_tool,
    get_department_summary,
    get_grouping_summary,
    set_grouping,
    set_story_count,
    set_constraint,
)

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"


class TestPhase2(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_studies = STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig_studies
        self.tmp.cleanup()

    def _make_session(self) -> StudySession:
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        return StudySession(
            study_id="test_school",
            program=program,
            config_path=str(CONFIG),
        )

    def test_slugify(self) -> None:
        self.assertEqual(slugify_study_id("Underwood Elementary"), "underwood_elementary")

    def test_department_summary_from_engine(self) -> None:
        session = self._make_session()
        summary = get_department_summary(session)
        self.assertEqual(len(summary["departments"]), 9)
        self.assertAlmostEqual(summary["totals"]["nfa_sf"], 40462, delta=1)
        self.assertAlmostEqual(summary["totals"]["target_gsf"], 40462 * 1.15 * 1.5, delta=1)

    def test_set_grouping(self) -> None:
        session = self._make_session()
        depts = session.department_names()
        result = set_grouping(
            session,
            [
                {
                    "id": "academic",
                    "name": "Academic Wing",
                    "departments": ["CORE ACADEMIC", "SPECIAL EDUCATION"],
                    "story_count": 3,
                },
                {
                    "id": "support",
                    "name": "Support Wing",
                    "departments": [d for d in depts if d not in ("CORE ACADEMIC", "SPECIAL EDUCATION")],
                    "story_count": 2,
                },
            ],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(session.masses), 2)
        g = get_grouping_summary(session)
        self.assertEqual(len(g["unassigned_departments"]), 0)

    def test_set_story_count(self) -> None:
        session = self._make_session()
        set_grouping(
            session,
            [{"id": "main", "name": "Main", "departments": session.department_names()}],
        )
        result = set_story_count(session, "main", 4)
        self.assertTrue(result["ok"])
        self.assertEqual(session.masses[0].story_count, 4)

    def test_set_constraint(self) -> None:
        session = self._make_session()
        set_constraint(session, "academic_width_ft", 80)
        self.assertEqual(session.constraints["academic_width_ft"], 80)

    def test_unknown_department_rejected(self) -> None:
        session = self._make_session()
        result = set_grouping(
            session,
            [{"id": "x", "name": "X", "departments": ["FAKE DEPARTMENT"]}],
        )
        self.assertFalse(result["ok"])

    def test_persistence_roundtrip(self) -> None:
        session = self._make_session()
        set_grouping(
            session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": ["CORE ACADEMIC"],
                    "story_count": 3,
                }
            ],
        )
        set_constraint(session, "max_length_ft", 280)
        session.save()

        loaded = StudySession.load("test_school")
        self.assertEqual(len(loaded.masses), 1)
        self.assertEqual(loaded.masses[0].story_count, 3)
        self.assertEqual(loaded.constraints["max_length_ft"], 280)
        self.assertAlmostEqual(
            loaded.program.totals["nfa_sf"], session.program.totals["nfa_sf"], delta=1
        )

    def test_execute_tool_dispatch(self) -> None:
        session = self._make_session()
        raw = execute_tool(session, "get_department_summary", {})
        data = json.loads(raw)
        self.assertIn("departments", data)
        self.assertEqual(len(data["departments"]), 9)


if __name__ == "__main__":
    unittest.main()
