"""Intent-interpreter smoke score for number/unit/variation briefs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from massing_explorer.brief import assign_open_departments, parse_brief
from massing_explorer.load import load_program_file

SMOKE = Path(r"c:\Users\tu\Downloads\massing_llm_smoke_test_units_numbers_variations.txt")
CONFIG = ROOT / "config" / "project.example.yaml"
PROGRAM = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"
M = 3.280839895
SQM = 10.76391041671


def main() -> int:
    names = [d.name for d in load_program_file(PROGRAM, config_path=str(CONFIG)).departments]
    text = SMOKE.read_text(encoding="utf-8")
    prompts = [
        (int(m.group(1)), m.group(2).strip())
        for m in re.finditer(r"^(\d+)\.\s+(.*)$", text, flags=re.M)
    ]
    expect = {
        1: dict(mass_count=4, keep=("HEALTH", "DINING"), pin="ART", site=400, pref="low_rise"),
        2: dict(mass_count=3, height=60),
        3: dict(mass_count=5, width_m=90, stories=3),
        4: dict(mass_count=6),
        5: dict(mass_count=4),
        6: dict(mass_min=2, mass_max=3, stories=6, pref="low_rise"),
        7: dict(mass_count=7, height_m=14),
        8: dict(mass_count=5, depth=420),
        9: dict(stories=5),
        10: dict(mass_count=4, site_m=115),
        11: dict(mass_count=3, gfa=250000),
        12: dict(mass_min=5, mass_max=8, blen_m=55),
        13: dict(mass_count=4, height=50),
        14: dict(mass_count=5),
        15: dict(mass_count=6, height_m=21, site_m=122),
        16: dict(mass_min=8, mass_max=10),
        17: dict(mass_count=4, blen=220),
        18: dict(mass_min=4, mass_max=5, stories=5),
        19: dict(mass_count=4, gfa_sqm=28000, height=65),
        20: dict(mass_min=4, mass_max=6, gfa_sqm=32000, height=55),
        21: dict(mass_count=4, gfa=180000, site=400, keep=("HEALTH", "DINING"), pin="ART"),
        22: dict(mass_count=4, gfa_sqm=16500, height_m=18),
        23: dict(mass_count=5, gfa=200000),
        24: dict(mass_count=3, gfa_sqm=22000),
        25: dict(mass_count=6, site_m=120, blen=65),
    }

    ok = total = 0
    for i, prompt in prompts:
        parsed = parse_brief(prompt, names)
        assign_open_departments(parsed)
        e = expect[i]
        misses: list[str] = []

        def check(name: str, cond: bool) -> None:
            nonlocal ok, total
            total += 1
            if cond:
                ok += 1
            else:
                misses.append(name)

        if "mass_count" in e:
            check("mass_count", parsed.mass_count == e["mass_count"])
        if "mass_min" in e:
            check(
                "mass_min",
                parsed.mass_count_min == e["mass_min"]
                or parsed.mass_count == e["mass_min"],
            )
        if "mass_max" in e:
            check(
                "mass_max",
                parsed.mass_count_max == e["mass_max"]
                or parsed.mass_count == e["mass_max"],
            )
        if "keep" in e:
            a_tok, b_tok = e["keep"]
            found = False
            for a, b in parsed.keep_together:
                blob = f"{a} {b}".upper()
                if a_tok in blob and b_tok in blob:
                    found = True
            for _, ds in parsed.named_masses:
                blob = " ".join(ds).upper()
                if a_tok in blob and b_tok in blob:
                    found = True
            check("keep", found)
        if "pin" in e:
            check("pin", any(e["pin"] in x for x in parsed.pin_ground))
        if "site" in e:
            check(
                "site",
                abs((parsed.constraints.get("max_total_length_ft") or 0) - e["site"]) < 1,
            )
        if "site_m" in e:
            check(
                "site_m",
                abs((parsed.constraints.get("max_total_length_ft") or 0) - e["site_m"] * M) < 1,
            )
        if "width_m" in e:
            check(
                "width_m",
                abs((parsed.constraints.get("max_building_width_ft") or 0) - e["width_m"] * M) < 1,
            )
        if "pref" in e:
            check("pref", parsed.preference == e["pref"])
        if "height" in e:
            check(
                "height",
                abs((parsed.constraints.get("max_height_ft") or 0) - e["height"]) < 1,
            )
        if "height_m" in e:
            check(
                "height_m",
                abs((parsed.constraints.get("max_height_ft") or 0) - e["height_m"] * M) < 1,
            )
        if "stories" in e:
            check("stories", parsed.max_stories == e["stories"])
        if "depth" in e:
            got = parsed.constraints.get("max_total_length_ft") or parsed.constraints.get(
                "max_building_length_ft"
            ) or 0
            check("depth", abs(got - e["depth"]) < 1)
        if "blen" in e:
            check(
                "blen",
                abs((parsed.constraints.get("max_building_length_ft") or 0) - e["blen"]) < 1,
            )
        if "blen_m" in e:
            check(
                "blen_m",
                abs((parsed.constraints.get("max_building_length_ft") or 0) - e["blen_m"] * M) < 1,
            )
        if "gfa" in e:
            g = parsed.constraints.get("max_gfa_sf") or parsed.constraints.get("target_gfa_sf") or 0
            check("gfa", abs(g - e["gfa"]) < 2)
        if "gfa_sqm" in e:
            g = parsed.constraints.get("max_gfa_sf") or parsed.constraints.get("target_gfa_sf") or 0
            check("gfa_sqm", abs(g - e["gfa_sqm"] * SQM) < 5)

        if misses:
            print(
                f"{i}: MISS {misses} | mass={parsed.mass_count} "
                f"range={parsed.mass_count_min}-{parsed.mass_count_max} "
                f"stories={parsed.max_stories} cons={parsed.constraints} "
                f"keep={parsed.keep_together} pin={parsed.pin_ground}"
            )

    print(f"SCORE {ok}/{total} = {100 * ok / max(total, 1):.0f}%")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
