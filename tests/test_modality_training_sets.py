"""Regression: modality training sets (requirement / limitation / preference).

Loads ``llm_modality_training_50_sets.txt`` from Downloads when present;
otherwise skips. Asserts number-agnostic semantic signals and correct
briefing buckets for each labeled clause.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from massing_explorer.brief import briefing_from_parsed, parse_brief
from massing_explorer.load import load_program_file


TRAINING = Path(r"c:\Users\tu\Downloads\llm_modality_training_50_sets.txt")
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "underwood_elementary_space_summary.xlsx"

_NUM = r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
_WORD = r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|\d+)"


def _load_sets(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    sets: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        m = re.match(r"^SET\s+(\d+)\s*$", line.strip(), flags=re.I)
        if m:
            if current:
                sets.append(current)
            current = {
                "id": int(m.group(1)),
                "requirement": "",
                "limitation": "",
                "preference": "",
            }
            continue
        if current is None:
            continue
        for key in ("requirement", "limitation", "preference"):
            pref = key.upper() + ":"
            if line.strip().upper().startswith(pref):
                current[key] = line.split(":", 1)[1].strip()
                break
    if current:
        sets.append(current)
    return sets


def _first_num(text: str) -> float | None:
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    }
    m = re.search(_NUM, text)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b",
        text,
        flags=re.I,
    )
    if m:
        return float(words[m.group(1).lower()])
    return None


def _levers(briefing: dict) -> dict[str, set[str]]:
    out = {"requirement": set(), "limitation": set(), "preference": set()}
    for bucket, kind in (
        ("requirements", "requirement"),
        ("limitations", "limitation"),
        ("preferences", "preference"),
    ):
        for c in briefing.get(bucket) or []:
            out[kind].add(str(c.get("lever") or ""))
    return out


def _has_lever(briefing: dict, lever_substr: str, kinds: set[str] | None = None) -> bool:
    for bucket, kind in (
        ("requirements", "requirement"),
        ("limitations", "limitation"),
        ("preferences", "preference"),
    ):
        if kinds and kind not in kinds:
            continue
        for c in briefing.get(bucket) or []:
            if lever_substr in str(c.get("lever") or ""):
                return True
    return False


@unittest.skipUnless(TRAINING.exists(), "modality training file not present in Downloads")
class ModalityTrainingSets(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.names = [d.name for d in load_program_file(str(EXAMPLE)).departments]
        cls.sets = _load_sets(TRAINING)
        assert len(cls.sets) == 50

    def _parse_set(self, s: dict):
        combined = " ".join(
            p for p in (s["requirement"], s["limitation"], s["preference"]) if p
        )
        parsed = parse_brief(combined, self.names)
        briefing = briefing_from_parsed(parsed)
        return parsed, briefing

    def test_all_sets_parse_core_signals(self) -> None:
        failures: list[str] = []
        for s in self.sets:
            parsed, briefing = self._parse_set(s)
            sid = s["id"]
            req, lim, pref = s["requirement"], s["limitation"], s["preference"]

            # Mass count (exact / range / min / max / preferred)
            if re.search(
                rf"(?:exactly|use|contain|require|include|scheme|plan|project).{{0,30}}"
                rf"(?:{_WORD})\s+(?:primary\s+|main\s+)?mass",
                req,
                re.I,
            ) or re.search(
                rf"there (?:must|need).{{0,20}}{_WORD}\s+mass", req, re.I
            ):
                if re.search(r"share one mass|same mass|own mass", req, re.I):
                    pass
                elif not (
                    parsed.mass_count
                    or parsed.mass_count_min
                    or parsed.mass_count_max
                    or _has_lever(briefing, "mass_count")
                ):
                    failures.append(f"SET {sid}: mass count not picked up from {req!r}")

            if re.search(
                rf"(?:between|from)\s+{_WORD}\s+and\s+{_WORD}\s+mass|"
                rf"{_WORD}\s+to\s+{_WORD}\s+mass",
                req,
                re.I,
            ):
                if not (parsed.mass_count_min and parsed.mass_count_max):
                    failures.append(f"SET {sid}: mass count range not picked up")

            if re.search(rf"at least\s+{_WORD}\s+mass", req, re.I) and not re.search(
                r"remain under|stay under", req, re.I
            ):
                if parsed.mass_count_min is None and not _has_lever(briefing, "mass_count"):
                    failures.append(f"SET {sid}: mass_count_min missing")

            if re.search(
                rf"(?:no more than|not more than)\s+{_WORD}\s+mass|"
                r"count should not exceed",
                lim,
                re.I,
            ):
                if not re.search(r"exceed .+ stor|remain under", lim, re.I):
                    if parsed.mass_count_max is None and not _has_lever(
                        briefing, "mass_count", {"limitation", "requirement"}
                    ):
                        failures.append(f"SET {sid}: mass_count_max missing")

            # Keep together (Gym/Dining) — Art+Music is one Underwood dept
            if re.search(r"gym|dining", req, re.I) and re.search(
                r"same mass|same building|together|share|grouped|adjacent", req, re.I
            ):
                if not parsed.keep_together and not _has_lever(briefing, "keep_together"):
                    if not _has_lever(briefing, "same_mass"):
                        failures.append(f"SET {sid}: keep_together missing")

            # Alone / own mass
            if re.search(r"own mass|by itself", req, re.I):
                if not _has_lever(briefing, "alone"):
                    failures.append(f"SET {sid}: alone missing")

            # Pin ground
            if re.search(
                r"ground floor|at grade|level 1|first occupied|from grade|"
                r"ground-floor|direct ground",
                req + " " + lim,
                re.I,
            ) and re.search(r"art|music|dining|gym", req + " " + lim, re.I):
                if not parsed.pin_ground and not _has_lever(briefing, "pin_ground"):
                    failures.append(f"SET {sid}: pin_ground missing")

            # Double height
            if re.search(r"double[\s-]?height", req + " " + pref, re.I):
                if not parsed.double_height_departments and not _has_lever(
                    briefing, "double_height"
                ):
                    failures.append(f"SET {sid}: double_height missing")

            # Max stories / height
            if re.search(
                r"no taller than|stories? or less|no more than .+floor|"
                r"not exceed .+stor|have more than .+floor|or below",
                lim + " " + req,
                re.I,
            ) and not re.search(r"sqft|footprint|gsf|clear height|ceiling", lim + " " + req, re.I):
                if not (
                    parsed.max_stories
                    or parsed.constraints.get("hard_max_stories")
                    or _has_lever(briefing, "max_stories")
                    or _has_lever(briefing, "hard_max_stories")
                    or _has_lever(briefing, "story_range")
                ):
                    failures.append(f"SET {sid}: max_stories missing")

            if re.search(r"(?:taller|higher|height).{0,20}(?:ft|feet|m\b)", lim, re.I) or re.search(
                r"remain below .{0,10}m .{0,10}height|under .{0,10}ft tall", lim, re.I
            ):
                if not (
                    parsed.constraints.get("max_height_ft")
                    or _has_lever(briefing, "max_height")
                ):
                    failures.append(f"SET {sid}: max_height missing")

            # Length / site length (not ratio / proportion language)
            if re.search(
                r"(?:no longer|not go beyond|capped at|"
                r"longer than|length should|no primary mass may be longer|"
                r"should not exceed .{0,10}ft in length|mass length|"
                r"exceed .{0,20}in length)",
                lim,
                re.I,
            ) and not re.search(r"width-to-length|proportion|\d+\s*:\s*\d+", lim, re.I):
                if not (
                    parsed.constraints.get("max_building_length_ft")
                    or parsed.constraints.get("max_total_length_ft")
                    or parsed.constraints.get("max_edge_ft")
                    or parsed.constraints.get("department_max_edge_ft")
                    or _has_lever(briefing, "max_length")
                    or _has_lever(briefing, "site_length")
                    or _has_lever(briefing, "max_edge")
                    or any(
                        d.get("lever") == "length" and d.get("mode") == "max"
                        for d in (parsed.dimensions or [])
                    )
                ):
                    failures.append(f"SET {sid}: length cap missing")

            if re.search(r"site length|overall site|combined length", lim, re.I):
                if not (
                    parsed.constraints.get("max_total_length_ft")
                    or _has_lever(briefing, "site_length")
                ):
                    failures.append(f"SET {sid}: site_length missing")

            # Width
            if re.search(r"(?:ft|feet).{0,10}wide|wide.{0,10}(?:ft|feet)|width of|width", req, re.I) and re.search(
                r"academic|core|exactly|needs? to be|has to|must be|maintain", req, re.I
            ):
                if not (
                    any(d.get("lever") == "width" for d in parsed.dimensions)
                    or _has_lever(briefing, "width")
                ):
                    failures.append(f"SET {sid}: exact/dept width missing")

            # Ratio
            if re.search(r"\d+\s*:\s*\d+|proportion|ratio", pref + " " + lim, re.I):
                if not (
                    parsed.length_over_width
                    or parsed.constraints.get("preferred_ratio")
                    or _has_lever(briefing, "ratio")
                ):
                    failures.append(f"SET {sid}: ratio missing")

            # Open space
            if re.search(r"open space|built coverage", req + " " + lim + " " + pref, re.I):
                if not (
                    parsed.constraints.get("min_open_space_pct")
                    or parsed.constraints.get("preferred_open_space_pct")
                    or _has_lever(briefing, "open_space")
                ):
                    failures.append(f"SET {sid}: open_space missing")

            # GFA / footprint
            if re.search(r"gsf|gfa|total .{0,20}square", lim, re.I):
                if not (
                    parsed.constraints.get("max_gfa_sf")
                    or _has_lever(briefing, "gfa")
                ):
                    failures.append(f"SET {sid}: gfa missing")

            if re.search(r"footprint|per floor", lim, re.I) and re.search(
                r"sqft|square|sf\b", lim, re.I
            ):
                if not (
                    parsed.constraints.get("max_footprint_sf")
                    or _has_lever(briefing, "footprint")
                ):
                    failures.append(f"SET {sid}: footprint missing")

        self.assertEqual(
            failures,
            [],
            "modality training gaps:\n" + "\n".join(failures),
        )

    def test_dynamic_numbers_round_trip(self) -> None:
        """Spot-check that extracted numbers match values present in the clause text."""
        s = next(x for x in self.sets if x["id"] == 1)
        parsed, briefing = self._parse_set(s)
        n = _first_num(s["requirement"])
        self.assertIsNotNone(n)
        self.assertEqual(parsed.mass_count, int(n))
        stories = _first_num(s["limitation"])
        self.assertEqual(parsed.max_stories, int(stories))
        self.assertTrue(_has_lever(briefing, "mass_count", {"requirement"}))
        self.assertTrue(
            _has_lever(briefing, "max_stories", {"limitation", "requirement"})
        )


if __name__ == "__main__":
    unittest.main()
