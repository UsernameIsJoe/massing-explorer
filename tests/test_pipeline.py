"""Pipeline: brief parsing, engine grouping, pairing-aware resize."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from massing_explorer.brief import apply_brief, parse_brief
from massing_explorer.group import group_departments, match_department
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession
from massing_explorer.solver import solve_massing_study
from massing_explorer.tools import execute_tool, pair_masses, resize_mass, set_grouping

ROOT = Path(__file__).resolve().parents[1]
UNDERWOOD = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

CORE = "CORE ACADEMIC"
SPED = "SPECIAL EDUCATION"
HPE = "HEALTH & PHYSICAL EDUCATION"
DINING = "DINING & FOOD SERVICE"
ART = "ART & MUSIC"
MEDIA = "MEDIA CENTER"
ADMIN = "ADMINISTRATION & GUIDANCE"
CUSTODIAL = "CUSTODIAL & MAINTENANCE"
MEDICAL = "MEDICAL"


class TestMatchDepartment(unittest.TestCase):
    def setUp(self) -> None:
        self.names = [
            CORE, SPED, HPE, DINING, ART, MEDIA, ADMIN, CUSTODIAL, MEDICAL,
        ]

    def test_substring(self) -> None:
        self.assertEqual(match_department("dining", self.names), DINING)
        self.assertEqual(match_department("custodial", self.names), CUSTODIAL)

    def test_typo(self) -> None:
        self.assertEqual(match_department("custodiala", self.names), CUSTODIAL)


class TestParseBrief(unittest.TestCase):
    def setUp(self) -> None:
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.names = [d.name for d in program.departments]

    def test_attached_to_and_paired_with(self) -> None:
        parsed = parse_brief(
            "custodial should be attached to dining, and media should be "
            "paired with administration",
            self.names,
        )
        pairs = {tuple(sorted(p)) for p in parsed.keep_together}
        self.assertIn(tuple(sorted((CUSTODIAL, DINING))), pairs)
        self.assertIn(tuple(sorted((MEDIA, ADMIN))), pairs)

    def test_example_prompt(self) -> None:
        parsed = parse_brief(
            "custodial and dining should stay together, site length is 300, "
            "width is 100, the max story is 4 floor",
            self.names,
        )
        self.assertEqual(parsed.constraints["max_total_length_ft"], 300)
        self.assertEqual(parsed.constraints["max_building_width_ft"], 100)
        self.assertEqual(parsed.max_stories, 4)
        pairs = {tuple(sorted(p)) for p in parsed.keep_together}
        self.assertIn(tuple(sorted((CUSTODIAL, DINING))), pairs)

    def test_typo_prompt(self) -> None:
        parsed = parse_brief(
            "custodiala nd dining hsould stay together, site length is 280, "
            "width is 90, max story is 3",
            self.names,
        )
        self.assertEqual(parsed.constraints["max_total_length_ft"], 280)
        self.assertEqual(parsed.max_stories, 3)
        pairs = {tuple(sorted(p)) for p in parsed.keep_together}
        self.assertIn(tuple(sorted((CUSTODIAL, DINING))), pairs)

    def test_shorter_than_is_a_cap_not_an_exact_length(self) -> None:
        parsed = parse_brief(
            "Length should be shorter than 50. Prefer ratio 3:5.",
            self.names,
        )
        self.assertEqual(parsed.constraints["max_building_length_ft"], 50)
        self.assertEqual(parsed.constraints["length_limit_is_cap"], 1)
        self.assertNotIn("exact_building_length_ft", parsed.constraints)

    def test_length_should_be_is_exact(self) -> None:
        parsed = parse_brief("The building length should be 50.", self.names)
        self.assertEqual(parsed.constraints["exact_building_length_ft"], 50)
        self.assertNotIn("max_building_length_ft", parsed.constraints)

    def test_meters_convert_to_feet(self) -> None:
        parsed = parse_brief("Length should be shorter than 59 meters.", self.names)
        self.assertAlmostEqual(
            parsed.constraints["max_building_length_ft"], 59 * 3.280839895, places=4
        )
        self.assertEqual(parsed.constraints["length_limit_is_cap"], 1)

    def test_each_mass_not_longer_than_meters(self) -> None:
        parsed = parse_brief(
            "Each mass should not be longer than 75 meters.",
            self.names,
        )
        self.assertAlmostEqual(
            parsed.constraints["max_building_length_ft"], 75 * 3.280839895, places=4
        )
        self.assertEqual(parsed.constraints["length_limit_is_cap"], 1)

    def test_each_mass_prefer_two_floors(self) -> None:
        parsed = parse_brief("each mass prefer to be 2 floors", self.names)
        self.assertEqual(parsed.constraints.get("preferred_stories"), 2.0)
        self.assertIsNone(parsed.max_stories)
        briefing = __import__(
            "massing_explorer.brief", fromlist=["briefing_from_parsed"]
        ).briefing_from_parsed(parsed)
        self.assertTrue(
            any(
                c.get("lever") == "preferred_stories" and float(c.get("value") or 0) == 2
                for c in briefing["preferences"]
            )
        )

    def test_preferred_stories_under_hard_max(self) -> None:
        parsed = parse_brief(
            "each mass can be no more than 3 stories. each mass prefer to be 2 floors.",
            self.names,
        )
        self.assertEqual(parsed.max_stories, 3)
        self.assertEqual(parsed.constraints.get("preferred_stories"), 2.0)
        parsed = parse_brief(
            "each mass can be no more than 3 stories and no longer than 210 ft, "
            "and I'd like the mass proportion to stay close to 3:4. "
            "Core Academic should maintain an 80 ft width.",
            self.names,
        )
        self.assertEqual(parsed.max_stories, 3)
        self.assertAlmostEqual(parsed.constraints["max_building_length_ft"], 210.0)
        self.assertNotIn("max_total_length_ft", parsed.constraints)
        self.assertAlmostEqual(parsed.length_over_width or 0.0, 3.0 / 4.0, places=4)
        self.assertEqual(
            parsed.constraints.get("department_widths", {}).get("CORE ACADEMIC"), 80.0
        )

    def test_frontage_and_low_rise(self) -> None:
        parsed = parse_brief(
            "300 ft of frontage, nothing wider than 80, keep it low rise",
            self.names,
        )
        self.assertEqual(parsed.constraints["max_total_length_ft"], 300)
        self.assertEqual(parsed.constraints["max_building_width_ft"], 80)
        self.assertEqual(parsed.preference, "low_rise")

    def test_spoken_four_masses_gym_dining_site_cap(self) -> None:
        """Natural brief: word count, 'gym', 'together', 'no longer than'."""
        from massing_explorer.group import match_department

        self.assertEqual(
            match_department("gym", self.names), "HEALTH & PHYSICAL EDUCATION"
        )
        parsed = parse_brief(
            "Four masses. Gym and dining together. Art on the ground floor. "
            "Site no longer than 400 feet. Keep it low if you can.",
            self.names,
        )
        self.assertEqual(parsed.mass_count, 4)
        self.assertEqual(parsed.constraints["max_total_length_ft"], 400)
        self.assertEqual(parsed.preference, "low_rise")
        self.assertIn("ART & MUSIC", parsed.pin_ground)
        pairs = {tuple(sorted(p)) for p in parsed.keep_together}
        self.assertIn(
            tuple(sorted(("HEALTH & PHYSICAL EDUCATION", "DINING & FOOD SERVICE"))),
            pairs,
        )
        from massing_explorer.brief import assign_open_departments

        assign_open_departments(parsed)
        home = {d: name for name, depts in parsed.named_masses for d in depts}
        self.assertEqual(len(parsed.named_masses), 4)
        self.assertEqual(
            home["HEALTH & PHYSICAL EDUCATION"], home["DINING & FOOD SERVICE"]
        )


class TestGrouping(unittest.TestCase):
    def setUp(self) -> None:
        self.program = load_program_file(UNDERWOOD, config_path=CONFIG)

    def test_every_department_assigned(self) -> None:
        result = group_departments(self.program)
        assigned = {d for m in result.masses for d in m.departments}
        self.assertEqual(assigned, {d.name for d in self.program.departments})

    def test_keep_together_merges_families(self) -> None:
        result = group_departments(
            self.program, keep_together=[(CUSTODIAL, DINING)]
        )
        home = {
            d: m.id
            for m in result.masses
            for d in m.departments
        }
        self.assertEqual(home[CUSTODIAL], home[DINING])

    def test_keep_apart_splits(self) -> None:
        result = group_departments(
            self.program, keep_apart=[(ART, "CORE ACADEMIC")]
        )
        # ART is already a different family; still assigned
        assigned = {d for m in result.masses for d in m.departments}
        self.assertIn(ART, assigned)


class TestApplyBrief(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="brief_run", program=program, config_path=str(CONFIG)
        )
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_pipeline_groups_and_fits_site(self) -> None:
        out = apply_brief(
            self.session,
            "custodial and dining should stay together, site length is 400, "
            "width is 100, the max story is 4 floor",
        )
        self.assertTrue(out["ok"])
        home = {
            d: m["id"]
            for m in out["grouping"]
            for d in m["departments"]
        }
        self.assertEqual(home[CUSTODIAL], home[DINING])
        self.assertEqual(self.session.constraints["max_total_length_ft"], 400)
        self.assertEqual(self.session.constraints["max_building_width_ft"], 100)
        self.assertEqual(self.session.constraints["max_stories"], 4)
        self.assertTrue(self.session.masses)
        assigned = {d for m in self.session.masses for d in m.departments}
        self.assertEqual(assigned, set(self.session.department_names()))

    def test_named_wings_are_applied_and_not_overwritten(self) -> None:
        text = (
            "The gym and art should be in mass one. Mass two is dining and the media center. "
            "Academic and special education stay together. Mass one and mass two together "
            "should be under 360 feet. The site frontage is 420 feet, nothing wider than "
            "80 feet, and no more than 3 stories. Keep it low. The gymnasium is double height."
        )
        out = apply_brief(self.session, text)
        home = {
            d: m["id"]
            for m in out["grouping"]
            for d in m["departments"]
        }
        self.assertEqual(home[HPE], home[ART])
        self.assertEqual(home[DINING], home[MEDIA])
        self.assertEqual(home[CORE], home[SPED])
        self.assertNotEqual(home[HPE], home[DINING])
        self.assertNotEqual(home[HPE], home[CORE])
        assigned = {d for m in self.session.masses for d in m.departments}
        self.assertEqual(assigned, set(self.session.department_names()))
        self.assertAlmostEqual(self.session.pairings[0].total_length_ft, 360)
        self.assertTrue(self.session.pairings[0].length_is_cap)
        self.assertEqual(self.session.constraints["max_total_length_ft"], 420)
        self.assertEqual(self.session.constraints["max_building_width_ft"], 80)
        self.assertEqual(self.session.constraints["max_stories"], 3)
        self.assertIn("Gymnasium", self.session.double_height_rooms)
        self.assertTrue(self.session.brief_locked)
        blocked = execute_tool(
            self.session,
            "set_grouping",
            {
                "masses": [
                    {
                        "id": "only",
                        "name": "Academic Support",
                        "departments": [CORE, SPED],
                    }
                ]
            },
        )
        self.assertIn("Do not call", blocked)
        self.assertEqual(
            {d for m in self.session.masses for d in m.departments},
            assigned,
        )

    def test_four_mass_brief_is_executed(self) -> None:
        text = (
            "I want 4 masses. Gym and dining should be double height and in the same mass. "
            "Core academic by itself is one mass. Art and music on the ground floor. "
            "Prefer ratio 3:5. Max 3 stories. Length should be shorter than 50."
        )
        out = apply_brief(self.session, text)
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(self.session.masses), 4)
        home = {
            d: m.id
            for m in self.session.masses
            for d in m.departments
        }
        self.assertEqual(home[HPE], home[DINING])
        gym_mass = next(m for m in self.session.masses if HPE in m.departments)
        academic = next(m for m in self.session.masses if CORE in m.departments)
        art = next(m for m in self.session.masses if ART in m.departments)
        self.assertEqual(set(gym_mass.departments), {HPE, DINING})
        self.assertEqual(academic.departments, [CORE])
        self.assertGreater(len(art.departments), 1)
        self.assertIn(ART, art.departments)
        self.assertNotEqual(art.story_count, 1)
        self.assertEqual(gym_mass.story_count, 2)
        self.assertLessEqual(academic.story_count, 3)
        self.assertEqual(self.session.floor_pins.get(ART), 0)
        self.assertAlmostEqual(self.session.constraints["length_over_width"], 3 / 5)
        self.assertEqual(self.session.constraints["max_building_length_ft"], 50)
        self.assertEqual(self.session.constraints["length_limit_is_cap"], 1)
        self.assertNotIn("exact_building_length_ft", self.session.constraints)
        self.assertFalse(self.session.pairings)
        self.assertTrue(self.session.brief_locked)

        result = solve_massing_study(self.session)
        by_id = {m.id: m for m in result.masses}
        # 50 is a cap. Bars are sized to the 3:5 ratio, not stretched to 50.
        for mass in result.masses:
            length = mass.floors[0].length_ft
            width = mass.floors[0].width_ft
            self.assertGreater(
                abs(length - 50.0),
                0.4,
                f"{mass.name} length {length} looks filled to the 50 ft cap",
            )
            self.assertAlmostEqual(length / width, 0.6, delta=0.05)
        gym = by_id[gym_mass.id]
        self.assertTrue(any(a.double_height for a in gym.floors[0].allocations))
        self.assertTrue(
            all(
                a.double_height
                for a in gym.floors[0].allocations
                if a.department in {HPE, DINING}
            )
        )
        art_levels = [
            floor.level
            for floor in by_id[art.id].floors
            for alloc in floor.allocations
            if alloc.department == ART
        ]
        self.assertEqual(art_levels, [0])

        blocked = execute_tool(
            self.session,
            "pair_masses",
            {"mass_ids": [m.id for m in self.session.masses], "total_length_ft": 50},
        )
        self.assertIn("Do not pair", blocked)

    def test_search_reads_limits_from_session(self) -> None:
        from massing_explorer.tools import search_site_schemes

        apply_brief(
            self.session,
            "site length is 350, width is 100, max story is 4",
            search=False,
        )
        # Even if the tool is called with no numeric args, session limits apply
        out = search_site_schemes(self.session)
        self.assertTrue(out["ok"])
        self.assertEqual(out["limits_applied"].get("max_total_length_ft"), 350)
        self.assertNotIn("max_total_length_ft", out["limits_not_checked"])


class TestPairingAwareResize(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        program = load_program_file(UNDERWOOD, config_path=CONFIG)
        self.session = StudySession(
            study_id="pair_resize", program=program, config_path=str(CONFIG)
        )
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED, ART],
                    "story_count": 3,
                },
                {
                    "id": "community",
                    "name": "Community",
                    "departments": [HPE, DINING, MEDIA, ADMIN, MEDICAL, CUSTODIAL],
                    "story_count": 2,
                },
            ],
        )
        self.session.save()

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def test_suggestion_does_not_offer_independent_width(self) -> None:
        self.session.constraints["max_building_length_ft"] = 120
        pair_masses(self.session, ["academic", "community"], 600)
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        self.assertTrue(result.resize_suggestions)
        for suggestion in result.resize_suggestions:
            self.assertIsNone(
                suggestion.option_width_ft,
                suggestion.suggestion,
            )
            self.assertIn("paired", suggestion.suggestion.lower())

    def test_resize_width_updates_pairing_length(self) -> None:
        pair_masses(self.session, ["academic", "community"], 400)
        before = solve_massing_study(self.session, config_path=str(CONFIG))
        shared = before.masses[0].fixed_dim_ft
        out = resize_mass(self.session, "academic", width_ft=shared * 2)
        self.assertTrue(out["ok"])
        self.assertTrue(any("pairing" in c for c in out["changes"]))
        after = solve_massing_study(self.session, config_path=str(CONFIG))
        widths = {m.fixed_dim_ft for m in after.masses}
        self.assertEqual(len(widths), 1, "paired masses must still share one width")
        self.assertAlmostEqual(after.masses[0].fixed_dim_ft, shared * 2, delta=1.0)
        combined = sum(m.floors[0].length_ft for m in after.masses)
        self.assertAlmostEqual(combined, 400 / 2, delta=2.0)

    def test_site_total_suggestion_targets_unpaired_mass(self) -> None:
        set_grouping(
            self.session,
            [
                {
                    "id": "academic",
                    "name": "Academic",
                    "departments": [CORE, SPED],
                    "story_count": 2,
                },
                {
                    "id": "support",
                    "name": "Support",
                    "departments": [ADMIN, MEDIA, MEDICAL, ART, CUSTODIAL],
                    "story_count": 2,
                },
                {
                    "id": "athletics",
                    "name": "Athletics",
                    "departments": [HPE, DINING],
                    "story_count": 2,
                },
            ],
        )
        pair_masses(self.session, ["academic", "support"], 280)
        self.session.constraints["athletics_width_ft"] = 100
        self.session.constraints["max_total_length_ft"] = 300
        result = solve_massing_study(self.session, config_path=str(CONFIG))
        total_fail = [
            s for s in result.resize_suggestions if "combined length" in s.issue
        ]
        self.assertTrue(total_fail)
        self.assertEqual(total_fail[0].mass_id, "athletics")
        self.assertIn("unpaired", total_fail[0].suggestion.lower())


if __name__ == "__main__":
    unittest.main()
