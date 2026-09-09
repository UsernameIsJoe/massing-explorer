"""Measured traits and Bradley–Terry weights. A brief is not a choice."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from massing_explorer.preference import (
    chance_a_beats_b,
    describe_weights,
    fit_weights,
    information,
    next_pair,
    parse_choice,
    take_choice,
)
from massing_explorer.try_loop import effective_sample_size, select_elites
from massing_explorer.traits import measure_traits


def _mass(width: float, length: float, stories: int) -> SimpleNamespace:
    floors = [
        SimpleNamespace(width_ft=width, length_ft=length)
        for _ in range(stories)
    ]
    return SimpleNamespace(floors=floors)


class TestTraits(unittest.TestCase):
    def test_spread_and_height_come_from_the_drawing(self) -> None:
        even = SimpleNamespace(
            masses=[
                _mass(40, 80, 2),
                _mass(40, 80, 2),
            ]
        )
        piled = SimpleNamespace(
            masses=[
                _mass(40, 200, 4),
                _mass(20, 20, 1),
            ]
        )
        even_t = measure_traits(even)
        piled_t = measure_traits(piled)
        self.assertGreater(even_t["spread"], piled_t["spread"])
        self.assertGreater(piled_t["height_variance"], even_t["height_variance"])
        self.assertNotIn("street_edge", even_t)

    def test_street_edge_only_when_a_frontage_exists(self) -> None:
        result = SimpleNamespace(masses=[_mass(40, 80, 2), _mass(40, 40, 2)])
        session = SimpleNamespace(constraints={"max_total_length_ft": 200}, pairings=[])
        traits = measure_traits(result, session)
        self.assertAlmostEqual(traits["street_edge"], 120 / 200)


class TestBradleyTerry(unittest.TestCase):
    def test_no_choices_leaves_weights_empty(self) -> None:
        weights = fit_weights([], {})
        self.assertTrue(all(value == 0.0 for value in weights.values()))
        self.assertIn("No compared taste", describe_weights(weights))

    def test_choices_raise_the_favored_trait(self) -> None:
        schemes = {
            "spread": {"fits": True, "traits": {"spread": 0.8, "height_variance": 0.1}},
            "tall": {"fits": True, "traits": {"spread": 0.2, "height_variance": 0.9}},
        }
        comparisons = [
            {"a": "spread", "b": "tall", "winner": "a"},
            {"a": "spread", "b": "tall", "winner": "a"},
        ]
        weights = fit_weights(comparisons, schemes)
        self.assertGreater(weights["spread"], weights["height_variance"])
        self.assertGreater(
            chance_a_beats_b(schemes["spread"]["traits"], schemes["tall"]["traits"], weights),
            0.5,
        )

    def test_illegal_scheme_is_not_a_sample(self) -> None:
        schemes = {
            "ok": {"fits": True, "traits": {"spread": 0.6}},
            "bad": {"fits": False, "traits": {"spread": 0.1}},
        }
        weights = fit_weights(
            [{"a": "ok", "b": "bad", "winner": "a"}],
            schemes,
        )
        self.assertEqual(weights["spread"], 0.0)

    def test_ambiguous_pair_carries_more_information(self) -> None:
        self.assertGreater(information(0.5), information(0.9))

    def test_next_pair_connects_an_unseen_typology(self) -> None:
        schemes = [
            {"id": "a", "fits": True, "typology": "bar", "traits": {"spread": 0.4}},
            {"id": "b", "fits": True, "typology": "square", "traits": {"spread": 0.5}},
            {"id": "c", "fits": True, "typology": "low", "traits": {"spread": 0.6}},
        ]
        pair = next_pair(schemes, {}, [{"a": "a", "b": "b", "winner": "a"}])
        self.assertIsNotNone(pair)
        self.assertEqual(pair["kind"], "connecting")
        self.assertIn("c", {pair["a"], pair["b"]})

    def test_a_brief_is_not_a_choice(self) -> None:
        self.assertIsNone(parse_choice("I want 3 masses. Length 50 meter."))
        self.assertEqual(parse_choice("prefer the first"), "a")
        self.assertEqual(parse_choice("B"), "b")

    def test_choice_updates_the_weight_sentence(self) -> None:
        session = SimpleNamespace(
            constraints={
                "preference_learning": {
                    "weights": {},
                    "comparisons": [],
                    "pending_pair": {"a": "spread", "b": "tall"},
                },
                "try_loop": {
                    "schemes": [
                        {"id": "spread", "fits": True, "traits": {"spread": 0.8, "height_variance": 0.1}, "lengths": [40], "reason": "spread"},
                        {"id": "tall", "fits": True, "traits": {"spread": 0.2, "height_variance": 0.9}, "lengths": [80], "reason": "tall"},
                    ]
                },
            },
            save=lambda: None,
        )
        out = take_choice(session, "prefer the first")
        self.assertIsNotNone(out)
        self.assertIn("more spread", out["reply"])
        self.assertGreater(session.constraints["preference_learning"]["weights"]["spread"], 0)
        self.assertIn("reweighted", out["reply"])
        ranked = session.constraints["try_loop"]["particle_weights"]
        self.assertEqual(ranked[0]["id"], "spread")

    def test_elites_keep_a_lighter_legal_basin(self) -> None:
        records = [
            {"basin": "a", "fits_limitations": True, "mass_preference": 0.2, "traits": {}, "stage": 1, "score": (0, 0.2)},
            {"basin": "b", "fits_limitations": True, "mass_preference": 0.4, "traits": {}, "stage": 1, "score": (0, 0.4)},
            {"basin": "c", "fits_limitations": True, "mass_preference": 1.5, "traits": {}, "stage": 1, "score": (0, 1.5)},
            {"basin": "bad", "fits_limitations": False, "mass_preference": 0.0, "traits": {}, "stage": 1, "score": (2, 0)},
        ]
        elites = select_elites(records, {})
        keys = {item["key"] for item in elites}
        self.assertIn("a", keys)
        self.assertIn("c", keys)
        self.assertNotIn("bad", keys)
        self.assertGreater(effective_sample_size([3.0, 1.0, 0.2]), 1.0)
