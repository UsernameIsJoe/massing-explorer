"""Nine probe axes + four soft eval composites."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from massing_explorer.explore.axes import PROBE_AXIS_NAMES, encode_named, encode_strategy
from massing_explorer.explore.performance import EVAL_AXIS_NAMES, eval_composites, measure
from massing_explorer.explore.preference import TRAIT_NAMES


class TestProbeAxes(unittest.TestCase):
    def test_encode_has_nine_named_axes(self) -> None:
        strategy = {
            "P": {
                "mass_count": 3,
                "locked": True,
                "partition": {
                    "a": ["CORE ACADEMIC", "ART & MUSIC"],
                    "b": ["HEALTH & PHYSICAL EDUCATION", "DINING & FOOD SERVICE"],
                    "c": ["ADMINISTRATION"],
                },
            },
            "T": {"kind": "independent_bars", "l_leftover": False},
            "V": {"pins": {"ADMINISTRATION": 0}, "double_height": ["DINING"]},
            "G": {
                "stories": {"a": 2, "b": 2, "c": 1},
                "loading": "double",
                "envelope": "balanced",
            },
        }
        vec = encode_strategy(strategy)
        named = encode_named(strategy)
        self.assertEqual(len(PROBE_AXIS_NAMES), 9)
        self.assertEqual(len(vec), 9)
        self.assertEqual(list(named.keys()), list(PROBE_AXIS_NAMES))
        self.assertAlmostEqual(named["mass_count"], 3 / 8.0)
        self.assertEqual(named["loading"], 1.0)
        self.assertEqual(named["topology"], 0.0)
        self.assertGreater(named["distribution_balance"], 0.0)
        # Same partition → same organization fingerprint.
        again = encode_strategy(strategy)
        self.assertEqual(again[0], vec[0])

    def test_paired_topology_and_envelope_shift(self) -> None:
        base = {
            "P": {"mass_count": 2, "partition": {"a": ["A"], "b": ["B"]}},
            "T": {"kind": "paired_bars"},
            "V": {},
            "G": {"stories": {"a": 3, "b": 1}, "loading": "single", "envelope": "elongated"},
        }
        named = encode_named(base)
        self.assertEqual(named["topology"], 1.0)
        self.assertEqual(named["loading"], 0.0)
        self.assertEqual(named["geometric_character"], 1.0)
        self.assertGreater(named["height_articulation"], 0.0)


class TestEvalAxes(unittest.TestCase):
    def test_learn_traits_are_the_four_composites(self) -> None:
        self.assertEqual(TRAIT_NAMES, EVAL_AXIS_NAMES)
        self.assertEqual(len(TRAIT_NAMES), 4)

    def test_composites_from_diagnostics(self) -> None:
        vector = {
            "fits_limitations": True,
            "fragmentation": 0.0,
            "awkward_splits": 0.0,
            "public_on_grade": 1.0,
            "anchor_fit": 1.0,
            "preference_distance": 0.2,
            "leftover_area": 0.1,
            "footprint_likeness": 0.9,
            "street_edge": 0.8,
        }
        out = eval_composites(vector)
        for name in EVAL_AXIS_NAMES:
            self.assertIn(name, out)
            self.assertGreaterEqual(out[name], 0.0)
            self.assertLessEqual(out[name], 1.0)
        self.assertAlmostEqual(out["preference_alignment"], 0.8)
        self.assertGreater(out["program_coherence"], 0.7)
        self.assertGreater(out["performance_efficiency"], 0.7)

    def test_probe_score_overrides_robustness_proxy(self) -> None:
        vector = {
            "fits_limitations": True,
            "fragmentation": 0.0,
            "awkward_splits": 0.0,
            "anchor_fit": 1.0,
            "preference_distance": 0.0,
            "leftover_area": 0.5,
            "footprint_likeness": 0.5,
            "_robustness_probe": 0.25,
        }
        out = eval_composites(vector)
        self.assertAlmostEqual(out["robustness"], 0.25)


if __name__ == "__main__":
    unittest.main()
