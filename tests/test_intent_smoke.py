"""
Smoke test: intent interpreting only (parse → roles → numbers → ask gate).

Does not run COVER, search, solver, or UI. Exercises the path Studio uses
before generate: find_brief_questions + parse_brief + briefing_from_parsed.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.brief import briefing_from_parsed, parse_brief
from massing_explorer.load import load_program_file
from massing_explorer.modality import (
    accounted_magnitudes,
    clause_was_captured,
    find_brief_questions,
    stated_numbers,
    unaccounted_numbers,
)

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

CORE = "CORE ACADEMIC"
HPE = "HEALTH & PHYSICAL EDUCATION"
DINING = "DINING & FOOD SERVICE"
MEDIA = "MEDIA CENTER"
ADMIN = "ADMINISTRATION & GUIDANCE"
SPECIAL = "SPECIAL EDUCATION"
ART = "ART & MUSIC"


def _names() -> list[str]:
    program = load_program_file(UNDERWOOD, config_path=CONFIG)
    return sorted({d.name for d in program.departments})


def _levers(briefing: dict, bucket: str) -> set[str]:
    return {str(c.get("lever") or "") for c in briefing.get(bucket) or []}


def _dept_pairs(briefing: dict, bucket: str, lever: str) -> set[frozenset[str]]:
    out: set[frozenset[str]] = set()
    for c in briefing.get(bucket) or []:
        if c.get("lever") != lever:
            continue
        depts = c.get("departments") or []
        if len(depts) >= 2:
            out.add(frozenset(depts[:2]))
        elif len(depts) == 1:
            out.add(frozenset(depts))
    return out


class IntentSmokeUnderwood(unittest.TestCase):
    """Canonical Studio brief: must interpret cleanly and not pause generate."""

    BRIEF = (
        "I want 3 masses, max 3 floors. length max 70 meters and min 20 meters. "
        "Gym and dining together, double height. Media prefer ground, admin must ground. "
        "Core academic and special ed width has to be 80 ft. "
        "Prefer mass ratio 4:7. Prefer 2-3 floors."
    )

    @classmethod
    def setUpClass(cls) -> None:
        cls.names = _names()

    def test_does_not_block_generate(self) -> None:
        qs, parsed = find_brief_questions(
            self.BRIEF, use_llm=False, department_names=self.names
        )
        self.assertEqual(qs, [], msg=qs)
        self.assertTrue(clause_was_captured("length max 70 meters and min 20 meters", parsed))
        self.assertTrue(clause_was_captured("Prefer 2-3 floors", parsed))

    def test_hard_caps_and_mins(self) -> None:
        parsed = parse_brief(self.BRIEF, self.names)
        self.assertEqual(parsed.mass_count, 3)
        self.assertEqual(parsed.max_stories, 3)
        self.assertAlmostEqual(
            float(parsed.constraints["max_edge_ft"]), 70 * 3.280839895, places=2
        )
        self.assertAlmostEqual(
            float(parsed.constraints["min_edge_ft"]), 20 * 3.280839895, places=2
        )
        self.assertEqual(unaccounted_numbers("length max 70 meters and min 20 meters", parsed), [])

    def test_required_width_and_ratio_preference(self) -> None:
        parsed = parse_brief(self.BRIEF, self.names)
        widths = parsed.constraints.get("department_widths") or {}
        self.assertEqual(widths.get(CORE), 80.0)
        self.assertEqual(widths.get(SPECIAL), 80.0)
        self.assertAlmostEqual(float(parsed.length_over_width or 0), 4 / 7, places=4)

    def test_grouping_and_vertical_intent(self) -> None:
        parsed = parse_brief(self.BRIEF, self.names)
        self.assertIn((HPE, DINING), parsed.keep_together)
        self.assertEqual(set(parsed.double_height_departments), {HPE, DINING})
        self.assertIn(ADMIN, parsed.pin_ground)
        self.assertIn(MEDIA, parsed.pin_ground)
        kinds = parsed.constraints.get("pin_ground_kind") or {}
        self.assertEqual(kinds.get(ADMIN), "requirement")
        self.assertEqual(kinds.get(MEDIA), "preference")

    def test_soft_story_range_not_a_second_hard_cap(self) -> None:
        parsed = parse_brief(self.BRIEF, self.names)
        self.assertEqual(parsed.constraints.get("stories_min"), 2.0)
        self.assertEqual(parsed.constraints.get("stories_max"), 3.0)
        # Hard ceiling stays max 3 floors; prefer range must not invent a 2-story lock.
        self.assertEqual(parsed.max_stories, 3)
        nums = stated_numbers("Prefer 2-3 floors")
        self.assertTrue(nums)
        self.assertTrue(all(n["kind"] == "stories" for n in nums))

    def test_briefing_buckets(self) -> None:
        parsed = parse_brief(self.BRIEF, self.names)
        briefing = briefing_from_parsed(parsed)
        req = _levers(briefing, "requirements")
        lim = _levers(briefing, "limitations")
        pref = _levers(briefing, "preferences")
        self.assertIn("keep_together", req)
        self.assertIn("double_height", req)
        self.assertTrue({"max_edge", "min_edge", "max_stories"} & lim or "max_stories" in lim | req)
        self.assertTrue(
            any(c.get("lever") == "min_edge" for c in briefing["limitations"]),
            briefing["limitations"],
        )
        self.assertTrue(
            any(c.get("lever") in {"length_over_width", "ratio"} for c in briefing["preferences"])
            or "length_over_width" in pref
            or any("ratio" in (c.get("lever") or "") for c in briefing["preferences"]),
            briefing["preferences"],
        )
        # Admin ground = requirement; media ground = preference.
        pin_req = [
            c for c in briefing["requirements"] if c.get("lever") in {"pin_ground", "ground"}
        ]
        pin_pref = [
            c for c in briefing["preferences"] if c.get("lever") in {"pin_ground", "ground"}
        ]
        self.assertTrue(
            any(ADMIN in (c.get("departments") or []) for c in pin_req),
            pin_req,
        )
        self.assertTrue(
            any(MEDIA in (c.get("departments") or []) for c in pin_pref),
            pin_pref,
        )


class IntentSmokeRoles(unittest.TestCase):
    """Requirement / limitation / preference wording lands in the right bucket."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.names = _names()

    def test_must_width_is_requirement(self) -> None:
        text = "core academic width has to be 80 feet"
        parsed = parse_brief(text, self.names)
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(
                c.get("lever") in {"required_width", "width", "department_width"}
                and CORE in (c.get("departments") or [CORE])
                for c in briefing["requirements"]
            )
            or (parsed.constraints.get("department_widths") or {}).get(CORE) == 80.0
        )
        qs, _ = find_brief_questions(text, use_llm=False, department_names=self.names)
        self.assertEqual(qs, [])

    def test_no_more_than_stories_is_limitation(self) -> None:
        text = "no more than 3 floors"
        parsed = parse_brief(text, self.names)
        self.assertEqual(parsed.max_stories, 3)
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(c.get("lever") == "max_stories" for c in briefing["limitations"])
            or any(c.get("lever") == "max_stories" for c in briefing["requirements"])
        )

    def test_prefer_stories_is_preference(self) -> None:
        text = "floor height prefer 2 stories"
        parsed = parse_brief(text, self.names)
        self.assertEqual(parsed.constraints.get("preferred_stories"), 2.0)
        briefing = briefing_from_parsed(parsed)
        self.assertTrue(
            any(c.get("lever") == "preferred_stories" for c in briefing["preferences"]),
            briefing["preferences"],
        )

    def test_keep_apart_is_requirement(self) -> None:
        text = "dining and special education must be apart"
        parsed = parse_brief(text, self.names)
        self.assertTrue(parsed.keep_apart)
        briefing = briefing_from_parsed(parsed)
        self.assertIn("keep_apart", _levers(briefing, "requirements"))

    def test_compound_prefer_and_must_ground_keep_roles(self) -> None:
        text = "Media prefer ground, admin must ground."
        parsed = parse_brief(text, self.names)
        kinds = parsed.constraints.get("pin_ground_kind") or {}
        self.assertEqual(kinds.get(MEDIA), "preference")
        self.assertEqual(kinds.get(ADMIN), "requirement")


class IntentSmokeNumberAccounting(unittest.TestCase):
    """Every stated size either lands on a lever or asks once — never loops."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.names = _names()

    def test_spelled_meters_accounted(self) -> None:
        text = "each length under forty meters"
        parsed = parse_brief(text, self.names)
        self.assertTrue(clause_was_captured(text, parsed), accounted_magnitudes(parsed))
        qs, _ = find_brief_questions(text, use_llm=False, department_names=self.names)
        self.assertEqual(qs, [])

    def test_max_and_min_same_clause(self) -> None:
        text = "length max 40 meters and min 15 meters"
        parsed = parse_brief(text, self.names)
        self.assertIsNotNone(parsed.constraints.get("min_edge_ft"))
        self.assertEqual(unaccounted_numbers(text, parsed), [])
        qs, _ = find_brief_questions(text, use_llm=False, department_names=self.names)
        self.assertFalse(any("15" in (q.get("unplaced_numbers") or "") for q in qs), qs)

    def test_story_range_both_ends_are_stories(self) -> None:
        for clause in ("Prefer 2-3 floors", "keep most boxes to only 2 to 3 stories"):
            nums = stated_numbers(clause)
            self.assertGreaterEqual(len(nums), 2, clause)
            self.assertTrue(all(n["kind"] == "stories" for n in nums), (clause, nums))

    def test_answered_unresolved_does_not_repeat(self) -> None:
        text = "keep the frobulator under 77 zorks beside the mass length"
        qs, _ = find_brief_questions(text, use_llm=False, department_names=self.names)
        self.assertTrue(qs)
        qs2, _ = find_brief_questions(
            text,
            answers=[{"text": text, "kind": "limitation", "reason": "unresolved"}],
            use_llm=False,
            department_names=self.names,
        )
        self.assertFalse(any((q.get("text") or "") == text for q in qs2))

    def test_double_height_not_number_eight(self) -> None:
        text = "gym and dining together and double height"
        self.assertFalse(stated_numbers(text))
        qs, parsed = find_brief_questions(text, use_llm=False, department_names=self.names)
        # Grouping already grounded → do not block generate on modality.
        self.assertEqual(qs, [])
        self.assertTrue(parsed.keep_together)
        self.assertTrue(parsed.double_height_departments)


class IntentSmokeAmbiguity(unittest.TestCase):
    """Ask only when something is truly unplaced; skip grounded grouping."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.names = _names()

    def test_unknown_size_unit_asks(self) -> None:
        text = "keep the frobulator under 77 zorks"
        qs, parsed = find_brief_questions(text, use_llm=False, department_names=self.names)
        self.assertFalse(clause_was_captured(text, parsed))
        self.assertTrue(qs)
        self.assertTrue(any(q.get("reason") in {"modality", "unresolved"} for q in qs))

    def test_min_token_not_administration(self) -> None:
        text = "length max 70 meters and min 20 meters"
        parsed = parse_brief(text, self.names)
        self.assertNotIn(ADMIN, parsed.double_height_departments)
        self.assertNotIn(ADMIN, parsed.pin_ground)
        self.assertAlmostEqual(
            float(parsed.constraints["min_edge_ft"]), 20 * 3.280839895, places=2
        )

    def test_reparse_stable_no_new_questions(self) -> None:
        brief = IntentSmokeUnderwood.BRIEF
        qs1, p1 = find_brief_questions(brief, use_llm=False, department_names=self.names)
        qs2, p2 = find_brief_questions(brief, use_llm=False, department_names=self.names)
        self.assertEqual(qs1, [])
        self.assertEqual(qs2, [])
        self.assertEqual(p1.max_stories, p2.max_stories)
        self.assertEqual(p1.constraints.get("min_edge_ft"), p2.constraints.get("min_edge_ft"))


class IntentSmokeAlternateBriefs(unittest.TestCase):
    """A few non-Underwood shapes still interpret without a generate pause."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.names = _names()

    def test_compact_two_mass_low_rise(self) -> None:
        text = (
            "two masses. no more than 2 stories. each length under 180 ft. "
            "prefer compact footprints."
        )
        qs, parsed = find_brief_questions(text, use_llm=False, department_names=self.names)
        self.assertEqual(qs, [])
        self.assertEqual(parsed.mass_count, 2)
        self.assertEqual(parsed.max_stories, 2)

    def test_paired_frontage_cap(self) -> None:
        text = (
            "pair academic with art. combined length should stay under 280 feet. "
            "site frontage 340 ft."
        )
        qs, parsed = find_brief_questions(text, use_llm=False, department_names=self.names)
        # May or may not ask depending on pairing cues; sizes must not be unplaced.
        for q in qs:
            self.assertFalse(q.get("unplaced_numbers"), q)
        if parsed.pair_length_ft or parsed.constraints.get("max_total_length_ft"):
            self.assertTrue(
                parsed.pair_length_ft
                or parsed.constraints.get("max_total_length_ft")
                or parsed.constraints.get("max_building_length_ft")
            )


if __name__ == "__main__":
    unittest.main()
