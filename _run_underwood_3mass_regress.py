"""Run the user's Underwood 3-mass regression brief on current code."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from massing_explorer.brief import apply_brief
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession

ROOT = Path(__file__).resolve().parent
GSF = ROOT / "examples" / "Underwood_Elementary_Space_Summary_GSF_Tweaked.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

BRIEF = (
    "3 masses, max 3 floors. length max 60 meters. gym and dining together and "
    "double height. art and music prefer on ground floor. media prefer on top "
    "floor above admin. admin have to be on ground floor. core academic and "
    "special ed width has to be 80 feet. mass ratio have to be between 2:5 and "
    "5:8. prefer 3 floors."
)


def main() -> None:
    import massing_explorer.session as session_mod

    assert GSF.is_file(), f"missing {GSF}"
    tmp = tempfile.TemporaryDirectory()
    orig = session_mod.STUDIES_DIR
    session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
    try:
        program = load_program_file(GSF, config_path=CONFIG)
        session = StudySession(
            study_id="underwood_3mass_regress",
            program=program,
            config_path=str(CONFIG),
        )
        # Production-like budgets (slightly capped for runtime).
        session.constraints["cover_budget"] = {
            "start": 40,
            "step_small": 10,
            "step_large": 20,
            "max": 100,
        }
        session.constraints["explore_budget"] = {
            "mcts_sims": 64,
            "mcts_depth": 4,
            "mcts_roots": 4,
            "bo": 12,
            "refine": 14,
            "repair": 14,
        }
        session.save()
        out = apply_brief(session, BRIEF)
        explore = session.constraints.get("explore") or {}
        archive = explore.get("archive") or {}
        cells = list((archive.get("cells") or {}).values())
        legal = [c for c in cells if c.get("fits_limitations")]
        three = [
            c
            for c in legal
            if len(c.get("stories") or {}) == 3 or len(c.get("plates") or []) == 3
        ]
        phases = Counter()
        for c in cells:
            r = str(c.get("reason") or "")
            if r.startswith("COVER"):
                phases["COVER"] += 1
            elif r.startswith("mcts"):
                phases["mcts"] += 1
            elif r.startswith("bayes"):
                phases["bayes"] += 1
            elif r.startswith("refine"):
                phases["refine"] += 1
            elif "repair" in r:
                phases["repair"] += 1
        pool = (archive.get("p_pool") or {}).get("entries") or {}
        pst = Counter(str(e.get("status")) for e in pool.values())
        report = {
            "ok": out.get("ok"),
            "attempts": archive.get("attempts"),
            "cells": len(cells),
            "legal": len(legal),
            "legal_three_mass": len(three),
            "phases": dict(phases),
            "p_pool": dict(pst),
            "p_pool_size": len(pool),
            "note": (explore.get("note") or "")[:400],
            "constraints": {
                "mass_count": (session.constraints.get("p_constraints") or {}).get(
                    "mass_count"
                ),
                "max_edge_ft": session.constraints.get("max_edge_ft")
                or session.constraints.get("max_building_length_ft"),
                "max_stories": session.constraints.get("max_stories"),
                "ratio_band": session.constraints.get("ratio_band"),
                "department_widths": session.constraints.get("department_widths"),
            },
            "legal_reasons": [c.get("reason") for c in three[:8]],
            "legal_stories": [c.get("stories") for c in three[:8]],
        }
        out_path = ROOT / "studies" / "_underwood_3mass_regress_report.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        print("wrote", out_path)
    finally:
        session_mod.STUDIES_DIR = orig
        tmp.cleanup()


if __name__ == "__main__":
    main()
