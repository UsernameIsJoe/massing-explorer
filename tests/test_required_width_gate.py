"""Exact department width is a hard requirement; preferred stories are soft."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from massing_explorer.explore.performance import measure, preference_distance
from massing_explorer.load import load_program_file
from massing_explorer.search import SiteEnvelope, _mass_options, apply_scheme, search_schemes
from massing_explorer.session import StudySession
from massing_explorer.solver import required_width_ft, solve_massing_study
from massing_explorer.tools import set_grouping


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "project.example.yaml"
EXAMPLE = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
CORE = "CORE ACADEMIC"
SPED = "SPECIAL EDUCATION"
ADMIN = "ADMINISTRATION & GUIDANCE"


class RequiredWidthGateTests(unittest.TestCase):
    def setUp(self) -> None:
        import massing_explorer.session as session_mod

        self._orig = session_mod.STUDIES_DIR
        self.tmp = tempfile.TemporaryDirectory()
        session_mod.STUDIES_DIR = Path(self.tmp.name) / "studies"
        self.program = load_program_file(str(EXAMPLE), config_path=str(CONFIG))

    def tearDown(self) -> None:
        import massing_explorer.session as session_mod

        session_mod.STUDIES_DIR = self._orig
        self.tmp.cleanup()

    def _session(self) -> StudySession:
        session = StudySession(
            study_id="req_width",
            program=self.program,
            config_path=str(CONFIG),
        )
        set_grouping(
            session,
            [
                {
                    "id": "mass_1",
                    "name": "Mass 1",
                    "departments": [CORE, SPED],
                    "story_count": 2,
                },
                {
                    "id": "mass_2",
                    "name": "Mass 2",
                    "departments": [ADMIN],
                    "story_count": 2,
                },
            ],
        )
        session.constraints["department_widths"] = {CORE: 80.0, SPED: 80.0}
        session.constraints["max_stories"] = 3
        session.constraints["max_building_length_ft"] = 400.0
        session.constraints["max_building_width_ft"] = 200.0
        session.constraints["preferred_stories"] = 2.0
        return session

    def test_required_width_helper(self) -> None:
        session = self._session()
        self.assertEqual(required_width_ft(session, session.masses[0]), 80.0)
        self.assertIsNone(required_width_ft(session, session.masses[1]))

    def test_search_only_offers_required_width(self) -> None:
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=400,
            max_building_width_ft=200,
            max_stories=3,
        )
        from massing_explorer.config import load_project_config

        config = load_project_config(str(CONFIG))
        opts = _mass_options(session, session.masses[0], config, envelope)
        self.assertTrue(opts)
        self.assertTrue(all(abs(o.width_ft - 80.0) < 0.05 for o in opts))

    def test_apply_scheme_cannot_overwrite_required_width(self) -> None:
        session = self._session()
        envelope = SiteEnvelope(
            max_building_length_ft=400,
            max_building_width_ft=200,
            max_total_length_ft=600,
            max_stories=3,
        )
        candidates, _ = search_schemes(
            session, envelope, top_n=1, config_path=str(CONFIG), verify=False
        )
        self.assertTrue(candidates)
        candidates[0].options[0].width_ft = 95.0
        apply_scheme(session, candidates[0], save=False)
        self.assertAlmostEqual(session.constraints["mass_1_width_ft"], 80.0, places=2)

    def test_solve_keeps_required_width_despite_stamped_wrong_key(self) -> None:
        session = self._session()
        session.constraints["mass_1_width_ft"] = 95.0
        result = solve_massing_study(session, config_path=str(CONFIG))
        academic = next(m for m in result.masses if m.id == "mass_1")
        self.assertAlmostEqual(academic.floors[0].width_ft, 80.0, places=1)

    def test_wrong_width_fails_legal_gate(self) -> None:
        session = self._session()
        rogue = SimpleNamespace(
            masses=[
                SimpleNamespace(
                    id="mass_1",
                    floors=[
                        SimpleNamespace(
                            level=0,
                            width_ft=95.0,
                            length_ft=100.0,
                            allocations=[],
                            usable_area_sf=9500,
                            allocated_gsf=9500,
                        )
                    ],
                )
            ],
            validation=[
                SimpleNamespace(
                    check="required_width:mass_1",
                    passed=False,
                    message="Mass 1: width 95.0 ft vs required 80 ft",
                )
            ],
        )
        perf = measure(rogue, session)
        self.assertFalse(perf["fits_limitations"])
        self.assertIn("required_width", perf["failed_kinds"])

    def test_preferred_stories_affects_preference_distance(self) -> None:
        session = self._session()
        near = preference_distance(
            SimpleNamespace(
                masses=[
                    SimpleNamespace(
                        id="mass_1",
                        floors=[
                            SimpleNamespace(level=0, width_ft=80, length_ft=100, allocations=[]),
                            SimpleNamespace(level=1, width_ft=80, length_ft=100, allocations=[]),
                        ],
                    ),
                    SimpleNamespace(
                        id="mass_2",
                        floors=[
                            SimpleNamespace(level=0, width_ft=60, length_ft=80, allocations=[]),
                            SimpleNamespace(level=1, width_ft=60, length_ft=80, allocations=[]),
                        ],
                    ),
                ],
                validation=[],
            ),
            session,
        )
        far = preference_distance(
            SimpleNamespace(
                masses=[
                    SimpleNamespace(
                        id="mass_1",
                        floors=[
                            SimpleNamespace(level=i, width_ft=80, length_ft=100, allocations=[])
                            for i in range(3)
                        ],
                    ),
                    SimpleNamespace(
                        id="mass_2",
                        floors=[
                            SimpleNamespace(level=i, width_ft=60, length_ft=80, allocations=[])
                            for i in range(3)
                        ],
                    ),
                ],
                validation=[],
            ),
            session,
        )
        self.assertLess(near, far)


if __name__ == "__main__":
    unittest.main()
