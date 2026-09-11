"""Transparency payload for the studio UI."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from massing_explorer.brief import briefing_from_parsed, parse_brief
from massing_explorer.explore.ui_payload import transparency_payload


class TestUiPayload(unittest.TestCase):
    def test_transparency_quick_path_has_roles(self) -> None:
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
        self.assertTrue(t["interpreted"]["requirements"])
        self.assertTrue(t["interpreted"]["limitations"])
        self.assertTrue(t["interpreted"]["preferences"])
        self.assertEqual(t["sample_pool"]["count"], 1)
        self.assertTrue(t["sample_pool"]["schemes"][0]["preview"]["boxes"])
        self.assertEqual(t["sample_pool"]["selected_rank"], 0)
        self.assertEqual(t["process"]["steps"][0]["phase"], "QUICK")
        self.assertIsNone(t.get("learn_pair"))

    def test_transparency_marks_kept_cell_id_string(self) -> None:
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
                    "performance": {"feasible": True, "fits_limitations": True, "lengths": [120.0]},
                    "plates": [
                        {
                            "mass_id": "m1",
                            "mass_name": "Wing A",
                            "stories": 2,
                            "width_ft": 60,
                            "length_ft": 120,
                        }
                    ],
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
        self.assertTrue(any(c.get("kept") for c in t["top_candidates"]))
        self.assertTrue(t["top_candidates"][0]["preview"]["boxes"])
        self.assertTrue(t["sample_pool"]["schemes"])
        kept = next(c for c in t["top_candidates"] if c.get("kept"))
        self.assertEqual(kept["cell_id"], "c1")
        self.assertEqual(len(kept["probe_axes"]), 9)
        self.assertIn("program_organization", kept["probe_axes"])
        self.assertEqual(len(kept["eval_axes"]), 4)
        self.assertIn("program_coherence", kept["eval_axes"])
        phases = [s["phase"] for s in t["process"]["steps"]]
        self.assertIn("COVER", phases)
        self.assertIn("BO", phases)
        self.assertIn("REFINE", phases)
        refine = next(s for s in t["process"]["steps"] if s["phase"] == "REFINE")
        self.assertIn("3 elites", refine["summary"])

    def test_sample_pool_includes_every_cover_cell(self) -> None:
        plates = [
            {
                "mass_id": "m1",
                "mass_name": "Wing A",
                "stories": 2,
                "width_ft": 60,
                "length_ft": 120,
            }
        ]
        cells = {}
        for i in range(12):
            cid = f"c{i}"
            cell_plates = [
                {
                    "mass_id": "m1",
                    "mass_name": "M1",
                    "stories": 2 + (i % 2),
                    "width_ft": 60.0 + i,
                    "length_ft": 100.0 + i * 2,
                }
            ]
            cells[cid] = {
                "cell": cid,
                "partition": "A|B",
                "fits_limitations": i < 10,
                "stories": {"m1": 2 + (i % 2)},
                "strategy": {
                    "T": {"kind": "independent_bars"},
                    "G": {"envelope": "compact" if i % 2 else "balanced", "loading": "double"},
                },
                "performance": {
                    "feasible": True,
                    "fits_limitations": i < 10,
                    "lengths": [100.0 + i * 2],
                },
                "plates": cell_plates,
            }
        archive = {"attempts": 12, "legal": 10, "cells": cells}
        session = SimpleNamespace(
            last_search=[{"total_length_ft": 380.0, "verified": True, "masses": plates}] * 8,
            constraints={
                "briefing": {"requirements": [], "limitations": [], "preferences": []},
                "explore": {"mode": "cover", "kept_cell": "c0", "archive": archive},
            },
            masses=[],
        )
        t = transparency_payload(session, full_explore=True)
        all_ids = {f"c{i}" for i in range(12)}
        pool_ids = {s.get("cell_id") for s in t["sample_pool"]["schemes"]}
        self.assertEqual(t["sample_pool"]["count"], 12)
        self.assertEqual(t["sample_pool"]["typology_cells"], 12)
        self.assertEqual(t["sample_pool"]["drawings_collapsed"], 0)
        self.assertEqual(pool_ids, all_ids)
        self.assertTrue(all(c.get("why") for c in t["top_candidates"]))
        fails = [s for s in t["sample_pool"]["schemes"] if not s.get("verified")]
        self.assertEqual(len(fails), 2)

        # Same plates → UI collapses duplicate drawings.
        for i, entry in cells.items():
            entry["plates"] = plates
            entry["performance"]["lengths"] = [120.0]
        t2 = transparency_payload(session, full_explore=True)
        self.assertEqual(t2["sample_pool"]["typology_cells"], 12)
        self.assertEqual(t2["sample_pool"]["count"], 1)
        self.assertEqual(t2["sample_pool"]["drawings_collapsed"], 11)

    def test_learn_pair_surfaces_pending_ab_cards(self) -> None:
        def cell(cid: str, length: float) -> dict:
            return {
                "cell": cid,
                "partition": "A|B",
                "fits_limitations": True,
                "stories": {"m1": 2},
                "strategy": {
                    "T": {"kind": "independent_bars"},
                    "G": {"envelope": "balanced", "loading": "double"},
                },
                "performance": {
                    "feasible": True,
                    "fits_limitations": True,
                    "lengths": [length],
                    "program_coherence": 0.7,
                    "preference_alignment": 0.5,
                    "performance_efficiency": 0.6,
                    "robustness": 0.4,
                },
                "plates": [
                    {
                        "mass_id": "m1",
                        "mass_name": "Wing A",
                        "stories": 2,
                        "width_ft": 60,
                        "length_ft": length,
                    }
                ],
            }

        archive = {
            "attempts": 2,
            "legal": 2,
            "cells": {"cA": cell("cA", 110.0), "cB": cell("cB", 140.0)},
        }
        session = SimpleNamespace(
            last_search=[],
            constraints={
                "briefing": {"requirements": [], "limitations": [], "preferences": []},
                "explore": {
                    "mode": "cover",
                    "kept_cell": "cA",
                    "archive": archive,
                    "learning": {
                        "weights": {},
                        "comparisons": [],
                        "pending_pair": {"a": "cA", "b": "cB", "kind": "contrast"},
                        "note": "No pairwise taste yet.",
                    },
                },
            },
            masses=[],
        )
        t = transparency_payload(session, full_explore=True)
        pair = t["learn_pair"]
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertEqual(pair["kind"], "contrast")
        self.assertEqual(pair["max_comparisons"], 4)
        self.assertEqual(pair["a"]["cell_id"], "cA")
        self.assertEqual(pair["b"]["cell_id"], "cB")
        self.assertEqual(pair["a"]["side"], "a")
        self.assertTrue(pair["a"]["preview"]["boxes"])
        self.assertTrue(pair["b"]["preview"]["boxes"])
        self.assertIn("program_coherence", pair["a"]["axes"])
        phases = [s["phase"] for s in t["process"]["steps"]]
        self.assertIn("LEARN", phases)
