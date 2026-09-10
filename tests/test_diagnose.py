"""DIAGNOSE when COVER finds zero legal schemes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from massing_explorer.explore import archive as archive_mod
from massing_explorer.explore.controller import run_search
from massing_explorer.explore.diagnose import (
    classify_failure,
    diagnose,
    failure_patterns,
    find_minimal_conflict,
    relaxation_probes,
)
from massing_explorer.explore.ui_payload import transparency_payload
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession
from massing_explorer.study_state import MassGrouping
from massing_explorer.tools import set_grouping

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

CORE = "CORE ACADEMIC"
HPE = "HEALTH & PHYSICAL EDUCATION"
DINING = "DINING & FOOD SERVICE"
ART = "ART & MUSIC"


class DiagnoseUnitTests(unittest.TestCase):
    def test_classify_search_when_thin(self) -> None:
        archive = {
            "attempts": 3,
            "legal": 0,
            "cells": {
                "c0": {
                    "cell": "c0",
                    "fits_limitations": False,
                    "performance": {"failed_kinds": ["site_length"], "limit_fails": 1},
                }
            },
            "cover": {"samples_planned": 40, "samples_used": 2},
        }
        session = SimpleNamespace(constraints={}, masses=[], brief_locked=True)
        self.assertEqual(classify_failure(session, archive), "search")

    def test_classify_conflict_when_same_fail_dominates(self) -> None:
        cells = {}
        for i in range(12):
            cells[f"c{i}"] = {
                "cell": f"c{i}",
                "fits_limitations": False,
                "performance": {
                    "failed_kinds": ["site_length", "site_width"],
                    "limit_fails": 2,
                },
            }
        archive = {"attempts": 20, "legal": 0, "cells": cells, "cover": {"samples_planned": 40, "samples_used": 20}}
        session = SimpleNamespace(constraints={"briefing": {}}, masses=[], brief_locked=True)
        patterns = failure_patterns(archive)
        self.assertGreaterEqual(patterns["dominant_share"], 0.55)
        self.assertEqual(classify_failure(session, archive, patterns), "conflict")

    def test_classify_model_when_courtyard_brief(self) -> None:
        archive = {"attempts": 20, "legal": 0, "cells": {}, "cover": {}}
        session = SimpleNamespace(
            constraints={"brief_text": "prefer a courtyard around a lawn"},
            masses=[],
            brief_locked=True,
        )
        self.assertEqual(classify_failure(session, archive), "model")

    def test_probes_never_mark_apply_true(self) -> None:
        session = SimpleNamespace(
            constraints={
                "max_stories": 3,
                "max_edge_ft": 131.0,
                "briefing": {
                    "limitations": [
                        {"kind": "limitation", "lever": "max_stories", "value": 3},
                        {"kind": "limitation", "lever": "max_edge", "value": 131.0, "unit": "ft"},
                    ],
                    "requirements": [],
                },
            },
            masses=[],
        )
        conflict = {
            "kind": "conflict",
            "clauses": [
                {"kind": "limitation", "lever": "max_stories", "value": 3, "label": "max stories 3"},
                {
                    "kind": "limitation",
                    "lever": "max_edge",
                    "value": 131.0,
                    "label": "every mass edge",
                },
            ],
        }
        probes = relaxation_probes(session, conflict, {"focus": "edge"})
        self.assertTrue(probes)
        self.assertTrue(all(p.get("apply") is False for p in probes))
        levers = {p.get("lever") for p in probes}
        self.assertTrue(levers & {"max_stories", "max_edge"})


class DiagnoseIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="diagnose_zero", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, ART],
                    "story_count": 2,
                },
                {
                    "id": "public",
                    "name": "Public",
                    "departments": [HPE, DINING],
                    "story_count": 2,
                },
            ],
        )
        # Impossible all-edge cap → COVER stays illegal.
        self.session.constraints["max_edge_ft"] = 10.0
        self.session.constraints["max_building_length_ft"] = 10.0
        self.session.constraints["max_building_width_ft"] = 10.0
        self.session.constraints["max_stories"] = 2
        self.session.constraints["briefing"] = {
            "requirements": [],
            "limitations": [
                {"kind": "limitation", "lever": "max_edge", "value": 10.0, "unit": "ft"},
                {"kind": "limitation", "lever": "max_stories", "value": 2},
            ],
            "preferences": [],
        }
        self.session.brief_locked = True
        self.session.constraints["cover_budget"] = {
            "start": 8,
            "step_small": 4,
            "step_large": 4,
            "max": 12,
        }
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_cover_zero_legal_runs_diagnose_skips_refine(self) -> None:
        out = run_search(self.session, mode="cover")
        store = self.session.constraints.get("explore") or {}
        diag = store.get("diagnose") or {}
        self.assertTrue(diag.get("ran"), diag)
        self.assertIn(diag.get("class"), {"search", "conflict", "model"})
        self.assertEqual(int((store.get("archive") or {}).get("legal") or 0), 0)
        self.assertFalse((store.get("refine") or {}).get("ran"))
        self.assertFalse((store.get("mcts") or {}).get("ran"))
        self.assertFalse((store.get("bayes") or {}).get("ran"))
        self.assertTrue(any(p.get("apply") is False for p in (diag.get("probes") or []) or [{"apply": False}]))
        t = transparency_payload(self.session, full_explore=True)
        phases = [s.get("phase") for s in (t.get("process") or {}).get("steps") or []]
        self.assertIn("DIAGNOSE", phases)
        self.assertNotIn("REFINE", phases)
        self.assertIn("DIAGNOSE", out.get("note") or store.get("note") or "")

    def test_diagnose_does_not_erase_brief_site_caps(self) -> None:
        """Probe drops must round-trip; frontage stays on the live session."""
        self.session.constraints["max_total_length_ft"] = 420.0
        self.session.save()
        run_search(self.session, mode="cover")
        self.assertEqual(self.session.constraints.get("max_total_length_ft"), 420.0)
        self.assertEqual(self.session.constraints.get("max_edge_ft"), 10.0)
        self.assertEqual(self.session.constraints.get("max_building_length_ft"), 10.0)
        self.assertEqual(self.session.constraints.get("max_building_width_ft"), 10.0)
        self.assertEqual(self.session.constraints.get("max_stories"), 2)
        self.assertTrue(self.session.brief_locked)

    def test_minimal_conflict_names_edge_or_stories(self) -> None:
        archive = archive_mod.empty_archive()
        # Seed one illegal evaluation so patterns exist.
        from massing_explorer.explore.controller import _evaluate

        _evaluate(self.session, archive, "seed")
        conflict = find_minimal_conflict(self.session, archive)
        self.assertIn(conflict.get("kind"), {"conflict", "suspected", "unknown", "none"})
        levers = {c.get("lever") for c in (conflict.get("clauses") or [])}
        self.assertTrue(
            levers & {"max_edge", "max_length", "max_stories", "exact_width", "site_length"}
            or conflict.get("kind") in {"unknown", "none"},
            conflict,
        )


if __name__ == "__main__":
    unittest.main()
