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
from massing_explorer.tools import pair_masses, resize_mass, set_grouping

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

    def test_frontage_and_low_rise(self) -> None:
        parsed = parse_brief(
            "300 ft of frontage, nothing wider than 80, keep it low rise",
            self.names,
        )
        self.assertEqual(parsed.constraints["max_total_length_ft"], 300)
        self.assertEqual(parsed.constraints["max_building_width_ft"], 80)
        self.assertEqual(parsed.preference, "low_rise")


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
        solved = out.get("solved") or {}
        self.assertTrue(solved.get("ok"))
        self.assertLessEqual(solved.get("total_ground_length_ft", 999), 401)

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
