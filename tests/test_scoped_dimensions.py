"""Scoped dimension clauses: department / bar / building width-depth-length."""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.brief import briefing_from_parsed, parse_brief
from massing_explorer.load import load_program_file


UNDERWOOD = Path(r"c:\Users\tu\Downloads\Underwood_Elementary_Space_Summary_TEST_Structured.xlsx")
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "underwood_elementary_space_summary.xlsx"


class ScopedDimensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = UNDERWOOD if UNDERWOOD.exists() else EXAMPLE
        cls.names = [d.name for d in load_program_file(str(path)).departments]

    def test_for_dept_width_has_to_be(self) -> None:
        parsed = parse_brief(
            "for core academic the width has to be 80ft",
            self.names,
        )
        self.assertEqual(parsed.constraints.get("department_widths", {}).get("CORE ACADEMIC"), 80.0)
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(
                c.get("lever") == "exact_width" and "CORE ACADEMIC" in (c.get("departments") or [])
                for c in briefing["requirements"]
            )
        )

    def test_dept_width_must_be(self) -> None:
        parsed = parse_brief("academic width must be 70 ft", self.names)
        self.assertIn("CORE ACADEMIC", parsed.constraints.get("department_widths") or {})

    def test_bars_no_wider_than(self) -> None:
        parsed = parse_brief("bars no wider than 60 ft", self.names)
        self.assertAlmostEqual(parsed.constraints.get("max_building_width_ft"), 60.0, places=1)

    def test_each_mass_should_not_be_longer_than_meters(self) -> None:
        parsed = parse_brief(
            "each mass should not be longer than 75 meters",
            self.names,
        )
        self.assertAlmostEqual(
            parsed.constraints.get("max_building_length_ft"),
            75 * 3.280839895,
            places=3,
        )
        self.assertEqual(parsed.constraints.get("length_limit_is_cap"), 1)
        self.assertAlmostEqual(
            parsed.constraints.get("max_edge_ft") or 0,
            75 * 3.280839895,
            places=3,
        )
        self.assertAlmostEqual(
            parsed.constraints.get("max_building_width_ft") or 0,
            75 * 3.280839895,
            places=3,
        )
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(
                c.get("lever") in {"max_length", "max_edge"}
                and abs(float(c.get("value") or 0) - 75 * 3.280839895) < 0.5
                for c in briefing["limitations"]
            )
        )

    def test_site_length_max(self) -> None:
        parsed = parse_brief("site length max 400 ft", self.names)
        self.assertAlmostEqual(parsed.constraints.get("max_total_length_ft"), 400.0, places=1)
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(c.get("lever") == "site_length" for c in briefing["limitations"])
        )

    def test_maintain_width_is_requirement_not_global_max(self) -> None:
        parsed = parse_brief(
            "Core Academic needs to maintain a width of 85 ft",
            self.names,
        )
        self.assertEqual(parsed.constraints.get("department_widths", {}).get("CORE ACADEMIC"), 85.0)
        self.assertIsNone(parsed.constraints.get("max_building_width_ft"))
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(
                c.get("lever") == "exact_width" and c.get("kind") == "requirement"
                for c in briefing["requirements"]
            )
        )
        self.assertFalse(
            any(
                c.get("lever") == "max_width" and abs(float(c.get("value") or 0) - 85) < 0.01
                for c in briefing["limitations"]
            )
        )

    def test_role_from_words_ignores_numbers(self) -> None:
        from massing_explorer.brief import _role_from_words

        self.assertEqual(_role_from_words("must be 85 ft wide"), "requirement")
        self.assertEqual(_role_from_words("max site length 400 ft"), "limitation")
        self.assertEqual(_role_from_words("prefer about four masses"), "preference")
        self.assertEqual(
            _role_from_words("Core Academic needs to maintain a width of thirty-five ft"),
            "requirement",
        )
        self.assertEqual(_role_from_words("should stay under 400 ft"), "limitation")
        self.assertEqual(_role_from_words("closer to 85 ft if possible"), "preference")
        self.assertEqual(_role_from_words("at least 40 ft wide"), "limitation")
        self.assertEqual(_role_from_words("requires exactly 4 masses"), "requirement")

    def test_wing_depth_exact(self) -> None:
        parsed = parse_brief("wing depth 45'", self.names)
        self.assertTrue(
            any(
                c.get("lever") == "width" and c.get("mode") == "exact" and c.get("value") == 45.0
                for c in parsed.dimensions
            )
        )

    def test_full_user_brief_includes_academic_width(self) -> None:
        brief = (
            "3-4 masses, gym and dining together and double heights. "
            "art and music better on ground floor. all masses max story is 3, "
            "and mass ratio prefer to be 3:5. for core academic the width has to be 80ft."
        )
        parsed = parse_brief(brief, self.names)
        self.assertEqual(parsed.constraints["department_widths"]["CORE ACADEMIC"], 80.0)
        levers = {c["lever"] for c in briefing_from_parsed(parsed)["requirements"]}
        self.assertIn("exact_width", levers)

    def test_length_max_meters_and_special_ed_width(self) -> None:
        brief = (
            "3 masses, max 3 floors. length max 40 meters. "
            "gym and dining together and double height. "
            "media prefer on ground floor. admin have to be on ground floor. "
            "core academic and special ed width has to be 80 feet. "
            "mass ratio prefer to be 4:7"
        )
        parsed = parse_brief(brief, self.names)
        self.assertAlmostEqual(
            parsed.constraints.get("max_edge_ft") or 0,
            40 * 3.280839895,
            places=3,
        )
        self.assertAlmostEqual(
            parsed.constraints.get("max_building_width_ft") or 0,
            40 * 3.280839895,
            places=3,
        )
        widths = parsed.constraints.get("department_widths") or {}
        self.assertEqual(widths.get("CORE ACADEMIC"), 80.0)
        self.assertEqual(widths.get("SPECIAL EDUCATION"), 80.0)
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(c.get("lever") == "max_edge" for c in briefing["limitations"])
        )
        from massing_explorer.modality import find_brief_questions, stated_numbers

        self.assertFalse(stated_numbers("gym and dining together and double height"))
        qs, _ = find_brief_questions(brief, use_llm=False, department_names=self.names)
        self.assertEqual(qs, [])

    def test_targeted_length_max_is_all_edge_for_that_mass_only(self) -> None:
        parsed = parse_brief(
            "core academic should not be longer than 50 meters",
            self.names,
        )
        edges = parsed.constraints.get("department_max_edge_ft") or {}
        self.assertAlmostEqual(
            float(edges.get("CORE ACADEMIC") or 0),
            50 * 3.280839895,
            places=3,
        )
        self.assertIsNone(parsed.constraints.get("max_edge_ft"))
        self.assertIsNone(parsed.constraints.get("max_building_length_ft"))
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(
                c.get("lever") == "max_edge"
                and "CORE ACADEMIC" in (c.get("departments") or [])
                for c in briefing["limitations"]
            )
        )

    def test_academic_length_max_phrase(self) -> None:
        parsed = parse_brief("academic length max 80 ft", self.names)
        edges = parsed.constraints.get("department_max_edge_ft") or {}
        self.assertAlmostEqual(float(edges.get("CORE ACADEMIC") or 0), 80.0, places=1)
        self.assertIsNone(parsed.constraints.get("max_edge_ft"))

    def test_global_length_max_is_not_hardcoded(self) -> None:
        parsed = parse_brief("length max 55 meters", self.names)
        self.assertAlmostEqual(
            parsed.constraints.get("max_edge_ft") or 0,
            55 * 3.280839895,
            places=3,
        )


if __name__ == "__main__":
    unittest.main()
