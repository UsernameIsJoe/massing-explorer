"""Per-mass preferences stay preferences, and stage two ranks them."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from massing_explorer.brief import apply_brief, parse_brief
from massing_explorer.mass_prefs import (
    bind_mass_preferences,
    parse_mass_preferences,
    preference_distance,
)


from pathlib import Path

from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"


class TestParseMassPreferences(unittest.TestCase):
    def test_each_mass_keeps_its_own_shape(self) -> None:
        text = (
            "I want 3 masses. Length 50 meter. "
            "Mass A should be a thin, 3 floor building. "
            "Mass B should be a ratio 1:1, 2 floors box. "
            "Mass three should be a 2 stories small cube."
        )
        prefs = parse_mass_preferences(text)
        self.assertEqual(len(prefs), 3)
        self.assertEqual(prefs[0]["shape"], "thin")
        self.assertEqual(prefs[0]["stories"], 3)
        self.assertEqual(prefs[1]["shape"], "box")
        self.assertEqual(prefs[1]["stories"], 2)
        self.assertAlmostEqual(prefs[1]["ratio"], 1.0)
        self.assertEqual(prefs[2]["shape"], "cube")
        self.assertEqual(prefs[2]["stories"], 2)
        self.assertEqual(prefs[2]["size"], "small")

        parsed = parse_brief(text, [])
        self.assertEqual(parsed.mass_count, 3)
        self.assertEqual(len(parsed.mass_preferences), 3)
        self.assertIn("max_building_length_ft", parsed.constraints)
        self.assertGreater(parsed.constraints["max_building_length_ft"], 160)
        self.assertNotIn("exact_building_length_ft", parsed.constraints)

    def test_bind_follows_mass_order(self) -> None:
        prefs = parse_mass_preferences(
            "Mass A should be thin 3 floors. Mass B should be a box."
        )
        masses = [
            SimpleNamespace(id="m1", name="One"),
            SimpleNamespace(id="m2", name="Two"),
        ]
        bound = bind_mass_preferences(prefs, masses)
        self.assertEqual([p["mass_id"] for p in bound], ["m1", "m2"])

    def test_distance_prefers_the_asked_shape(self) -> None:
        thin = SimpleNamespace(
            floors=[SimpleNamespace(length_ft=90, width_ft=30, area_sf=2700)]
        )
        square = SimpleNamespace(
            floors=[SimpleNamespace(length_ft=50, width_ft=50, area_sf=2500)]
        )
        pref = {"stories": 3, "shape": "thin", "ratio": None, "size": None}
        self.assertLess(preference_distance(pref, thin), preference_distance(pref, square))

    def test_brief_binds_each_mass_preference(self) -> None:
        import tempfile

        import massing_explorer.session as session_mod

        orig = session_mod.STUDIES_DIR
        tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(tmp.name)
        try:
            program = load_program_file(UNDERWOOD, config_path=CONFIG)
            session = StudySession(
                study_id="mass_prefs", program=program, config_path=str(CONFIG)
            )
            text = (
                "I want 3 masses. Length 50 meter. "
                "Mass A should be a thin, 3 floor building. "
                "Mass B should be a ratio 1:1, 2 floors box. "
                "Mass three should be a 2 stories small cube."
            )
            apply_brief(session, text, search=False)
            bound = session.constraints["mass_preferences"]
            self.assertEqual(len(bound), 3)
            self.assertEqual(len(session.masses), 3)
            self.assertEqual(
                [p["shape"] for p in bound],
                ["thin", "box", "cube"],
            )
            self.assertEqual([p["mass_id"] for p in bound], [m.id for m in session.masses])
        finally:
            session_mod.STUDIES_DIR = orig
            tmp.cleanup()
