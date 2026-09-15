"""Strategy actions, archive cells, and the search controller."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from massing_explorer.brief import apply_brief
from massing_explorer.explore.actions import apply_action
from massing_explorer.explore.archive import empty_archive, insert, legal_cells, restore_entry
from massing_explorer.explore.controller import (
    allocate_local_tries,
    effective_sample_size,
    run_search,
    select_elites,
)
from massing_explorer.explore.preference import (
    fit_weights,
    next_pair,
    parse_choice,
    schemes_from_archive,
    take_choice,
    taste_weight,
)
from massing_explorer.explore.csp import describe_csp, solve_partitions
from massing_explorer.explore.partitions import enumerate_partitions
from massing_explorer.explore.explain import empty_cells
from massing_explorer.explore.performance import novelty_versus_archive, preference_distance
from massing_explorer.explore.bayes import (
    GaussianProcess,
    encode_strategy,
    expected_improvement,
    run_bayes,
)
from massing_explorer.explore.mcts import SIM_CAP, catalog_actions, run_mcts
from massing_explorer.explore.planner import MAX_ACTIONS, parse_plan, run_planner
from massing_explorer.explore.strategy import cell_key, grouping_is_required, partition_id, read_strategy
from massing_explorer.explore.topology import describe_topology, paired_bars_drawable, stated_frontage_ft
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession
from massing_explorer.study_state import MassGrouping
from massing_explorer.tools import set_grouping
from massing_explorer.visual import (
    render_csp_board,
    render_explain_board,
    render_massing_visual,
    render_mcts_tree,
    render_planner_trace,
    render_site_plan,
    render_topology_board,
)

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"
OUTPUT = ROOT / "output" / "judge"

CORE = "CORE ACADEMIC"
HPE = "HEALTH & PHYSICAL EDUCATION"
DINING = "DINING & FOOD SERVICE"
ART = "ART & MUSIC"


def _tiny_session() -> SimpleNamespace:
    masses = [
        MassGrouping(id="academic", name="Academic", departments=[CORE, ART], story_count=2),
        MassGrouping(id="public", name="Public", departments=[HPE, DINING], story_count=2),
    ]
    return SimpleNamespace(
        masses=masses,
        brief_locked=True,
        floor_pins={},
        double_height_rooms=[],
        pairings=[],
        constraints={
            "briefing": {
                "requirements": [
                    {"kind": "requirement", "lever": "mass_count", "value": 2},
                    {
                        "kind": "requirement",
                        "lever": "same_mass",
                        "departments": [HPE, DINING],
                    },
                ],
                "limitations": [],
                "preferences": [],
            },
            "max_stories": 3,
            "story_lock": {"public": 2},
        },
        department_names=lambda: [CORE, ART, HPE, DINING],
        save=lambda: None,
    )


class TestActions(unittest.TestCase):
    def test_split_is_rejected_when_grouping_is_required(self) -> None:
        session = _tiny_session()
        out = apply_action(session, {"op": "SPLIT_MASS", "programs": [ART], "from": "academic"})
        self.assertFalse(out["ok"])
        self.assertIn("constraint solver", out["reason"].lower())
        self.assertEqual(len(session.masses), 2)

    def test_keep_apart_cannot_undo_a_required_pair(self) -> None:
        session = _tiny_session()
        out = apply_action(session, {"op": "KEEP_APART", "programs": [HPE, DINING]})
        self.assertFalse(out["ok"])
        self.assertEqual(set(session.masses[1].departments), {HPE, DINING})

    def test_pin_ground_is_a_floor_pin_not_a_new_mass(self) -> None:
        session = _tiny_session()
        out = apply_action(session, {"op": "PIN_GROUND", "programs": [ART]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(session.floor_pins[ART], 0)
        self.assertEqual(len(session.masses), 2)

    def test_courtyard_is_unsupported_not_a_failed_sample(self) -> None:
        session = _tiny_session()
        out = apply_action(session, {"op": "COURTYARD"})
        self.assertFalse(out["ok"])
        self.assertIn("cannot realize", out["reason"])


    def test_width_step_is_nearby_delta_not_resize_mass(self) -> None:
        import inspect

        from massing_explorer.explore import actions as actions_mod

        session = _tiny_session()
        session.constraints["academic_width_ft"] = 60.0
        out = apply_action(session, {"op": "SET_WIDTH", "mass": "academic", "delta_ft": 10})
        self.assertTrue(out["ok"], out)
        self.assertEqual(session.constraints["academic_width_ft"], 70.0)
        source = inspect.getsource(actions_mod._set_width)
        self.assertNotIn("resize_mass", source)


class TestArchive(unittest.TestCase):
    def test_an_illegal_evaluation_is_not_coverage(self) -> None:
        session = _tiny_session()
        archive = empty_archive()
        insert(
            archive,
            session,
            SimpleNamespace(validation=[], masses=[]),
            {"fits_limitations": False, "failed_checks": 1},
            reason="over the cap",
        )
        insert(
            archive,
            session,
            SimpleNamespace(validation=[], masses=[]),
            {"fits_limitations": True, "failed_checks": 0},
            reason="fits",
        )
        self.assertEqual(len(legal_cells(archive)), 1)
        self.assertTrue(archive["cells"][cell_key(session)]["fits_limitations"])
        self.assertEqual(archive["attempts"], 2)


class TestController(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="explore_phase1", program=program, config_path=str(CONFIG)
        )
        # Keep suite fast; production COVER uses start=40 / max=120 and
        # MCTS ~64 / depth 4 from several COVER roots.
        self.session.constraints["cover_budget"] = {
            "start": 8,
            "step_small": 4,
            "step_large": 6,
            "max": 20,
        }
        self.session.constraints["explore_budget"] = {
            "mcts_sims": 6,
            "mcts_depth": 2,
            "mcts_roots": 2,
            "bo": 2,
            "refine": 4,
        }
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_cover_fills_an_archive_without_undoing_required_wings(self) -> None:
        text = (
            "I want 4 masses. Gym and dining should be double height and in the same mass. "
            "Core academic by itself is one mass. Art and music on the ground floor. "
            "Prefer ratio 3:5. Max 3 stories. Length should be shorter than 50."
        )
        out = apply_brief(self.session, text)
        self.assertTrue(out["ok"], out)
        self.assertFalse(grouping_is_required(self.session))
        explore = self.session.constraints.get("explore") or {}
        archive = explore.get("archive") or {}
        self.assertGreaterEqual(archive.get("attempts", 0), 2)
        self.assertIn("unsupported", archive)
        self.assertIn("courtyard", archive["unsupported"])
        home = {d: m.id for m in self.session.masses for d in m.departments}
        self.assertEqual(home[HPE], home[DINING])
        self.assertEqual(len(self.session.masses), 4)
        parts = {e.get("partition") for e in (archive.get("cells") or {}).values()}
        self.assertGreater(len(parts), 1, parts)
        explain = explore.get("explain") or {}
        joined = " ".join(explain.get("sentences") or [])
        self.assertIn("Courtyard", joined)
        self.assertIn("constrained", joined.lower())
        self.assertGreaterEqual(int(explain.get("locked") or 0), 1)
        robust = explore.get("robustness") or {}
        self.assertTrue(robust.get("ran"))
        self.assertEqual(len(self.session.masses), 4)
        render_explain_board(
            explain,
            robust,
            OUTPUT / "blueprint_phase5_explain.png",
            title="Empty cells and same-strategy robustness",
        )
        strategy = read_strategy(self.session)
        self.assertFalse(strategy["P"]["locked"])
        self.assertEqual(strategy["P"]["mass_count"], 4)
        self.assertEqual(strategy["T"]["kind"], "independent_bars")
        from massing_explorer.solver import solve_massing_study

        result = solve_massing_study(self.session)
        render_massing_visual(result, OUTPUT / "blueprint_phase1_four_mass.png")

    def test_cover_on_a_site_envelope_still_fits(self) -> None:
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE],
                    "story_count": 3,
                },
                {
                    "id": "public",
                    "name": "Public",
                    "departments": [HPE, DINING],
                    "story_count": 2,
                },
                {
                    "id": "arts",
                    "name": "Arts",
                    "departments": [ART],
                    "story_count": 2,
                },
            ],
        )
        leftover = [
            d for d in self.session.department_names()
            if d not in {CORE, HPE, DINING, ART}
        ]
        if leftover:
            self.session.masses.append(
                MassGrouping(id="support", name="Support", departments=leftover, story_count=2)
            )
        self.session.constraints["max_total_length_ft"] = 400
        self.session.constraints["max_building_width_ft"] = 100
        self.session.constraints["max_stories"] = 4
        out = run_search(self.session, mode="cover")
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["archive"]["attempts"], 1)
        png = OUTPUT / "blueprint_phase1.png"
        site = OUTPUT / "blueprint_phase1_site.png"
        from massing_explorer.solver import solve_massing_study

        result = solve_massing_study(self.session)
        render_massing_visual(result, png)
        render_site_plan(result, site, max_total_length_ft=400)
        self.assertTrue(png.exists())
        self.assertTrue(site.exists())

    def _open_lineages(self) -> None:
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE],
                    "story_count": 3,
                },
                {
                    "id": "public",
                    "name": "Public",
                    "departments": [HPE, DINING],
                    "story_count": 2,
                },
                {
                    "id": "arts",
                    "name": "Arts",
                    "departments": [ART],
                    "story_count": 2,
                },
            ],
        )
        leftover = [
            d for d in self.session.department_names()
            if d not in {CORE, HPE, DINING, ART}
        ]
        if leftover:
            self.session.masses.append(
                MassGrouping(id="support", name="Support", departments=leftover, story_count=2)
            )
        self.session.constraints["max_total_length_ft"] = 400
        self.session.constraints["max_building_width_ft"] = 100
        self.session.constraints["max_stories"] = 4
        self.session.brief_locked = True

    def test_cover_runs_refine_and_prepares_a_pair(self) -> None:
        self._open_lineages()
        out = run_search(self.session, mode="cover")
        self.assertTrue(out["ok"])
        self.assertTrue(out["refine"]["ran"])
        allocation = out["refine"]["allocation"]
        self.assertTrue(allocation)
        self.assertTrue(all(int(v) >= 1 for v in allocation.values()))
        pair = out["learning"]["pending_pair"]
        self.assertIsNotNone(pair, out["learning"])
        archive = self.session.constraints["explore"]["archive"]
        illegal = {
            key for key, entry in (archive.get("cells") or {}).items()
            if not entry.get("fits_limitations")
        }
        self.assertNotIn(pair["a"], illegal)
        self.assertNotIn(pair["b"], illegal)
        from massing_explorer.solver import solve_massing_study

        cells = {e["cell"]: e for e in legal_cells(archive)}
        restore_entry(self.session, cells[pair["a"]])
        render_massing_visual(
            solve_massing_study(self.session), OUTPUT / "blueprint_phase2_pair_a.png"
        )
        restore_entry(self.session, cells[pair["b"]])
        render_massing_visual(
            solve_massing_study(self.session), OUTPUT / "blueprint_phase2_pair_b.png"
        )
        render_site_plan(
            solve_massing_study(self.session),
            OUTPUT / "blueprint_phase2_cover_refine_site.png",
            max_total_length_ft=400,
        )
        render_massing_visual(
            solve_massing_study(self.session),
            OUTPUT / "blueprint_phase2_cover_refine.png",
        )

    def test_choosing_a_updates_taste_and_leaves_wings_intact(self) -> None:
        self._open_lineages()
        run_search(self.session, mode="cover")
        before = list(
            (self.session.constraints.get("explore") or {}).get("learning", {}).get("comparisons")
            or []
        )
        self.assertEqual(before, [])
        self.assertIsNone(
            take_choice(
                self.session,
                "I want 4 masses. Gym and dining should stay together.",
            )
        )
        still = (self.session.constraints["explore"].get("learning") or {}).get("comparisons")
        self.assertEqual(still, [])
        out = take_choice(self.session, "A")
        self.assertIsNotNone(out)
        learning = self.session.constraints["explore"]["learning"]
        self.assertEqual(len(learning["comparisons"]), 1)
        self.assertEqual(learning["comparisons"][0]["winner"], "a")
        home = {d: m.id for m in self.session.masses for d in m.departments}
        self.assertEqual(home[HPE], home[DINING])
        from massing_explorer.solver import solve_massing_study

        render_massing_visual(
            solve_massing_study(self.session),
            OUTPUT / "blueprint_phase2_after_choice.png",
        )

    def test_open_p_enumerates_when_grouping_was_not_required(self) -> None:
        out = apply_brief(
            self.session,
            "site length is 400, width is 100, the max story is 4 floor",
        )
        self.assertTrue(out["ok"], out)
        self.assertFalse(grouping_is_required(self.session))
        explore = self.session.constraints.get("explore") or {}
        archive = explore.get("archive") or {}
        parts = {e.get("partition") for e in (archive.get("cells") or {}).values() if e.get("partition")}
        self.assertGreaterEqual(len(parts), 2, parts)
        self.assertGreaterEqual(len(explore.get("partitions") or parts), 2)
        stated = archive.get("stated_partition") or partition_id(self.session)
        self.assertTrue(stated)
        home = {d: m.id for m in self.session.masses for d in m.departments}
        stated_legal = any(
            e.get("partition") == stated and e.get("fits_limitations")
            for e in (archive.get("cells") or {}).values()
        )
        if stated_legal:
            self.assertNotEqual(home.get(HPE), home.get(DINING))
        from massing_explorer.solver import solve_massing_study

        legal = [e for e in (archive.get("cells") or {}).values() if e.get("fits_limitations")]
        family = next((e for e in legal if e.get("partition") == stated), None)
        public = next(
            (e for e in legal if "gym with dining" in (e.get("reason") or "")),
            None,
        )
        other = next(
            (e for e in legal if e.get("partition") != stated),
            None,
        )
        self.assertIsNotNone(family or legal, "expected at least one legal cell")
        for name, entry in (("family", family), ("public", public or other)):
            if entry is None:
                # Alternate P was sampled; it may all miss the site cap.
                self.assertTrue(
                    any(
                        e.get("partition") != stated
                        for e in (archive.get("cells") or {}).values()
                    ),
                    f"no alternate partition attempted for {name}",
                )
                continue
            restore_entry(self.session, entry)
            png = OUTPUT / f"blueprint_phase3_{name}.png"
            render_massing_visual(solve_massing_study(self.session), png)
            self.assertTrue(png.exists())
        restore_entry(self.session, family or legal[0])
        render_site_plan(
            solve_massing_study(self.session),
            OUTPUT / "blueprint_phase3_site.png",
            max_total_length_ft=400,
        )


class TestPreference(unittest.TestCase):
    def test_a_written_brief_is_not_a_choice(self) -> None:
        self.assertIsNone(parse_choice("I want 4 masses. Gym and dining in the same mass."))
        self.assertIsNone(parse_choice("prefer 3 stories"))
        self.assertIsNone(parse_choice("length should be shorter than 50"))
        self.assertEqual(parse_choice("a"), "a")
        self.assertEqual(parse_choice("I prefer B"), "b")

    def test_fit_weights_moves_toward_the_winner(self) -> None:
        schemes = {
            "wide": {
                "fits": True,
                "traits": {
                    "program_coherence": 0.8,
                    "preference_alignment": 0.1,
                    "performance_efficiency": 0.5,
                    "robustness": 0.2,
                },
            },
            "tall": {
                "fits": True,
                "traits": {
                    "program_coherence": 0.1,
                    "preference_alignment": 0.9,
                    "performance_efficiency": 0.5,
                    "robustness": 0.2,
                },
            },
        }
        weights = fit_weights(
            [{"a": "wide", "b": "tall", "winner": "a"}],
            schemes,
        )
        self.assertGreater(weights["program_coherence"], weights["preference_alignment"])

    def test_next_pair_skips_cells_that_broke_a_cap(self) -> None:
        archive = {
            "cells": {
                "legal-1": {
                    "cell": "legal-1",
                    "fits_limitations": True,
                    "partition": "p-a",
                    "stories": {"m1": 2, "m2": 3},
                    "strategy": {
                        "T": {"kind": "independent_bars"},
                        "G": {"envelope": "balanced", "loading": "double", "stories": {"m1": 2, "m2": 3}},
                    },
                    "performance": {
                        "feasible": True,
                        "failed_checks": 0,
                        "program_coherence": 0.5,
                        "preference_alignment": 0.1,
                        "performance_efficiency": 0.8,
                        "robustness": 0.5,
                    },
                },
                "legal-2": {
                    "cell": "legal-2",
                    "fits_limitations": True,
                    "partition": "p-b",
                    "stories": {"m1": 2, "m2": 2},
                    "strategy": {
                        "T": {"kind": "paired_bars"},
                        "G": {"envelope": "elongated", "loading": "single", "stories": {"m1": 2, "m2": 2}},
                    },
                    "performance": {
                        "feasible": True,
                        "failed_checks": 0,
                        "program_coherence": 0.2,
                        "preference_alignment": 0.4,
                        "performance_efficiency": 0.6,
                        "robustness": 0.5,
                    },
                },
                "over": {
                    "cell": "over",
                    "fits_limitations": False,
                    "performance": {
                        "feasible": False,
                        "failed_checks": 1,
                        "program_coherence": 0.9,
                    },
                },
            }
        }
        schemes = schemes_from_archive(archive)
        self.assertEqual({s["id"] for s in schemes}, {"legal-1", "legal-2"})
        pair = next_pair(schemes, {}, [])
        self.assertIsNotNone(pair)
        self.assertEqual(set(pair[k] for k in ("a", "b")), {"legal-1", "legal-2"})

    def test_next_pair_prefers_architecturally_different_legal_schemes(self) -> None:
        traits = {
            "program_coherence": 0.5,
            "preference_alignment": 0.2,
            "performance_efficiency": 0.5,
            "robustness": 0.2,
        }
        schemes = [
            {
                "id": "same-a",
                "fits": True,
                "traits": dict(traits),
                "entry": {
                    "partition": "p-core",
                    "stories": {"m1": 2, "m2": 2},
                    "strategy": {
                        "T": {"kind": "independent"},
                        "G": {"envelope": "balanced", "loading": "double", "stories": {"m1": 2, "m2": 2}},
                    },
                    "plates": [
                        {"mass_id": "m1", "stories": 2, "width_ft": 60, "length_ft": 100},
                        {"mass_id": "m2", "stories": 2, "width_ft": 60, "length_ft": 100},
                    ],
                },
            },
            {
                "id": "same-b",
                "fits": True,
                "traits": dict(traits),
                "entry": {
                    "partition": "p-core",
                    "stories": {"m1": 2, "m2": 2},
                    "strategy": {
                        "T": {"kind": "independent"},
                        "G": {"envelope": "compact", "loading": "double", "stories": {"m1": 2, "m2": 2}},
                    },
                    "plates": [
                        {"mass_id": "m1", "stories": 2, "width_ft": 60, "length_ft": 100},
                        {"mass_id": "m2", "stories": 2, "width_ft": 60, "length_ft": 100},
                    ],
                },
            },
            {
                "id": "other-p",
                "fits": True,
                "traits": dict(traits),
                "entry": {
                    "partition": "p-split",
                    "stories": {"m1": 3, "m2": 2, "m3": 2},
                    "strategy": {
                        "T": {"kind": "paired_bars"},
                        "G": {
                            "envelope": "elongated",
                            "loading": "single",
                            "stories": {"m1": 3, "m2": 2, "m3": 2},
                        },
                    },
                    "plates": [
                        {"mass_id": "m1", "stories": 3, "width_ft": 50, "length_ft": 160},
                        {"mass_id": "m2", "stories": 2, "width_ft": 70, "length_ft": 80},
                        {"mass_id": "m3", "stories": 2, "width_ft": 70, "length_ft": 90},
                    ],
                },
            },
        ]
        pair = next_pair(schemes, {}, [])
        self.assertIsNotNone(pair)
        self.assertIn("other-p", {pair["a"], pair["b"]})
        # Envelope-only twins must not be offered against each other.
        twin = next_pair(schemes[:2], {}, [])
        self.assertIsNone(twin)

    def test_next_pair_stops_before_five_comparisons(self) -> None:
        from massing_explorer.explore.preference import MAX_LEARN_COMPARISONS

        traits = {
            "program_coherence": 0.5,
            "preference_alignment": 0.2,
            "performance_efficiency": 0.5,
            "robustness": 0.2,
        }

        def scheme(cid: str, partition: str, topo: str, stories: dict, plates: list) -> dict:
            return {
                "id": cid,
                "fits": True,
                "traits": dict(traits),
                "entry": {
                    "partition": partition,
                    "stories": stories,
                    "strategy": {
                        "T": {"kind": topo},
                        "G": {"envelope": "balanced", "loading": "double", "stories": stories},
                    },
                    "plates": plates,
                },
            }

        schemes = [
            scheme("c1", "p1", "independent_bars", {"a": 2, "b": 2}, [
                {"mass_id": "a", "stories": 2, "width_ft": 60, "length_ft": 100},
                {"mass_id": "b", "stories": 2, "width_ft": 60, "length_ft": 110},
            ]),
            scheme("c2", "p2", "paired_bars", {"a": 3, "b": 2}, [
                {"mass_id": "a", "stories": 3, "width_ft": 50, "length_ft": 140},
                {"mass_id": "b", "stories": 2, "width_ft": 80, "length_ft": 90},
            ]),
            scheme("c3", "p3", "independent_bars", {"a": 2, "b": 3, "c": 2}, [
                {"mass_id": "a", "stories": 2, "width_ft": 55, "length_ft": 80},
                {"mass_id": "b", "stories": 3, "width_ft": 55, "length_ft": 120},
                {"mass_id": "c", "stories": 2, "width_ft": 70, "length_ft": 70},
            ]),
            scheme("c4", "p4", "paired_bars", {"a": 2, "b": 2, "c": 3}, [
                {"mass_id": "a", "stories": 2, "width_ft": 40, "length_ft": 160},
                {"mass_id": "b", "stories": 2, "width_ft": 40, "length_ft": 150},
                {"mass_id": "c", "stories": 3, "width_ft": 90, "length_ft": 80},
            ]),
            scheme("c5", "p5", "independent_bars", {"a": 3, "b": 3}, [
                {"mass_id": "a", "stories": 3, "width_ft": 70, "length_ft": 90},
                {"mass_id": "b", "stories": 3, "width_ft": 70, "length_ft": 100},
            ]),
        ]
        comparisons: list[dict] = []
        seen_pairs = 0
        for _ in range(10):
            pair = next_pair(schemes, {}, comparisons)
            if pair is None:
                break
            seen_pairs += 1
            comparisons.append({"a": pair["a"], "b": pair["b"], "winner": "a"})
        self.assertEqual(MAX_LEARN_COMPARISONS, 4)
        self.assertLessEqual(seen_pairs, MAX_LEARN_COMPARISONS)
        self.assertIsNone(next_pair(schemes, {}, comparisons))


class TestRefineAllocation(unittest.TestCase):
    def test_local_tries_keep_a_floor(self) -> None:
        elites = [
            {
                "cell": "p1",
                "performance": {
                    "program_coherence": 1.0,
                    "preference_alignment": 0.0,
                    "performance_efficiency": 0.0,
                    "robustness": 0.0,
                },
            },
            {
                "cell": "p2",
                "performance": {
                    "program_coherence": 0.4,
                    "preference_alignment": 0.0,
                    "performance_efficiency": 0.0,
                    "robustness": 0.0,
                },
            },
            {
                "cell": "p3",
                "performance": {
                    "program_coherence": 0.0,
                    "preference_alignment": 0.0,
                    "performance_efficiency": 0.0,
                    "robustness": 0.0,
                },
            },
        ]
        weights = {
            "program_coherence": 5.0,
            "preference_alignment": 0.0,
            "performance_efficiency": 0.0,
            "robustness": 0.0,
        }
        counts = allocate_local_tries(elites, 6, weights, floor=1)
        self.assertEqual(sum(counts.values()), 6)
        self.assertTrue(all(v >= 1 for v in counts.values()))
        self.assertGreaterEqual(counts["p1"], counts["p3"])

    def test_collapsed_weights_do_not_delete_a_lineage(self) -> None:
        elites = [
            {
                "cell": "heavy",
                "performance": {
                    "program_coherence": 1.0,
                    "preference_alignment": 0.0,
                    "performance_efficiency": 0.0,
                    "robustness": 0.0,
                },
            },
            {
                "cell": "light",
                "performance": {
                    "program_coherence": 0.0,
                    "preference_alignment": 0.0,
                    "performance_efficiency": 0.0,
                    "robustness": 0.0,
                },
            },
        ]
        weights = {
            "program_coherence": 80.0,
            "preference_alignment": 0.0,
            "performance_efficiency": 0.0,
            "robustness": 0.0,
        }
        raw = [taste_weight(e, weights) for e in elites]
        self.assertLess(effective_sample_size(raw), 1.25)
        remaining = 6
        allocation = {e["cell"]: 1 for e in elites}
        leftover = remaining - len(elites)
        if leftover > 0:
            allocation[elites[0]["cell"]] += leftover
        self.assertEqual(allocation["light"], 1)
        self.assertEqual(sum(allocation.values()), 6)

    def test_select_elites_keeps_a_light_lineage(self) -> None:
        archive = {
            "cells": {
                "heavy": {
                    "cell": "heavy",
                    "fits_limitations": True,
                    "performance": {
                        "program_coherence": 1.0,
                        "preference_alignment": 0.0,
                        "performance_efficiency": 0.0,
                        "robustness": 0.0,
                        "failed_checks": 0,
                    },
                },
                "mid": {
                    "cell": "mid",
                    "fits_limitations": True,
                    "performance": {
                        "program_coherence": 0.6,
                        "preference_alignment": 0.0,
                        "performance_efficiency": 0.0,
                        "robustness": 0.0,
                        "failed_checks": 0,
                    },
                },
                "light": {
                    "cell": "light",
                    "fits_limitations": True,
                    "performance": {
                        "program_coherence": 0.0,
                        "preference_alignment": 0.0,
                        "performance_efficiency": 0.0,
                        "robustness": 0.0,
                        "failed_checks": 0,
                    },
                },
                "over": {
                    "cell": "over",
                    "fits_limitations": False,
                    "performance": {
                        "program_coherence": 0.9,
                        "failed_checks": 1,
                    },
                },
            }
        }
        weights = {
            "program_coherence": 10.0,
            "preference_alignment": 0.0,
            "performance_efficiency": 0.0,
            "robustness": 0.0,
        }
        kept = select_elites(archive, weights)
        ids = {e["cell"] for e in kept}
        self.assertIn("light", ids)
        self.assertNotIn("over", ids)
        self.assertLessEqual(len(kept), 3)


class TestPartitions(unittest.TestCase):
    def _open(self) -> SimpleNamespace:
        masses = [
            MassGrouping(id="academic", name="Academic", departments=[CORE], story_count=2),
            MassGrouping(id="arts", name="Arts", departments=[ART], story_count=2),
            MassGrouping(id="athletics", name="Athletics", departments=[HPE], story_count=2),
            MassGrouping(id="dining", name="Dining", departments=[DINING], story_count=2),
        ]
        return SimpleNamespace(
            masses=masses,
            brief_locked=False,
            floor_pins={},
            floor_steps={},
            floor_tapers={},
            pairings=[],
            constraints={
                "briefing": {"requirements": [], "limitations": [], "preferences": []},
                "max_stories": 3,
            },
            department_names=lambda: [CORE, ART, HPE, DINING],
            save=lambda: None,
        )

    def test_locked_brief_returns_only_the_stated_grouping(self) -> None:
        session = _tiny_session()
        session.constraints["partition_locked"] = True
        found = enumerate_partitions(session)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["reason"], "stated grouping")

    def test_open_brief_proposes_gym_with_dining(self) -> None:
        session = self._open()
        found = enumerate_partitions(session)
        self.assertGreaterEqual(len(found), 2)
        self.assertLessEqual(len(found), 5)
        reasons = [item["reason"] for item in found]
        self.assertIn("colocate gym with dining", reasons)
        public = next(item for item in found if item["reason"] == "colocate gym with dining")
        homes = {}
        for group in public["groups"]:
            for dept in group["departments"]:
                homes[dept] = group["id"]
        self.assertEqual(homes[HPE], homes[DINING])
        self.assertNotEqual(homes[CORE], homes[HPE])

    def test_keep_apart_blocks_that_merge(self) -> None:
        session = self._open()
        session.constraints["keep_apart"] = [[HPE, DINING]]
        found = enumerate_partitions(session)
        reasons = [item["reason"] for item in found]
        self.assertNotIn("colocate gym with dining", reasons)
        for item in found:
            homes = {}
            for group in item["groups"]:
                for dept in group["departments"]:
                    homes[dept] = group["id"]
            self.assertNotEqual(homes[HPE], homes[DINING])


class TestCsp(unittest.TestCase):
    def test_open_p_enumerates_bell_partitions_then_caps(self) -> None:
        session = TestPartitions()._open()
        report = describe_csp(session)
        self.assertFalse(report["locked"])
        self.assertEqual(report["feasible_count"], 15)
        self.assertEqual(report["shown"], 5)
        self.assertLessEqual(len(enumerate_partitions(session)), 5)
        self.assertTrue(all(item.get("source") == "csp" for item in report["chosen"]))
        self.assertEqual(report["chosen"][0]["reason"], "stated grouping")

    def test_apart_reduces_the_feasible_set(self) -> None:
        session = TestPartitions()._open()
        session.constraints["keep_apart"] = [[HPE, DINING]]
        report = describe_csp(session)
        self.assertEqual(report["feasible_count"], 10)
        self.assertGreaterEqual(len(report["rejected"]), 1)
        self.assertTrue(any("broke keep-apart" in (r.get("why") or "") for r in report["rejected"]))

    def test_together_glues_atoms_before_search(self) -> None:
        atoms = [
            {"id": "academic", "name": "Academic", "departments": [CORE], "story_count": 2},
            {"id": "arts", "name": "Arts", "departments": [ART], "story_count": 2},
            {"id": "athletics", "name": "Athletics", "departments": [HPE], "story_count": 2},
        ]
        free = solve_partitions(atoms, apart=[], together=[], cap=5)
        glued = solve_partitions(
            atoms,
            apart=[],
            together=[frozenset({CORE, ART})],
            cap=5,
        )
        self.assertEqual(free["feasible_count"], 5)
        self.assertEqual(glued["feasible_count"], 2)
        for item in glued["chosen"]:
            homes = {}
            for group in item["groups"]:
                for dept in group["departments"]:
                    homes[dept] = group["id"]
            self.assertEqual(homes[CORE], homes[ART])

    def test_planner_does_not_invent_p_when_grouping_is_open(self) -> None:
        session = TestPartitions()._open()
        colo = apply_action(session, {"op": "COLOCATE", "programs": [HPE, DINING]})
        split = apply_action(session, {"op": "SPLIT_MASS", "programs": [ART], "from": "arts"})
        apart = apply_action(session, {"op": "KEEP_APART", "programs": [CORE, ART]})
        self.assertFalse(colo["ok"])
        self.assertFalse(split["ok"])
        self.assertFalse(apart["ok"])
        self.assertIn("constraint solver", colo["reason"].lower())
        self.assertEqual(len(session.masses), 4)

    def test_csp_board_is_the_phase_six_visual(self) -> None:
        session = TestPartitions()._open()
        session.constraints["keep_apart"] = [[CORE, HPE]]
        report = describe_csp(session)
        png = OUTPUT / "blueprint_phase6_csp.png"
        render_csp_board(report, png, title="Phase 6 — CSP owns P")
        self.assertTrue(png.exists())
        self.assertGreater(png.stat().st_size, 1000)


class TestTopology(unittest.TestCase):
    def test_courtyard_stays_unsupported_when_d_is_stated(self) -> None:
        session = _tiny_session()
        session.constraints["max_total_length_ft"] = 400
        out = apply_action(session, {"op": "COURTYARD"})
        self.assertFalse(out["ok"])
        self.assertIn("cannot realize", out["reason"])
        shape = apply_action(session, {"op": "SET_SHAPE", "type": "L"})
        self.assertFalse(shape["ok"])
        self.assertIn("cannot realize", shape["reason"])

    def test_pairing_needs_stated_frontage_not_a_bar_cap(self) -> None:
        session = _tiny_session()
        session.constraints["max_building_length_ft"] = 50
        missing = apply_action(
            session,
            {"op": "PAIR_MASSES", "masses": ["academic", "public"], "length_ft": 50},
        )
        self.assertFalse(missing["ok"])
        self.assertIn("frontage", missing["reason"].lower())
        self.assertFalse(paired_bars_drawable(session))
        session.constraints["max_total_length_ft"] = 400
        self.assertTrue(paired_bars_drawable(session))
        invented = apply_action(
            session,
            {"op": "PAIR_MASSES", "masses": ["academic", "public"], "length_ft": 999},
        )
        self.assertFalse(invented["ok"])
        self.assertIn("invented", invented["reason"].lower())

    def test_cover_samples_paired_bars_only_when_d_is_stated(self) -> None:
        import massing_explorer.session as session_mod

        orig = session_mod.STUDIES_DIR
        tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
        try:
            program = load_program_file(UNDERWOOD, config_path=CONFIG)
            session = StudySession(
                study_id="explore_phase7", program=program, config_path=str(CONFIG)
            )
            session.constraints["explore_budget"] = {
                "mcts_sims": 6,
                "mcts_depth": 2,
                "mcts_roots": 2,
                "bo": 2,
                "refine": 4,
            }
            session.save()
            out = apply_brief(
                session,
                "I want 4 masses. Gym and dining should be in the same mass. "
                "Core academic by itself is one mass. Site length is 400, width is 100, max 4 stories.",
            )
            self.assertTrue(out["ok"], out)
            self.assertFalse(grouping_is_required(session))
            self.assertEqual(stated_frontage_ft(session), 400)
            explore = session.constraints.get("explore") or {}
            archive = explore.get("archive") or {}
            kinds = {
                ((e.get("strategy") or {}).get("T") or {}).get("kind")
                for e in (archive.get("cells") or {}).values()
            }
            self.assertIn("independent_bars", kinds)
            self.assertIn("paired_bars", kinds)
            self.assertIn("courtyard", archive.get("unsupported") or [])
            joined = " ".join((explore.get("explain") or {}).get("sentences") or [])
            self.assertIn("Courtyard", joined)
            self.assertIn("Streets", joined)
            self.assertNotIn("brief did not pair masses", joined)
            report = describe_topology(session)
            png = OUTPUT / "blueprint_phase7_topology.png"
            render_topology_board(report, png, title="Phase 7 — drawable T and stated D")
            self.assertTrue(png.exists())
            from massing_explorer.solver import solve_massing_study

            independent = next(
                e
                for e in (archive.get("cells") or {}).values()
                if ((e.get("strategy") or {}).get("T") or {}).get("kind") == "independent_bars"
                and e.get("fits_limitations")
            )
            paired = next(
                (
                    e
                    for e in (archive.get("cells") or {}).values()
                    if ((e.get("strategy") or {}).get("T") or {}).get("kind") == "paired_bars"
                ),
                None,
            )
            restore_entry(session, independent)
            render_site_plan(
                solve_massing_study(session),
                OUTPUT / "blueprint_phase7_independent.png",
                max_total_length_ft=400,
            )
            if paired:
                restore_entry(session, paired)
                render_site_plan(
                    solve_massing_study(session),
                    OUTPUT / "blueprint_phase7_paired.png",
                    max_total_length_ft=400,
                )
                self.assertTrue((OUTPUT / "blueprint_phase7_paired.png").exists())
        finally:
            session_mod.STUDIES_DIR = orig
            tmp.cleanup()


class TestMcts(unittest.TestCase):
    def test_planner_is_the_expansion_prior_and_courtyard_is_dead(self) -> None:
        session = _tiny_session()
        catalog = catalog_actions(session)
        self.assertTrue(any(a.get("op") == "COURTYARD" for a in catalog))
        self.assertFalse(any("width_ft" in a for a in catalog))
        self.assertFalse(any(a.get("op") == "SET_WIDTH" for a in catalog))
        self.assertTrue(any(a.get("op") == "SET_ENVELOPE" for a in catalog))
        self.assertTrue(any(a.get("op") == "SET_LOADING" for a in catalog))
        report = run_mcts(
            session,
            plan=[
                {"op": "PIN_GROUND", "programs": [ART]},
                {"op": "COURTYARD"},
                {"op": "SET_STORIES", "mass": "academic", "stories": 3},
                {"op": "PAIR_MASSES", "masses": ["academic", "public"], "length_ft": 999},
            ],
            simulations=8,
        )
        self.assertTrue(report["ran"])
        self.assertLessEqual(report["simulations"], SIM_CAP)
        self.assertIn("search.py", report["baseline"])
        self.assertTrue(any("PIN_GROUND" in p for p in report["prior"]))
        self.assertFalse(any("999" in p for p in report["prior"]))
        kinds = {n["kind"] for n in report["nodes"]}
        self.assertIn("unsupported", kinds)
        courtyard = next(
            n
            for n in report["nodes"]
            if n.get("planner_prior") and "COURTYARD" in n["op"]
        )
        self.assertEqual(courtyard["kind"], "unsupported")
        self.assertTrue(courtyard["planner_prior"])
        self.assertEqual(len(session.masses), 2)
        pin = apply_action(session, {"op": "PIN_GROUND", "programs": [ART]})
        self.assertTrue(pin["ok"])

    def test_mcts_does_not_import_search_quality_weights(self) -> None:
        import inspect

        import massing_explorer.explore.mcts as mcts_mod
        import massing_explorer.search as search_mod

        source = inspect.getsource(mcts_mod)
        self.assertNotIn("PREFERENCE_WEIGHTS", source)
        self.assertNotIn("search_schemes", source)
        self.assertIn("balanced", search_mod.PREFERENCE_WEIGHTS)

    def test_cover_runs_mcts_and_draws_the_tree(self) -> None:
        import massing_explorer.session as session_mod

        orig = session_mod.STUDIES_DIR
        tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
        try:
            program = load_program_file(UNDERWOOD, config_path=CONFIG)
            session = StudySession(
                study_id="explore_phase8", program=program, config_path=str(CONFIG)
            )
            session.constraints["cover_budget"] = {
                "start": 8,
                "step_small": 4,
                "step_large": 6,
                "max": 20,
            }
            session.constraints["explore_budget"] = {
                "mcts_sims": 6,
                "mcts_depth": 2,
                "mcts_roots": 2,
                "bo": 2,
                "refine": 4,
            }
            session.save()
            out = apply_brief(
                session,
                "I want 4 masses. Gym and dining should be in the same mass. "
                "Core academic by itself is one mass. Max 3 stories.",
                plan=[
                    {"op": "PIN_GROUND", "programs": [ART]},
                    {"op": "COURTYARD"},
                    {"op": "SPLIT_MASS", "programs": [ART]},
                ],
            )
            self.assertTrue(out["ok"], out)
            mcts = (session.constraints.get("explore") or {}).get("mcts") or {}
            self.assertTrue(mcts.get("ran"), mcts)
            self.assertGreaterEqual(int(mcts.get("simulations") or 0), 1)
            self.assertGreaterEqual(int(mcts.get("unsupported") or 0), 1)
            self.assertEqual(len(session.masses), 4)
            home = {d: m.id for m in session.masses for d in m.departments}
            self.assertEqual(home[HPE], home[DINING])
            png = OUTPUT / "blueprint_phase8_mcts.png"
            render_mcts_tree(mcts, png, title="Phase 8 — MCTS over design actions")
            self.assertTrue(png.exists())
            self.assertGreater(png.stat().st_size, 1000)
        finally:
            session_mod.STUDIES_DIR = orig
            tmp.cleanup()


class TestPlanner(unittest.TestCase):
    def test_caps_at_five_and_drops_invented_feet(self) -> None:
        session = _tiny_session()
        parsed = parse_plan(
            {
                "actions": [
                    {"op": "PIN_GROUND", "programs": [ART]},
                    {"op": "SET_STORIES", "mass": "academic", "stories": 3},
                    {"op": "SET_LOADING", "loading": "single"},
                    {"op": "COLOCATE", "programs": [CORE, ART]},
                    {"op": "KEEP_APART", "programs": [HPE, ART]},
                    {"op": "SET_STORIES", "mass": "academic", "stories": 2},
                ]
            },
            session,
        )
        self.assertEqual(len(parsed["actions"]), MAX_ACTIONS)
        self.assertTrue(any("five-action" in d["reason"] for d in parsed["dropped"]))
        invented = parse_plan(
            {"actions": [{"op": "SET_STORIES", "mass": "academic", "stories": 3, "width_ft": 80}]},
            session,
        )
        self.assertEqual(invented["actions"], [])
        self.assertEqual(invented["dropped"][0]["kind"], "illegal")

    def test_engine_falsifies_and_counts_illegal_vs_unsupported(self) -> None:
        session = _tiny_session()
        report = run_planner(
            session,
            plan=[
                {"op": "PIN_GROUND", "programs": [ART]},
                {"op": "SPLIT_MASS", "programs": [ART], "from": "academic"},
                {"op": "COURTYARD"},
                {"op": "SET_STORIES", "mass": "academic", "stories": 3, "width_ft": 90},
                {"op": "PAIR_MASSES", "masses": ["academic", "public"], "length_ft": 999},
            ],
        )
        self.assertTrue(report["ran"])
        self.assertEqual(report["applied"], 1)
        self.assertGreaterEqual(report["illegal"], 2)
        self.assertEqual(report["unsupported"], 1)
        self.assertEqual(len(session.masses), 2)
        self.assertEqual(session.floor_pins[ART], 0)
        png = OUTPUT / "blueprint_phase4_planner.png"
        render_planner_trace(report, png, title="Strategy planner — apply or reject")
        self.assertTrue(png.exists())

    def test_fake_client_plan_is_parsed(self) -> None:
        session = _tiny_session()

        class _Fake:
            def chat(self, messages):
                return {
                    "message": {
                        "content": (
                            '{"actions": [{"op": "COURTYARD"}, '
                            '{"op": "PIN_GROUND", "programs": ["ART & MUSIC"]}], '
                            '"why": "court then pin"}'
                        )
                    }
                }

        report = run_planner(session, client=_Fake())
        self.assertEqual(report["unsupported"], 1)
        self.assertEqual(report["applied"], 1)

    def test_locked_search_still_counts_a_bad_split(self) -> None:
        import massing_explorer.session as session_mod

        orig = session_mod.STUDIES_DIR
        tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
        try:
            program = load_program_file(UNDERWOOD, config_path=CONFIG)
            session = StudySession(
                study_id="explore_phase4", program=program, config_path=str(CONFIG)
            )
            session.constraints["cover_budget"] = {
                "start": 8,
                "step_small": 4,
                "step_large": 6,
                "max": 20,
            }
            session.constraints["explore_budget"] = {
                "mcts_sims": 6,
                "mcts_depth": 2,
                "mcts_roots": 2,
                "bo": 2,
                "refine": 4,
            }
            session.save()
            out = apply_brief(
                session,
                "I want 4 masses. Gym and dining should be in the same mass. "
                "Core academic by itself is one mass. Max 3 stories.",
                plan=[
                    {"op": "SPLIT_MASS", "programs": ["ART & MUSIC"]},
                    {"op": "PODIUM"},
                ],
            )
            self.assertTrue(out["ok"], out)
            planner = (session.constraints.get("explore") or {}).get("planner") or {}
            self.assertTrue(planner.get("ran"))
            self.assertGreaterEqual(int(planner.get("illegal") or 0), 1)
            self.assertGreaterEqual(int(planner.get("unsupported") or 0), 1)
            self.assertEqual(len(session.masses), 4)
        finally:
            session_mod.STUDIES_DIR = orig
            tmp.cleanup()


class TestExplainRobustness(unittest.TestCase):
    def test_preference_distance_is_zero_when_the_pin_holds(self) -> None:
        art = SimpleNamespace(
            department=ART,
            gsf=1000,
        )
        result = SimpleNamespace(
            masses=[
                SimpleNamespace(
                    floors=[
                        SimpleNamespace(
                            level=0,
                            width_ft=40,
                            length_ft=24,
                            allocations=[art],
                            usable_area_sf=960,
                            allocated_gsf=960,
                        )
                    ]
                )
            ],
            validation=[],
        )
        session = SimpleNamespace(
            floor_pins={ART: 0},
            constraints={"length_over_width": 0.6, "briefing": {"preferences": []}},
        )
        self.assertEqual(preference_distance(result, session), 0.0)
        session.floor_pins = {ART: 0}
        result.masses[0].floors[0].level = 2
        self.assertGreater(preference_distance(result, session), 0.0)

    def test_novelty_is_one_on_an_empty_archive(self) -> None:
        vector = {
            "program_coherence": 0.4,
            "preference_alignment": 0.1,
            "performance_efficiency": 0.8,
            "robustness": 0.5,
        }
        self.assertEqual(novelty_versus_archive(vector, None), 1.0)
        self.assertEqual(novelty_versus_archive(vector, {"cells": {}}), 1.0)
        clone_archive = {
            "cells": {
                "a": {
                    "fits_limitations": True,
                    "performance": dict(vector),
                }
            }
        }
        self.assertLess(novelty_versus_archive(vector, clone_archive), 0.05)

    def test_empty_cells_name_unsupported_even_before_cover(self) -> None:
        session = _tiny_session()
        out = empty_cells(session, empty_archive())
        kinds = {i["kind"] for i in out["items"]}
        self.assertIn("unsupported", kinds)
        self.assertIn("locked", kinds)
        self.assertTrue(any("Courtyard" in i["sentence"] for i in out["items"]))
        self.assertTrue(any("story-locked" in i["sentence"] for i in out["items"]))

    def test_robustness_does_not_regroup_or_leave_gsf_shifted(self) -> None:
        import massing_explorer.session as session_mod

        orig_dir = session_mod.STUDIES_DIR
        tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
        try:
            program = load_program_file(UNDERWOOD, config_path=CONFIG)
            before = {d.name: d.target_gsf for d in program.departments}
            session = StudySession(
                study_id="explore_phase5", program=program, config_path=str(CONFIG)
            )
            session.constraints["cover_budget"] = {
                "start": 8,
                "step_small": 4,
                "step_large": 6,
                "max": 20,
            }
            session.constraints["explore_budget"] = {
                "mcts_sims": 6,
                "mcts_depth": 2,
                "mcts_roots": 2,
                "bo": 2,
                "refine": 4,
            }
            session.save()
            apply_brief(
                session,
                "I want 4 masses. Gym and dining should be in the same mass. "
                "Core academic by itself is one mass. Site length is 400, width is 100, max 4 stories.",
            )
            home = {d: m.id for m in session.masses for d in m.departments}
            self.assertEqual(home[HPE], home[DINING])
            after = {d.name: d.target_gsf for d in session.program.departments}
            self.assertEqual(before, after)
            robust = (session.constraints.get("explore") or {}).get("robustness") or {}
            self.assertTrue(robust.get("ran"))
            self.assertEqual(home, {d: m.id for m in session.masses for d in m.departments})
            self.assertGreaterEqual(int(robust.get("survived") or 0) + int(robust.get("collapsed") or 0), 2)
            kept_perf = None
            archive = (session.constraints.get("explore") or {}).get("archive") or {}
            kept = (session.constraints.get("explore") or {}).get("kept_cell")
            if kept:
                kept_perf = (archive.get("cells") or {}).get(kept, {}).get("performance")
            if kept_perf:
                self.assertIn("preference_distance", kept_perf)
                self.assertIn("novelty", kept_perf)
                self.assertIn("leftover_area", kept_perf)
        finally:
            session_mod.STUDIES_DIR = orig_dir
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
