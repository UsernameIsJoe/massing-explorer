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

    def test_awkward_split_min_area_100_sqm(self) -> None:
        from types import SimpleNamespace

        from massing_explorer.explore.performance import (
            MIN_SPLIT_PART_SF,
            MIN_SPLIT_PART_SQM,
            _awkward_floor_splits,
            awkward_split_violations,
            eval_composites,
            measure,
            prefer_clean_splits,
        )

        self.assertEqual(MIN_SPLIT_PART_SQM, 100.0)
        self.assertAlmostEqual(MIN_SPLIT_PART_SF, 100.0 * 10.76391041671, places=3)

        def mass_with_floors(floor_allocs: list[list[tuple[str, float]]]) -> list:
            floors = []
            for i, allocs in enumerate(floor_allocs):
                floors.append(
                    SimpleNamespace(
                        level=i,
                        width_ft=100.0,
                        length_ft=100.0,
                        allocations=[
                            SimpleNamespace(department=d, gsf=g) for d, g in allocs
                        ],
                    )
                )
            return [SimpleNamespace(id="m1", name="Mass 1", floors=floors)]

        # Thin split slice under 100 m² (~1076 SF) — fail
        self.assertEqual(
            _awkward_floor_splits(
                mass_with_floors([[("CORE", 5000.0)], [("CORE", 500.0)]])
            ),
            1.0,
        )
        # Both slices large enough — pass (even if unbalanced by %)
        self.assertEqual(
            _awkward_floor_splits(
                mass_with_floors([[("CORE", 8000.0)], [("CORE", 1200.0)]])
            ),
            0.0,
        )
        # Shared floor with another program is allowed when slices are big enough
        self.assertEqual(
            _awkward_floor_splits(
                mass_with_floors(
                    [
                        [("CORE", 2000.0)],
                        [("CORE", 1500.0), ("MEDIA", 1500.0)],
                    ]
                )
            ),
            0.0,
        )
        # Gap: L0 + L2, skip L1 — still a deal breaker
        gap = mass_with_floors(
            [[("CORE", 2000.0)], [("MEDIA", 2000.0)], [("CORE", 1500.0)]]
        )
        self.assertEqual(_awkward_floor_splits(gap), 1.0)
        hits = awkward_split_violations(gap)
        self.assertTrue(any("non-contiguous" in r for h in hits for r in h["reasons"]))

        thin = mass_with_floors([[("CORE", 5000.0)], [("CORE", 400.0), ("MEDIA", 800.0)]])
        thin_hits = awkward_split_violations(thin)
        self.assertTrue(
            any("split slice under" in r for h in thin_hits for r in h["reasons"])
        )

        result = SimpleNamespace(masses=thin, validation=[])
        perf = measure(result)
        self.assertFalse(perf["fits_limitations"])
        self.assertIn("program_split", perf["failed_kinds"])

        pool = prefer_clean_splits(
            [
                {
                    "cell": "clean",
                    "performance": {"awkward_splits": 0.0, "fragmentation": 0.0},
                },
                {
                    "cell": "weird",
                    "performance": {"awkward_splits": 1.0, "fragmentation": 0.2},
                },
            ]
        )
        self.assertEqual([e["cell"] for e in pool], ["clean"])
        only_weird = prefer_clean_splits(
            [{"cell": "w1", "performance": {"awkward_splits": 0.8, "fragmentation": 0.5}}]
        )
        self.assertEqual(only_weird, [])

        clean = eval_composites(
            {
                "fragmentation": 0.0,
                "awkward_splits": 0.0,
                "public_on_grade": 1.0,
                "anchor_fit": 1.0,
                "preference_distance": 0.0,
                "leftover_area": 0.0,
                "footprint_likeness": 0.5,
            }
        )
        weird = eval_composites(
            {
                "fragmentation": 0.0,
                "awkward_splits": 1.0,
                "public_on_grade": 1.0,
                "anchor_fit": 1.0,
                "preference_distance": 0.0,
                "leftover_area": 0.0,
                "footprint_likeness": 0.5,
            }
        )
        self.assertGreater(clean["program_coherence"] - weird["program_coherence"], 0.4)

    def test_stated_weight_penalizes_awkward_splits(self) -> None:
        from massing_explorer.explore.preference import stated_weight

        clean = {
            "performance": {
                "failed_checks": 0,
                "preference_distance": 0.1,
                "awkward_splits": 0.0,
                "program_coherence": 0.9,
            }
        }
        weird = {
            "performance": {
                "failed_checks": 0,
                "preference_distance": 0.1,
                "awkward_splits": 1.0,
                "program_coherence": 0.4,
            }
        }
        self.assertGreater(stated_weight(clean), stated_weight(weird) * 4)

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
