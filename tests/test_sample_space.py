"""The sample space is the open levers, not a batch size and not a length cap."""

from __future__ import annotations

import unittest

from massing_explorer.sample_space import (
    allocate_local_tries,
    cell_key,
    coverage_status,
    design_kind,
    designed_points,
    effective_sample_size,
    local_move_priority,
    margins_missing,
    next_design_point,
)


FACTORS = [
    {"id": "stories:a", "kind": "stories", "levels": [1, 2, 3]},
    {"id": "shape:a", "kind": "shape", "levels": ["thin", "bar", "box"]},
    {"id": "loading", "kind": "loading", "levels": ["double", "single"]},
]
BASE = {"stories:a": 2, "shape:a": "thin", "loading": "double"}


class TestDesignedSpace(unittest.TestCase):
    def test_margins_come_before_a_repeat_cell(self) -> None:
        design = designed_points(FACTORS, BASE)
        attempted = [BASE]
        point = next_design_point(FACTORS, design, attempted, set(), set())
        self.assertIsNotNone(point)
        self.assertTrue(
            point["stories:a"] != 2 or point["shape:a"] != "thin" or point["loading"] != "double"
        )
        self.assertTrue(margins_missing(FACTORS, attempted))

    def test_stop_only_when_no_remaining_point_can_open_a_cell(self) -> None:
        design = designed_points(FACTORS, BASE)
        attempted = list(design)
        legal = {cell_key(point, FACTORS) for point in design}
        status = coverage_status(FACTORS, design, attempted, legal, set(), at_cap=False)
        self.assertTrue(status["map_complete"])
        self.assertEqual(status["stopped"], "design_exhausted")
        self.assertIsNone(next_design_point(FACTORS, design, attempted, legal, set()))

    def test_a_repeated_batch_does_not_finish_untried_levels(self) -> None:
        design = designed_points(FACTORS, BASE)
        attempted = [BASE, dict(BASE)]
        status = coverage_status(
            FACTORS, design, attempted, {cell_key(BASE, FACTORS)}, set(), at_cap=False
        )
        self.assertFalse(status["map_complete"])
        self.assertTrue(status["margins_missing"])

    def test_cap_before_the_design_is_an_incomplete_map(self) -> None:
        design = designed_points(FACTORS, BASE)
        status = coverage_status(FACTORS, design, [BASE], set(), set(), at_cap=True)
        self.assertFalse(status["map_complete"])
        self.assertEqual(status["stopped"], "safety_cap")
        self.assertIn("untried", status["note"])

    def test_a_shared_cell_does_not_skip_an_untried_margin(self) -> None:
        factors = [{"id": "shape:a", "kind": "shape", "levels": ["box", "cube", "bar"]}]
        baseline = {"shape:a": "box"}
        design = designed_points(factors, baseline)
        legal = {cell_key(baseline, factors)}
        point = next_design_point(factors, design, [baseline], legal, set())
        self.assertEqual(point["shape:a"], "cube")
        self.assertEqual(cell_key(point, factors), cell_key(baseline, factors))
        bar = next_design_point(factors, design, [baseline, point], legal, set())
        self.assertEqual(bar["shape:a"], "bar")
        self.assertNotEqual(cell_key(bar, factors), cell_key(baseline, factors))
        done = [baseline, point, bar]
        self.assertIsNone(next_design_point(factors, design, done, legal | {cell_key(bar, factors)}, set()))

    def test_infeasible_cell_is_not_coverage(self) -> None:
        design = designed_points(FACTORS, BASE)
        failed = {cell_key(BASE, FACTORS)}
        point = next_design_point(FACTORS, design, [BASE], set(), failed)
        self.assertIsNotNone(point)
        self.assertNotEqual(cell_key(point, FACTORS), cell_key(BASE, FACTORS))


    def test_a_large_story_product_is_not_called_a_joint_sample(self) -> None:
        factors = [
            {"id": "stories:a", "kind": "stories", "levels": [1, 2, 3, 4]},
            {"id": "stories:b", "kind": "stories", "levels": [1, 2, 3, 4]},
        ]
        self.assertEqual(design_kind(factors), "margins_only")
        baseline = {"stories:a": 2, "stories:b": 2}
        design = designed_points(factors, baseline)
        legal = {cell_key(point, factors) for point in design}
        status = coverage_status(factors, design, design, legal, set(), at_cap=False)
        self.assertIn("not a full joint sample", status["note"])


class TestParticleAllocation(unittest.TestCase):
    def test_light_elite_keeps_a_floor(self) -> None:
        particles = [
            {"id": "heavy", "fits": True, "weight": 8.0},
            {"id": "mid", "fits": True, "weight": 2.0},
            {"id": "light", "fits": True, "weight": 0.2},
            {"id": "illegal", "fits": False, "weight": 9.0},
        ]
        counts = allocate_local_tries(particles, budget=6, floor=1)
        self.assertNotIn("illegal", counts)
        self.assertGreaterEqual(counts["light"], 1)
        self.assertGreaterEqual(counts["heavy"], counts["light"])
        self.assertEqual(sum(counts.values()), 6)

    def test_a_negative_height_weight_draws_stories_together(self) -> None:
        seed = {"a": 1, "b": 3}
        apart = {"stories": {"a": 1, "b": 4}, "shapes": {"a": "bar", "b": "bar"}}
        together = {"stories": {"a": 2, "b": 3}, "shapes": {"a": "bar", "b": "bar"}}
        weights = {"height_variance": -1.0}
        self.assertGreater(
            local_move_priority(seed, {"a": "bar", "b": "bar"}, together, weights),
            local_move_priority(seed, {"a": "bar", "b": "bar"}, apart, weights),
        )

    def test_ess_collapses_when_one_particle_holds_the_mass(self) -> None:
        spread = effective_sample_size([1.0, 1.0, 1.0])
        collapsed = effective_sample_size([9.0, 0.05, 0.05])
        self.assertGreater(spread, collapsed)
        self.assertAlmostEqual(spread, 3.0)
