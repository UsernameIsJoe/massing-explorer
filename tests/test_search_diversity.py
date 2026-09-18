"""P constraints, archive identity, COVER no-ops, and feasibility story weight."""

from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from massing_explorer.explore.cover import CoverSample, _canonicalize_samples, _sample_distance
from massing_explorer.explore.csp import describe_csp
from massing_explorer.explore.feasibility import feasibility_distance, violations_from_checks
from massing_explorer.explore.realize import _envelope_widths
from massing_explorer.explore.repair import _nudge_for_violations
from massing_explorer.explore.strategy import grouping_is_required, idea_key
from massing_explorer.massing_models import ValidationCheck


def _mass(mid: str, depts: list[str], stories: int = 2):
    return SimpleNamespace(id=mid, name=mid, departments=list(depts), story_count=stories)


def _flatten(item: dict) -> tuple[list[dict], list[int]]:
    """One atom per department plus its block index, for feature helpers."""
    atoms: list[dict] = []
    assign: list[int] = []
    for bi, group in enumerate(item.get("groups") or []):
        for dept in group.get("departments") or []:
            atoms.append({"departments": [dept]})
            assign.append(bi)
    return atoms, assign


class TestPartitionConstraints(unittest.TestCase):
    def test_mass_count_and_together_do_not_freeze_p(self) -> None:
        gym, dining, art, admin, media = "Gym", "Dining", "Art", "Admin", "Media"
        session = SimpleNamespace(
            constraints={
                "p_constraints": {
                    "together": [[gym, dining]],
                    "apart": [],
                    "alone": [],
                    "mass_count": 3,
                    "mass_count_min": 3,
                    "mass_count_max": 3,
                },
                "briefing": {
                    "requirements": [
                        {"lever": "mass_count", "value": 3},
                        {"lever": "same_mass", "departments": [gym, dining, art, admin, media]},
                    ]
                },
            },
            masses=[
                _mass("public", [gym, dining]),
                _mass("arts", [art]),
                _mass("ops", [admin]),
                _mass("media", [media], 1),
            ],
            department_names=lambda: [gym, dining, art, admin, media],
            brief_locked=True,
            floor_pins={},
            double_height_rooms=[],
        )
        self.assertFalse(grouping_is_required(session))
        report = describe_csp(session, cap=8)
        self.assertFalse(report["locked"])
        self.assertGreaterEqual(report["feasible_count"], 2)
        for item in report["chosen"]:
            groups = item["groups"]
            self.assertEqual(len(groups), 3)
            homes = {d: i for i, g in enumerate(groups) for d in g["departments"]}
            self.assertEqual(homes[gym], homes[dining])

    def test_mass_count_range_does_not_prefer_higher_count(self) -> None:
        gym, dining, art, admin, media = "Gym", "Dining", "Art", "Admin", "Media"
        session = SimpleNamespace(
            constraints={
                "p_constraints": {
                    "together": [[gym, dining]],
                    "apart": [],
                    "alone": [],
                    "mass_count": 4,
                    "mass_count_min": 2,
                    "mass_count_max": 5,
                    "preferred_mass_count": None,
                },
                "briefing": {"requirements": [], "limitations": [], "preferences": []},
            },
            masses=[
                _mass("public", [gym, dining]),
                _mass("arts", [art]),
                _mass("ops", [admin]),
                _mass("media", [media], 1),
            ],
            department_names=lambda: [gym, dining, art, admin, media],
            brief_locked=True,
            floor_pins={},
            double_height_rooms=[],
        )
        from massing_explorer.explore.csp import _score

        report = describe_csp(session, cap=8)
        sizes = {len(item["groups"]) for item in report["chosen"]}
        self.assertGreaterEqual(len(sizes), 2, sorted(sizes))
        self.assertIn(min(sizes), sizes)
        self.assertLess(min(sizes), max(sizes))
        # Default semantic rank is flat: mass count is not in the score.
        self.assertEqual(_score([{"departments": ["A"]}], [0])[:2], (1000, 0))  # stated
        atoms = [{"departments": [d]} for d in ("A", "B", "C", "D", "E")]
        three = [0, 0, 1, 1, 2]  # two pairwise merges → generic
        two = [0, 0, 0, 1, 1]  # two multi-atom blocks → generic
        self.assertEqual(_score(atoms, three)[:2], (200, 0))
        self.assertEqual(_score(atoms, two)[:2], (200, 0))
        # Mass count must not decide the order when shape is otherwise tied.
        self.assertEqual(_score(atoms, three)[:2], _score(atoms, two)[:2])

    def test_preferred_mass_count_may_favor_higher_or_lower(self) -> None:
        from massing_explorer.explore.csp import _score

        atoms = [{"departments": [d]} for d in ("A", "B", "C", "D", "E")]
        three = [0, 0, 1, 1, 2]
        two = [0, 0, 0, 1, 1]
        self.assertEqual(len(set(three)), 3)
        self.assertEqual(len(set(two)), 2)
        # Prefer 3 → three beats two.
        self.assertGreater(
            _score(atoms, three, preferred_mass_count=3),
            _score(atoms, two, preferred_mass_count=3),
        )
        # Prefer 2 → two beats three.
        self.assertGreater(
            _score(atoms, two, preferred_mass_count=2),
            _score(atoms, three, preferred_mass_count=2),
        )
        # Without a preference, |P| alone must not decide the order.
        self.assertEqual(_score(atoms, three)[:2], _score(atoms, two)[:2])

    def test_shortlist_covers_both_ends_of_a_wide_range(self) -> None:
        names = [f"D{i}" for i in range(6)]
        session = SimpleNamespace(
            constraints={
                "p_constraints": {
                    "together": [],
                    "apart": [],
                    "alone": [],
                    "mass_count_min": 2,
                    "mass_count_max": 5,
                    "preferred_mass_count": None,
                },
                "briefing": {"requirements": [], "limitations": [], "preferences": []},
            },
            masses=[_mass(f"m{i}", [names[i]]) for i in range(6)],
            department_names=lambda: list(names),
            brief_locked=True,
            floor_pins={},
            double_height_rooms=[],
        )
        report = describe_csp(session, cap=8)
        sizes = {len(item["groups"]) for item in report["chosen"]}
        self.assertIn(2, sizes, sorted(sizes))
        self.assertIn(5, sizes, sorted(sizes))

    def test_shortlist_shares_slots_fairly_across_mass_counts(self) -> None:
        """Larger |P| slices must not crowd out smaller ones in the shortlist."""
        from collections import Counter

        from massing_explorer.explore.csp import _stratum_quotas

        # Equal floor across 4 strata.
        q = _stratum_quotas([2, 3, 4, 5], 8, available={2: 99, 3: 99, 4: 99, 5: 99})
        self.assertEqual(q, {2: 2, 3: 2, 4: 2, 5: 2})
        # Preferred may claim remainder only after equal floors.
        q2 = _stratum_quotas(
            [2, 3, 4],
            8,
            preferred=4,
            available={2: 99, 3: 99, 4: 99},
        )
        self.assertEqual(q2[2], 2)
        self.assertEqual(q2[3], 2)
        self.assertEqual(q2[4], 4)

        names = [f"D{i}" for i in range(6)]
        session = SimpleNamespace(
            constraints={
                "p_constraints": {
                    "together": [],
                    "apart": [],
                    "alone": [],
                    "mass_count_min": 2,
                    "mass_count_max": 5,
                    "preferred_mass_count": None,
                },
                "briefing": {"requirements": [], "limitations": [], "preferences": []},
            },
            masses=[_mass(f"m{i}", [names[i]]) for i in range(6)],
            department_names=lambda: list(names),
            brief_locked=True,
            floor_pins={},
            double_height_rooms=[],
        )
        report = describe_csp(session, cap=8)
        counts = Counter(len(item["groups"]) for item in report["chosen"])
        # Near-equal: every present stratum within 1 of each other, none starved.
        self.assertGreaterEqual(len(counts), 3)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        for k in counts:
            self.assertGreaterEqual(counts[k], 1)

        # Two-level range with a large cap: neither side may monopolize.
        session.constraints["p_constraints"]["mass_count_min"] = 3
        session.constraints["p_constraints"]["mass_count_max"] = 4
        report2 = describe_csp(session, cap=20)
        counts2 = Counter(len(item["groups"]) for item in report2["chosen"])
        self.assertIn(3, counts2)
        self.assertIn(4, counts2)
        self.assertLessEqual(abs(counts2[3] - counts2[4]), 1)
        self.assertGreaterEqual(min(counts2[3], counts2[4]), 9)


class TestIdeaIdentity(unittest.TestCase):
    def _session(self, stories: int, pin: dict | None = None):
        return SimpleNamespace(
            constraints={"loading": "double", "cover_envelope": "balanced", "story_lock": {}},
            masses=[_mass("a", ["Art"], stories)],
            pairings=[],
            floor_pins=pin or {},
            floor_tapers=None,
            floor_steps=None,
            double_height_rooms=[],
        )

    def test_pin_and_exact_stories_change_the_cell(self) -> None:
        bare = idea_key(self._session(3))
        pinned = idea_key(self._session(3, {"Art": 0}))
        taller = idea_key(self._session(4))
        self.assertNotEqual(bare, pinned)
        self.assertNotEqual(bare, taller)


class TestCoverCanonical(unittest.TestCase):
    def test_step_on_one_story_collapses_to_uniform(self) -> None:
        samples = _canonicalize_samples(
            [
                CoverSample(stories=(1, 1), plate_profile="uniform", label="u"),
                CoverSample(stories=(1, 1), plate_profile="step", label="s"),
                CoverSample(stories=(2, 1), plate_profile="step", label="real"),
            ]
        )
        plates = [(s.stories, s.plate_profile) for s in samples]
        self.assertEqual(plates, [((1, 1), "uniform"), ((2, 1), "step")])

    def test_different_partitions_are_farther_than_loading(self) -> None:
        base = CoverSample(partition_index=0, stories=(2, 1), loading="double")
        other_p = CoverSample(partition_index=1, stories=(2, 1), loading="double")
        other_l = CoverSample(partition_index=0, stories=(2, 1), loading="single")
        self.assertGreater(_sample_distance(base, other_p), _sample_distance(base, other_l))


class TestEnvelopeWidths(unittest.TestCase):
    def test_compact_and_elongated_generate_different_widths(self) -> None:
        plate = 10000.0
        compact = _envelope_widths(SimpleNamespace(constraints={"cover_envelope": "compact"}), plate)
        long = _envelope_widths(SimpleNamespace(constraints={"cover_envelope": "elongated"}), plate)
        self.assertLess(min(long), min(compact))


class TestContiguousStacking(unittest.TestCase):
    def test_contiguous_multi_floor_is_not_fragmentation(self) -> None:
        from massing_explorer.explore.performance import _fragmentation

        floor = lambda level, dept: SimpleNamespace(
            level=level,
            allocations=[SimpleNamespace(department=dept)],
        )
        masses = [
            SimpleNamespace(
                floors=[floor(0, "CORE"), floor(1, "CORE"), floor(2, "CORE")]
            )
        ]
        self.assertEqual(_fragmentation(masses), 0.0)
        gap = [
            SimpleNamespace(
                floors=[floor(0, "CORE"), floor(2, "CORE")]
            )
        ]
        self.assertGreater(_fragmentation(gap), 0.0)


class TestStoryDistance(unittest.TestCase):
    def test_story_violation_adds_distance(self) -> None:
        story = violations_from_checks(
            [ValidationCheck(check="max_stories:a", passed=False, message="too tall")]
        )
        none = violations_from_checks([])
        self.assertEqual(story["stories"], 1.0)
        self.assertGreater(feasibility_distance(story), feasibility_distance(none))

    def test_split_nudge_prefers_envelope_not_stories(self) -> None:
        session = SimpleNamespace(
            constraints={"max_stories": 4, "cover_envelope": "balanced", "story_lock": {}},
            masses=[_mass("a", ["Art", "Admin"], 1)],
        )
        label = _nudge_for_violations(session, {"split": 1.0})
        self.assertEqual(label, "envelope")
        self.assertEqual(session.constraints["cover_envelope"], "compact")
        self.assertEqual(session.masses[0].story_count, 1)

    def test_edge_overrun_nudge_bumps_stories(self) -> None:
        session = SimpleNamespace(
            constraints={"max_stories": 4, "cover_envelope": "balanced", "story_lock": {}},
            masses=[_mass("a", ["Art", "Admin"], 1)],
        )
        label = _nudge_for_violations(session, {"edge_overrun": 0.2})
        self.assertEqual(label, "stories+1")
        self.assertEqual(session.masses[0].story_count, 2)

    def test_edge_overrun_clears_width_locks_first(self) -> None:
        session = SimpleNamespace(
            constraints={
                "max_stories": 4,
                "cover_envelope": "balanced",
                "story_lock": {},
                "a_width_ft": 80.0,
            },
            masses=[_mass("a", ["Art", "Admin"], 1)],
        )
        label = _nudge_for_violations(
            session, {"edge_overrun": 0.2, "owners": {"edge_overrun": ["a"]}}
        )
        self.assertEqual(label, "clear-widths")
        self.assertNotIn("a_width_ft", session.constraints)
        self.assertEqual(session.masses[0].story_count, 1)


class TestCoverPartitionBudget(unittest.TestCase):
    def _session(self, *, n_atoms: int = 5, k_min: int = 2, k_max: int = 4):
        names = [f"D{i}" for i in range(n_atoms)]
        return SimpleNamespace(
            constraints={
                "p_constraints": {
                    "together": [],
                    "apart": [],
                    "alone": [],
                    "mass_count_min": k_min,
                    "mass_count_max": k_max,
                    "preferred_mass_count": None,
                },
                "briefing": {"requirements": [], "limitations": [], "preferences": []},
            },
            masses=[_mass(f"m{i}", [names[i]]) for i in range(n_atoms)],
            department_names=lambda: list(names),
            brief_locked=True,
            floor_pins={},
            double_height_rooms=[],
            pairings=[],
            floor_steps={},
            floor_tapers={},
        )

    def test_ui_shortlist_stays_small_while_cover_budget_is_12_to_20(self) -> None:
        from massing_explorer.explore.csp import UI_PARTITION_CAP, describe_csp
        from massing_explorer.explore.partitions import (
            COVER_PARTITION_MAX,
            COVER_PARTITION_MIN,
            cover_partition_budget,
            enumerate_partitions,
        )

        session = self._session()
        ui = describe_csp(session)
        self.assertLessEqual(ui["shown"], UI_PARTITION_CAP)
        self.assertLessEqual(len(enumerate_partitions(session)), UI_PARTITION_CAP)
        self.assertGreaterEqual(ui["feasible_count"], COVER_PARTITION_MIN)
        budget = cover_partition_budget(session, feasible_count=ui["feasible_count"])
        self.assertGreaterEqual(budget, COVER_PARTITION_MIN)
        self.assertLessEqual(budget, COVER_PARTITION_MAX)
        cover = enumerate_partitions(session, cap=budget)
        self.assertEqual(len(cover), budget)
        sizes = {len(item["groups"]) for item in cover}
        self.assertGreaterEqual(len(sizes), 2)

    def test_mcts_candidates_prefer_persisted_cover_pool(self) -> None:
        from massing_explorer.explore.partitions import cover_partition_candidates

        session = self._session()
        session.constraints["cover_partition_pool"] = [
            {
                "reason": "stated",
                "groups": [
                    {"id": "m0", "name": "m0", "departments": ["D0"], "story_count": 2},
                    {"id": "m1", "name": "m1", "departments": ["D1", "D2"], "story_count": 2},
                    {"id": "m2", "name": "m2", "departments": ["D3", "D4"], "story_count": 2},
                ],
            },
            {
                "reason": "alt",
                "groups": [
                    {"id": "a", "name": "a", "departments": ["D0", "D1"], "story_count": 2},
                    {"id": "b", "name": "b", "departments": ["D2"], "story_count": 2},
                    {"id": "c", "name": "c", "departments": ["D3"], "story_count": 2},
                    {"id": "d", "name": "d", "departments": ["D4"], "story_count": 2},
                ],
            },
            {
                "reason": "alt2",
                "groups": [
                    {"id": "x", "name": "x", "departments": ["D0"], "story_count": 2},
                    {"id": "y", "name": "y", "departments": ["D1"], "story_count": 2},
                    {"id": "z", "name": "z", "departments": ["D2", "D3", "D4"], "story_count": 2},
                ],
            },
        ]
        # Stated masses are one-dept each — first pool entry is not stated, so all three return.
        found = cover_partition_candidates(session, limit=4)
        self.assertEqual(len(found), 3)
        self.assertEqual([f["reason"] for f in found], ["stated", "alt", "alt2"])

    def test_local_partition_moves_extend_beyond_shortlist(self) -> None:
        from massing_explorer.explore.partitions import local_partition_candidates

        session = self._session(n_atoms=4, k_min=2, k_max=3)
        found = local_partition_candidates(session, limit=6)
        self.assertGreaterEqual(len(found), 1)
        for item in found:
            n = len(item["groups"])
            self.assertGreaterEqual(n, 2)
            self.assertLessEqual(n, 3)
            depts = [d for g in item["groups"] for d in g["departments"]]
            self.assertEqual(len(depts), len(set(depts)))

    def test_school_bars_outrank_arts_in_classroom_wing(self) -> None:
        """COVER shortlist must prefer academic|public|athletics over arts-in-academic."""
        from massing_explorer.explore.csp import _score

        atoms = [
            {"departments": ["CORE ACADEMIC"]},
            {"departments": ["SPECIAL EDUCATION"]},
            {"departments": ["ART & MUSIC"]},
            {"departments": ["ADMINISTRATION & GUIDANCE"]},
            {"departments": ["DINING & FOOD SERVICE", "HEALTH & PHYSICAL EDUCATION"]},
            {"departments": ["MEDIA CENTER"]},
            {"departments": ["MEDICAL"]},
        ]
        school = [0, 0, 1, 1, 2, 0, 1]  # academic+media | arts+admin+medical | gym
        jammed = [0, 0, 0, 1, 2, 0, 1]  # arts inside classroom bar
        self.assertGreater(_score(atoms, school), _score(atoms, jammed))

    def test_block_motif_distinguishes_art_with_academic(self) -> None:
        from massing_explorer.explore.csp import _block_motif, _relationship_family

        atoms = [
            {"departments": ["CORE ACADEMIC"]},
            {"departments": ["SPECIAL EDUCATION"]},
            {"departments": ["ART & MUSIC"]},
            {"departments": ["ADMINISTRATION & GUIDANCE"]},
            {"departments": ["DINING & FOOD SERVICE", "HEALTH & PHYSICAL EDUCATION"]},
            {"departments": ["MEDIA CENTER"]},
            {"departments": ["MEDICAL"]},
            {"departments": ["CUSTODIAL & MAINTENANCE"]},
        ]
        school = [0, 0, 1, 1, 2, 0, 1, 1]  # art off academic, media on academic
        art_on_aca = [0, 0, 0, 0, 2, 1, 0, 1]  # art+admin on academic, media with custodial
        self.assertNotEqual(_block_motif(atoms, school), _block_motif(atoms, art_on_aca))
        self.assertEqual(_block_motif(atoms, art_on_aca)[0], "with_academic")
        self.assertEqual(_block_motif(atoms, school)[0], "other")
        self.assertEqual(_relationship_family(atoms, school), "school_bars")
        self.assertEqual(_relationship_family(atoms, art_on_aca), "arts_with_academic")

    def test_relationship_features_read_gym_and_academic_cohesion(self) -> None:
        """Isolation and cohesion come from families, not department strings."""
        from massing_explorer.explore.csp import _relationship_features

        atoms = [
            {"departments": ["CORE ACADEMIC"]},
            {"departments": ["SPECIAL EDUCATION"]},
            {"departments": ["ART & MUSIC"]},
            {"departments": ["ADMINISTRATION & GUIDANCE"]},
            {"departments": ["DINING & FOOD SERVICE", "HEALTH & PHYSICAL EDUCATION"]},
            {"departments": ["MEDIA CENTER"]},
            {"departments": ["MEDICAL"]},
            {"departments": ["CUSTODIAL & MAINTENANCE"]},
        ]
        isolated = _relationship_features(atoms, [0, 0, 1, 1, 2, 0, 1, 1])
        self.assertEqual(isolated["gym_dining"], "isolated")
        self.assertEqual(isolated["academic"], "together")
        shared = _relationship_features(atoms, [0, 0, 1, 1, 2, 0, 1, 2])
        self.assertEqual(shared["gym_dining"], "shared")
        split = _relationship_features(atoms, [0, 1, 1, 1, 2, 0, 1, 1])
        self.assertEqual(split["academic"], "split")

    def test_shortlist_covers_relationship_features_not_four_families(self) -> None:
        """Secondary seats are feature coverage, so no basin monopolizes them."""
        from massing_explorer.explore.csp import (
            _relationship_features,
            _relationship_family,
        )

        depts = [
            "CORE ACADEMIC",
            "SPECIAL EDUCATION",
            "ART & MUSIC",
            "ADMINISTRATION & GUIDANCE",
            "DINING & FOOD SERVICE",
            "HEALTH & PHYSICAL EDUCATION",
            "MEDIA CENTER",
            "MEDICAL",
            "CUSTODIAL & MAINTENANCE",
        ]
        masses = [_mass(f"m{i}", [d], 2) for i, d in enumerate(depts)]
        session = SimpleNamespace(
            masses=masses,
            constraints={
                "p_constraints": {
                    "together": [["DINING & FOOD SERVICE", "HEALTH & PHYSICAL EDUCATION"]],
                    "apart": [],
                    "alone": [],
                    "mass_count_min": 3,
                    "mass_count_max": 4,
                }
            },
            department_names=lambda: list(depts),
        )
        report = describe_csp(session, cap=20)
        chosen = report.get("chosen") or []
        self.assertGreaterEqual(len(chosen), 8)
        features = [_relationship_features(*_flatten(item)) for item in chosen]

        # Every placement feature must show more than one value. Structural
        # gym+dining must too; academic cohesion is often uniformly "together"
        # on school programs and only needs a seat when split variants exist.
        for name in ("art", "media", "admin", "gym_dining"):
            values = Counter(f[name] for f in features)
            self.assertGreaterEqual(
                len(values), 2, msg=f"{name} collapsed to one value: {values}"
            )
            self.assertLess(
                max(values.values()),
                len(chosen),
                msg=f"{name} monopolized by one value: {values}",
            )
        academic_values = {f["academic"] for f in features}
        self.assertTrue(
            academic_values,
            msg="academic cohesion feature missing",
        )

        # Each placement feature reaches the basins that matter; athletics
        # riders are optional on small shortlists after motif-first seating.
        for name in ("art", "media", "admin"):
            values = {f[name] for f in features}
            self.assertEqual(
                values,
                {"with_academic", "alone", "with_athletics", "other"},
                msg=f"{name} placements missing from shortlist: {sorted(values)}",
            )
        self.assertIn("isolated", {f["gym_dining"] for f in features})

        # Combinations, not just marginals. The coarse taxonomy has four
        # buckets; feature coverage must beat that by a wide margin.
        families = Counter(_relationship_family(*_flatten(item)) for item in chosen)
        motifs = {(f["art"], f["media"], f["admin"]) for f in features}
        self.assertGreater(
            len(motifs),
            2 * len(families),
            msg=f"{len(motifs)} motifs vs {len(families)} families: {sorted(motifs)}",
        )
        self.assertLess(
            max(families.values()),
            len(chosen),
            msg=f"one coarse family took every seat; families={families}",
        )

        # The two placements the four families collapsed must each be seated
        # alongside an isolated gym+dining bar — the motifs that went missing.
        for role in ("with_academic", "alone"):
            self.assertTrue(
                any(
                    f["art"] == role and f["gym_dining"] == "isolated" for f in features
                ),
                msg=f"no art={role} seat with an isolated gym+dining bar",
            )

        # Motif cells keep their own representative, so most seats carry a
        # distinct art×media×admin combination rather than repeating one basin.
        self.assertGreaterEqual(
            len(motifs),
            len(chosen) // 2,
            msg=f"{len(motifs)} motifs across {len(chosen)} seats: {sorted(motifs)}",
        )
        # Coverage reaches past marginals: several art placements are seated
        # with more than one media/admin arrangement behind them.
        multi = [
            role
            for role in {m[0] for m in motifs}
            if len([m for m in motifs if m[0] == role]) > 1
        ]
        self.assertGreaterEqual(
            len(multi),
            2,
            msg=f"only {multi} art placements carry several motifs: {sorted(motifs)}",
        )
        # Each placement value that the four families collapsed is seated in
        # more than one motif, so the selector stays generic: no scripted
        # partition or motif triple is required, only feature coverage.
        for role in ("with_academic", "alone"):
            self.assertIn(
                role,
                {m[0] for m in motifs},
                msg=f"no art={role} motif; have {sorted(motifs)}",
            )

    def test_pool_expansion_reuses_the_shortlist_ordering(self) -> None:
        """P-pool growth pulls the same stream, minus what it already holds."""
        from massing_explorer.explore.csp import (
            _relationship_features,
            ordered_partition_candidates,
        )
        from massing_explorer.explore.p_pool import (
            csp_leftover_partitions,
            partition_key,
        )

        depts = [
            "CORE ACADEMIC",
            "SPECIAL EDUCATION",
            "ART & MUSIC",
            "ADMINISTRATION & GUIDANCE",
            "DINING & FOOD SERVICE",
            "HEALTH & PHYSICAL EDUCATION",
            "MEDIA CENTER",
            "MEDICAL",
            "CUSTODIAL & MAINTENANCE",
        ]
        session = SimpleNamespace(
            masses=[_mass(f"m{i}", [d], 2) for i, d in enumerate(depts)],
            constraints={
                "p_constraints": {
                    "together": [["DINING & FOOD SERVICE", "HEALTH & PHYSICAL EDUCATION"]],
                    "apart": [],
                    "alone": [],
                    "mass_count_min": 3,
                    "mass_count_max": 4,
                }
            },
            department_names=lambda: list(depts),
        )
        shortlist = ordered_partition_candidates(session, cap=12)
        self.assertEqual(len(shortlist), 12)
        known = {partition_key(item["groups"]) for item in shortlist}

        leftovers = csp_leftover_partitions(session, known, limit=8)
        self.assertEqual(len(leftovers), 8)
        # Never re-admits what the pool holds.
        for item in leftovers:
            self.assertNotIn(partition_key(item["groups"]), known)
        # Still feature-ordered, not the raw rank tail.
        motifs = {
            _relationship_features(*_flatten(item))["art"] for item in leftovers
        }
        self.assertGreaterEqual(len(motifs), 2, msg=f"art placements={motifs}")


ROOT = Path(__file__).resolve().parents[1]
GSF_TWEAKED = ROOT / "examples" / "Underwood_Elementary_Space_Summary_GSF_Tweaked.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

# Same brief and GSF as _diag_realize_step1.BRIEF_34.
BRIEF_34 = (
    "3-4 masses, max 3 floors. length max 60 meters. gym and dining together and "
    "double height. art and music prefer on ground floor. media prefer on top "
    "floor above admin. admin have to be on ground floor. core academic and "
    "special ed width has to be 80 feet. mass ratio have to be between 2:5 and "
    "5:8. prefer 3 floors."
)


def _story_patterns(n: int) -> list[tuple[int, ...]]:
    """Uniform and one-tall-bar stackings — the usual school moves."""
    out = [tuple([2] * n), tuple([1] * n)]
    for i in range(n):
        out.append(tuple(3 if j == i else 1 for j in range(n)))
        out.append(tuple(3 if j == i else 2 for j in range(n)))
    return out


@unittest.skipUnless(GSF_TWEAKED.is_file(), "tweaked Underwood GSF not available")
class UnderwoodShortlistYieldTests(unittest.TestCase):
    """
    The 3–4 mass Underwood brief must admit several *legal* organizations.

    Diversity is worthless if none of it can be built, so this walks the
    shortlist COVER would search and realizes each entry over a bounded
    stacking sweep. It guards the selector, not the search allocation.
    """

    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_brief_34_shortlist_admits_four_legal_organizations(self) -> None:
        from massing_explorer.brief import apply_brief
        from massing_explorer.explore.partitions import (
            apply_partition,
            cover_partition_budget,
            enumerate_partitions,
        )
        from massing_explorer.explore.realize import realize
        from massing_explorer.load import load_program_file
        from massing_explorer.session import StudySession

        program = load_program_file(GSF_TWEAKED, config_path=CONFIG)
        session = StudySession(
            study_id="brief34_shortlist", program=program, config_path=str(CONFIG)
        )
        # Parse the brief only: the search itself is not under test here.
        session.constraints["cover_budget"] = {
            "start": 0,
            "step_small": 0,
            "step_large": 0,
            "max": 0,
        }
        session.constraints["explore_budget"] = {
            "mcts_sims": 0,
            "mcts_depth": 0,
            "mcts_roots": 0,
            "bo": 0,
            "refine": 0,
            "repair": 0,
        }
        session.save()
        apply_brief(session, BRIEF_34)

        shortlist = enumerate_partitions(session, cap=cover_partition_budget(session))
        self.assertGreaterEqual(len(shortlist), 12)

        legal: set[str] = set()
        for item in shortlist:
            for pattern in _story_patterns(len(item["groups"])):
                apply_partition(
                    session,
                    [
                        {
                            "id": f"m{i}",
                            "name": f"M{i}",
                            "departments": list(group["departments"]),
                            "story_count": int(stories),
                        }
                        for i, (group, stories) in enumerate(
                            zip(item["groups"], pattern)
                        )
                    ],
                )
                for mass in session.masses:
                    session.constraints.pop(f"{mass.id}_width_ft", None)
                (session.constraints.get("explore") or {}).pop("realize_cache", None)
                _result, perf = realize(session)
                if perf.get("fits_limitations"):
                    legal.add(
                        " | ".join(
                            sorted(
                                "+".join(sorted(str(d) for d in g["departments"]))
                                for g in item["groups"]
                            )
                        )
                    )
                    break

        self.assertGreaterEqual(
            len(legal),
            4,
            msg=f"only {len(legal)} legal organization(s) in the shortlist: {legal}",
        )


if __name__ == "__main__":
    unittest.main()
