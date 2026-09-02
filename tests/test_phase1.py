"""Phase 1 tests for program ingest and GSF engine."""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.config import grossing_from_config, load_project_config
from massing_explorer.load import load_program_file

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CSV_EXAMPLE = ROOT / "examples" / "program.example.csv"
CONFIG = ROOT / "config" / "project.example.yaml"


class TestPhase1(unittest.TestCase):
    def test_underwood_parse_room_count(self) -> None:
        study = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.assertEqual(len(study.rooms), 41)
        self.assertEqual(len(study.departments), 9)

    def test_underwood_nfa_total(self) -> None:
        study = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.assertAlmostEqual(study.totals["nfa_sf"], 40462, delta=1)
        self.assertAlmostEqual(study.totals["declared_nfa_sf"], 40462, delta=1)

    def test_underwood_department_totals(self) -> None:
        study = load_program_file(UNDERWOOD, config_path=CONFIG)
        for dept in study.departments:
            if dept.declared_total_sf is not None:
                self.assertAlmostEqual(
                    dept.nfa_sf, dept.declared_total_sf, delta=1,
                    msg=f"Department {dept.name}",
                )

    def test_underwood_grossing_from_file(self) -> None:
        study = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.assertAlmostEqual(study.grossing.grossing_factor, 1.5, places=2)
        # File GFA = NFA * grossing
        self.assertAlmostEqual(study.totals["declared_gfa_sf"], 60693, delta=1)

    def test_gsf_calc_with_config(self) -> None:
        study = load_program_file(UNDERWOOD, config_path=CONFIG)
        expected = 40462 * 1.15 * 1.50
        self.assertAlmostEqual(study.totals["target_gsf"], expected, delta=1)

    def test_csv_example(self) -> None:
        study = load_program_file(CSV_EXAMPLE, config_path=CONFIG)
        self.assertGreater(len(study.rooms), 10)
        self.assertGreater(study.totals["nfa_sf"], 0)

    def test_config_anchor_rooms(self) -> None:
        config = load_project_config(CONFIG)
        grossing = grossing_from_config(config)
        self.assertAlmostEqual(grossing.area_adjustment, 1.15, places=2)
        self.assertAlmostEqual(grossing.grossing_factor, 1.50, places=2)
        gym = config["anchor_rooms"]["gym"]
        self.assertEqual(gym["min_width_ft"], 60)
        self.assertEqual(gym["min_length_ft"], 100)


if __name__ == "__main__":
    unittest.main()
