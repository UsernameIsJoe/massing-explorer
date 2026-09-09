"""Transparency payload for the studio UI."""

from __future__ import annotations

from types import SimpleNamespace

from massing_explorer.brief import briefing_from_parsed, parse_brief
from massing_explorer.explore.ui_payload import transparency_payload


def test_transparency_quick_path_has_roles():
    depts = ["Gym", "Dining", "Art", "Admin"]
    parsed = parse_brief(
        "Four masses. Gym and dining together. Max 400 ft. Art on the ground floor. Max 3 stories.",
        depts,
    )
    briefing = briefing_from_parsed(parsed)
    session = SimpleNamespace(
        last_search=[
            {
                "total_length_ft": 380.0,
                "score": 0.9,
                "verified": True,
                "masses": [
                    {
                        "mass_id": "m1",
                        "mass_name": "Wing A",
                        "stories": 2,
                        "width_ft": 60,
                        "length_ft": 120,
                    }
                ],
            }
        ],
        constraints={"briefing": briefing},
        masses=[],
        config_path="",
    )
    t = transparency_payload(session, parsed=parsed, full_explore=False)
    assert t["interpreted"]["requirements"]
    assert t["interpreted"]["limitations"]
    assert t["interpreted"]["preferences"]
    assert t["sample_pool"]["count"] == 1
    assert t["sample_pool"]["schemes"][0]["preview"]["boxes"]
    assert t["sample_pool"]["selected_rank"] == 0
    assert t["process"]["steps"][0]["phase"] == "QUICK"


def test_transparency_marks_kept_cell_id_string():
    archive = {
        "attempts": 3,
        "legal": 1,
        "cells": {
            "c1": {
                "cell": "c1",
                "partition": "A|B",
                "fits_limitations": True,
                "stories": {"m1": 2},
                "strategy": {"T": {"kind": "row"}},
                "performance": {"feasible": True},
            }
        },
    }
    session = SimpleNamespace(
        last_search=[],
        constraints={
            "briefing": {"requirements": [], "limitations": [], "preferences": []},
            "explore": {
                "mode": "cover",
                "note": "done",
                "kept_cell": "c1",
                "archive": archive,
                "refine": {"ran": True, "tries": 2, "improved": False, "elites": 3, "reason": "ok"},
                "bayes": {"ran": True, "spent": 2, "budget": 4, "observed": 2, "picked": [], "candidates": []},
            },
        },
        masses=[],
    )
    t = transparency_payload(session, full_explore=True)
    assert any(c.get("kept") for c in t["top_candidates"])
    phases = [s["phase"] for s in t["process"]["steps"]]
    assert "COVER" in phases
    assert "BO" in phases
    assert "REFINE" in phases
    refine = next(s for s in t["process"]["steps"] if s["phase"] == "REFINE")
    assert "3 elites" in refine["summary"]
