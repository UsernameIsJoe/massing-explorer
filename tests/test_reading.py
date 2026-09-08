"""Model reading sits before commit; engine still owns numbers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from massing_explorer.brief import apply_parsed_brief, parse_brief
from massing_explorer.chat import _commit_brief_with_reading
from massing_explorer.load import load_program_file
from massing_explorer.reading import DesignReading, validate_reading
from massing_explorer.session import StudySession

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

DINING = "DINING & FOOD SERVICE"
HPE = "HEALTH & PHYSICAL EDUCATION"
CORE = "CORE ACADEMIC"


class _FakeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages: list[dict]) -> dict:
        text = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return {"message": {"content": text}}


class TestValidateReading(unittest.TestCase):
    def setUp(self) -> None:
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.names = [d.name for d in program.departments]

    def test_keeps_legal_choices_and_drops_dimensions(self) -> None:
        reading = validate_reading(
            {
                "keep_together": [["dining", "gym"]],
                "pair_on_frontage": ["core academic", "health"],
                "pin_ground": ["dining"],
                "preference": "low-rise",
                "width_ft": 80,
                "reasons": ["dining with the gym", "feel low"],
            },
            self.names,
        )
        self.assertEqual(reading.keep_together, [(DINING, HPE)])
        self.assertEqual(reading.pair_departments, [CORE, HPE])
        self.assertEqual(reading.pin_ground, [DINING])
        self.assertEqual(reading.preference, "low_rise")
        self.assertTrue(any("width_ft" in d for d in reading.dropped))

    def test_unknown_department_is_dropped(self) -> None:
        reading = validate_reading(
            {"pin_ground": ["planetarium"], "preference": "tower"},
            self.names,
        )
        self.assertEqual(reading.pin_ground, [])
        self.assertIsNone(reading.preference)
        self.assertTrue(reading.dropped)


class TestApplyReading(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="reading", program=program, config_path=str(CONFIG)
        )
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_reading_merges_wings_pins_and_pairs(self) -> None:
        text = (
            "put dining with the gym, sit them on the frontage with academic, "
            "keep it low, site length is 400, width is 120, max story is 4"
        )
        parsed = parse_brief(text, self.session.department_names())
        reading = DesignReading(
            keep_together=[(DINING, HPE)],
            pair_departments=[DINING, CORE],
            pin_ground=[DINING],
            preference="low_rise",
            reasons=["dining with the gym", "frontage", "keep it low"],
        )
        out = apply_parsed_brief(self.session, parsed, reading=reading)
        home = {
            d: m["id"]
            for m in out["grouping"]
            for d in m["departments"]
        }
        self.assertEqual(home[DINING], home[HPE])
        self.assertEqual(self.session.floor_pins.get(DINING), 0)
        self.assertTrue(self.session.pairings)
        paired = set(self.session.pairings[0].mass_ids)
        self.assertIn(home[DINING], paired)
        self.assertIn(home[CORE], paired)
        self.assertEqual(parsed.preference, "low_rise")
        self.assertTrue(any("paired" in n for n in out["reading_notes"]))


class TestNamedMasses(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="named_masses", program=program, config_path=str(CONFIG)
        )
        self.session.save()
        self.names = self.session.department_names()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_model_json_for_plain_mass_list(self) -> None:
        reading = validate_reading(
            {
                "masses": [
                    {"name": "Mass 1", "departments": ["gym", "core academic"]},
                    {"name": "Mass 2", "departments": ["dining", "media"]},
                ],
                "pair_length_ft": 500,
                "reasons": ["these two masses together under 500 ft"],
            },
            self.names,
        )
        self.assertEqual(len(reading.masses), 2)
        self.assertIn(HPE, reading.masses[0][1])
        self.assertIn(CORE, reading.masses[0][1])
        self.assertIn(DINING, reading.masses[1][1])
        self.assertEqual(reading.pair_length_ft, 500)
        self.assertIsNone(reading.site_length_ft)

    def test_named_masses_and_shared_length_are_applied(self) -> None:
        text = (
            "mass one is gym and core academic, mass two is dining and media. "
            "the length of these two mass together should be under 500ft."
        )
        parsed = parse_brief(text, self.names)
        reading = validate_reading(
            {
                "masses": [
                    {"name": "Mass 1", "departments": ["gym", "core academic"]},
                    {"name": "Mass 2", "departments": ["dining", "media"]},
                ],
                "pair_length_ft": 500,
                "reasons": [text],
            },
            self.names,
        )
        out = apply_parsed_brief(self.session, parsed, reading=reading)
        home = {
            d: m["id"]
            for m in out["grouping"]
            for d in m["departments"]
        }
        self.assertEqual(home[CORE], home[HPE])
        self.assertEqual(home[DINING], home["MEDIA CENTER"])
        self.assertNotEqual(home[CORE], home[DINING])
        self.assertTrue(self.session.pairings)
        self.assertAlmostEqual(self.session.pairings[0].total_length_ft, 500)
        self.assertTrue(self.session.pairings[0].length_is_cap)
        paired = set(self.session.pairings[0].mass_ids)
        self.assertEqual(paired, {home[CORE], home[DINING]})
        self.assertIsNone(self.session.constraints.get("max_total_length_ft"))
        self.assertTrue(any("500" in n for n in out["reading_notes"]))

    def test_chat_turn_understands_the_sentence(self) -> None:
        client = _FakeClient(
            [
                json.dumps(
                    {
                        "masses": [
                            {
                                "name": "Mass 1",
                                "departments": ["gym", "core academic"],
                            },
                            {
                                "name": "Mass 2",
                                "departments": ["dining", "media"],
                            },
                        ],
                        "pair_length_ft": 500,
                        "reasons": ["two named masses, together under 500"],
                    }
                ),
                json.dumps({"action": "none"}),
            ]
        )
        text = (
            "mass one is gym and core academic, mass two is dining and media. "
            "the length of these two mass together should be under 500ft."
        )
        parsed = parse_brief(text, self.names)
        out = _commit_brief_with_reading(
            self.session, client, text, parsed
        )
        home = {
            d: m["id"]
            for m in out["grouping"]
            for d in m["departments"]
        }
        self.assertEqual(home[CORE], home[HPE])
        self.assertEqual(home[DINING], home["MEDIA CENTER"])
        self.assertAlmostEqual(self.session.pairings[0].total_length_ft, 500)


class TestChatCommitUsesReading(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="reading_chat", program=program, config_path=str(CONFIG)
        )
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_model_choice_is_applied_before_solve(self) -> None:
        client = _FakeClient(
            [
                json.dumps(
                    {
                        "keep_together": [["dining", "health"]],
                        "pair_on_frontage": ["dining", "core academic"],
                        "pin_ground": ["dining"],
                        "preference": "low_rise",
                        "reasons": ["dining with the gym on the frontage"],
                    }
                ),
                json.dumps({"index": 0, "reason": "lowest academic bar"}),
                json.dumps({"action": "none"}),
            ]
        )
        text = (
            "dining with the gym, pair that wing with academic on the frontage, "
            "keep it low, site length is 520, width is 120, max story is 4"
        )
        parsed = parse_brief(text, self.session.department_names())
        out = _commit_brief_with_reading(self.session, client, text, parsed)
        self.assertGreaterEqual(client.calls, 1)
        home = {
            d: m["id"]
            for m in out["grouping"]
            for d in m["departments"]
        }
        self.assertEqual(home[DINING], home[HPE])
        self.assertEqual(self.session.floor_pins.get(DINING), 0)
        self.assertTrue(self.session.pairings)
        self.assertEqual(out["reading"]["preference"], "low_rise")
        found = (out.get("search") or {}).get("found") or 0
        if found:
            self.assertIsNotNone(out.get("solved"))
        else:
            self.assertTrue(out.get("search", {}).get("notes"))


if __name__ == "__main__":
    unittest.main()
