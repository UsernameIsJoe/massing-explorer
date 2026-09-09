"""Preview mesh matches Rhino-style program splits and legend."""

from __future__ import annotations

import unittest
from pathlib import Path

from massing_explorer.brief import apply_parsed_brief, parse_brief
from massing_explorer.load import load_program_file
from massing_explorer.preview3d import preview_mesh
from massing_explorer.session import StudySession
from massing_explorer.solver import solve_massing_study
from massing_explorer.tools import apply_scheme, search_site_schemes, solve_dimensions

ROOT = Path(__file__).resolve().parents[1]


class TestPreview3D(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config = str(ROOT / "config" / "project.example.yaml")
        program = load_program_file(
            ROOT / "examples" / "underwood_elementary_space_summary.xlsx",
            config_path=config,
        )
        session = StudySession(
            study_id="preview_ui_check", program=program, config_path=config
        )
        text = (
            "Four masses. Gym and dining in the same building and the art spaces "
            "on the ground floor. Site under 400 ft."
        )
        parsed = parse_brief(text, session.department_names())
        apply_parsed_brief(session, parsed, search=False)
        searched = search_site_schemes(
            session,
            max_total_length_ft=session.constraints.get("max_total_length_ft"),
            max_stories=4,
            preference="balanced",
            top_n=3,
        )
        if searched.get("ok") and searched.get("found"):
            apply_scheme(session, 0)
        else:
            solve_dimensions(session)
        cls.result = solve_massing_study(session)
        cls.mesh = preview_mesh(cls.result, config={"story_height_ft": 14})

    def test_program_split_boxes(self) -> None:
        self.assertGreaterEqual(len(self.mesh["boxes"]), len(self.result.masses))
        depts = {b["department"] for b in self.mesh["boxes"]}
        self.assertGreaterEqual(len(depts), 2)
        self.assertGreater(len({b["color"] for b in self.mesh["boxes"]}), 1)

    def test_legend_present(self) -> None:
        legend = self.mesh["legend"]
        self.assertTrue(legend["departments"])
        self.assertEqual(len(legend["masses"]), len(self.result.masses))
        for entry in legend["departments"]:
            self.assertTrue(entry["name"])
            self.assertTrue(entry["color"].startswith("#"))

    def test_box_fields(self) -> None:
        box = self.mesh["boxes"][0]
        for key in ("department", "mass", "color", "dx", "dy", "dz", "double_height"):
            self.assertIn(key, box)

    def test_scheme_envelope_mesh(self) -> None:
        from massing_explorer.preview3d import scheme_envelope_mesh

        mesh = scheme_envelope_mesh(
            {
                "verified": True,
                "total_length_ft": 260,
                "masses": [
                    {
                        "mass_id": "a",
                        "mass_name": "A",
                        "stories": 3,
                        "width_ft": 60,
                        "length_ft": 140,
                    },
                    {
                        "mass_id": "b",
                        "mass_name": "B",
                        "stories": 2,
                        "width_ft": 50,
                        "length_ft": 100,
                    },
                ],
            },
            story_height_ft=14.0,
            rank=1,
        )
        self.assertTrue(mesh["envelope"])
        self.assertEqual(len(mesh["boxes"]), 2)
        self.assertAlmostEqual(mesh["boxes"][0]["dz"], 42.0)
        self.assertGreater(mesh["extent"]["x"], 240)


if __name__ == "__main__":
    unittest.main()
