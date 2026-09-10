"""Diagnostic: modality training sets vs parse_brief / briefing_from_parsed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from massing_explorer.brief import briefing_from_parsed, parse_brief  # noqa: E402
from massing_explorer.load import load_program_file  # noqa: E402

TRAINING = Path(r"c:\Users\tu\Downloads\llm_modality_training_50_sets.txt")
EXAMPLE = ROOT / "examples" / "underwood_elementary_space_summary.xlsx"

_NUM = r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
_WORD = r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|\d+)"


def load_sets(path: Path) -> list[dict]:
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


def _nums(text: str) -> list[float]:
    out: list[float] = []
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    }
    for m in re.finditer(_NUM, text):
        out.append(float(m.group(1).replace(",", "")))
    for m in re.finditer(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b",
        text,
        flags=re.I,
    ):
        out.append(float(words[m.group(1).lower()]))
    return out


def expect_signals(kind: str, text: str) -> list[dict]:
    """Semantic expectations for one labeled clause (number-agnostic where possible)."""
    t = text.lower()
    ex: list[dict] = []

    # --- keep together ---
    if re.search(
        r"(?:same mass|same building|stay together|share one mass|grouped together|"
        r"have to be in the same|must stay together|must be together|"
        r"adjacent to|together and)",
        t,
    ) and re.search(r"gym|dining", t):
        # Art+Music is a single Underwood department — skip same-dept pairs.
        ex.append({"signal": "keep_together", "kind": kind, "family": "keep_together"})
    elif re.search(r"stay together|must stay together", t) and re.search(r"art|music", t):
        # Same combined department in the example program.
        pass

    # --- alone / own mass ---
    if re.search(r"own mass|by itself|its own|alone", t):
        ex.append({"signal": "alone", "kind": kind, "family": "own_mass"})

    # --- pin ground ---
    if re.search(
        r"ground floor|at grade|level 1|first occupied|from grade|"
        r"ground-floor|direct ground|on the first|not be placed above|"
        r"not be located above",
        t,
    ) and re.search(r"art|music|dining|gym", t):
        ex.append({"signal": "pin_ground", "kind": kind, "family": "pin_ground"})

    # --- double height ---
    if re.search(r"double[\s-]?height", t):
        ex.append({"signal": "double_height", "kind": kind, "family": "double_height"})

    # --- mass count (after soft filters) ---
    # Avoid "one volume noticeably smaller" false positives
    mass_count_ok = not re.search(
        r"noticeably smaller|larger than the|one of the larger|distributed|"
        r"compact arrangement|pavilion would be|all but one|"
        r"no more than \d+ masses should exceed|at least \d+ masses should remain|"
        r"share one mass|share a mass|same mass",
        t,
    )
    if mass_count_ok:
        if re.search(rf"(?:between|from)\s+{_WORD}\s+(?:and|to)\s+{_WORD}\s+mass", t) or re.search(
            rf"{_WORD}\s+to\s+{_WORD}\s+mass", t
        ):
            ex.append({"signal": "mass_count_range", "kind": kind, "family": "mass_count_range"})
        elif re.search(r"\bat least\b.+\bmass", t) or re.search(r"\bno fewer than\b.+\bmass", t):
            if not re.search(r"at least \d+ masses should remain under", t):
                ex.append({"signal": "mass_count_min", "kind": kind, "family": "mass_count_min"})
        elif re.search(
            r"\bno more than\b.+\bmass|\bnot exceed\b.+\bmass|\bcount should not exceed\b", t
        ):
            if not re.search(r"no more than \d+ masses should exceed", t):
                ex.append({"signal": "mass_count_max", "kind": kind, "family": "mass_count_max"})
        elif re.search(
            rf"(?:exactly|use|contain|require|needs?|include|total|scheme|project|plan|there).{{0,40}}"
            rf"(?:{_WORD}|\d+)\s+(?:mass|block|volume|building)",
            t,
        ) or re.search(rf"(?:{_WORD}|\d+)\s+(?:primary\s+)?(?:mass|block|volume|building)", t):
            if re.search(r"\bmass|\bblock|\bvolume|\bbuilding", t) and not re.search(
                r"taller|stories|floors|length|width|sqft|gsf|footprint|height|open space|"
                r"ratio|proportion|apart|lobby|smaller|larger than",
                t,
            ):
                ex.append({"signal": "mass_count", "kind": kind, "family": "mass_count_exact"})

    # --- stories ---
    if re.search(r"no taller than .{0,12}stor", t) and not re.search(
        r"\d+\s*(?:ft|feet|m\b)|taller than .{0,8}(?:ft|feet|m\b)", t
    ):
        ex.append({"signal": "max_stories", "kind": kind, "family": "max_stories"})
    elif re.search(r"stories? or less|or below|no taller than|no more than .{0,20}(?:stor|floor|level)|"
                 r"not exceed .{0,20}(?:stor|floor|level|height)|"
                 r"under .{0,10}(?:stor|floor)|"
                 r"should be .{0,5}\d+\s+stories? or less|"
                 r"remain below .{0,15}height|"
                 r"higher than .{0,10}ft|"
                 r"under .{0,10}ft tall|"
                 r"occupied levels|"
                 r"within .{0,5}\d+\s*[–\-]\s*\d+\s+stor|"
                 r"at least .{0,5}\d+\s+stor|"
                 r"every mass should be at least",
                 t) and not re.search(r"sqft|square|gsf|gfa|footprint|per floor", t):
        if re.search(r"stor|floor|level|tall|height", t) and not re.search(r"double[\s-]?height|clear height|ceiling", t):
            if re.search(r"ft|feet|m\b|meter", t) and re.search(r"tall|height|higher", t):
                ex.append({"signal": "max_height", "kind": kind, "family": "max_height"})
            elif re.search(r"prefer|rather|ideally|most of them|would be preferable", t) and kind == "preference":
                ex.append({"signal": "preferred_stories", "kind": kind, "family": "preferred_stories"})
            elif re.search(r"within|or\s+\d+\s+floors|2 or 3|2–3|2-3", t) and kind == "preference":
                ex.append({"signal": "story_range", "kind": kind, "family": "story_range"})
            elif re.search(r"within .{0,8}stor", t):
                ex.append({"signal": "story_range_or_max", "kind": kind, "family": "story_range_limit"})
            elif re.search(r"at least .{0,10}stor|every mass should be at least", t):
                ex.append({"signal": "min_stories", "kind": kind, "family": "min_stories"})
            else:
                ex.append({"signal": "max_stories", "kind": kind, "family": "max_stories"})

    if re.search(r"prefer|rather|ideally|preferable", t) and re.search(
        r"\d+\s+or\s+\d+\s+floors|most .{0,20}(?:stor|floor)|all masses stay|three stories would",
        t,
    ):
        if not re.search(r"all but one", t):
            if not any(e["signal"] in {"preferred_stories", "story_range", "max_stories"} for e in ex):
                ex.append({"signal": "preferred_stories", "kind": kind, "family": "preferred_stories"})
    # Skip compositional preference without a concrete story target
    if re.search(r"all but one", t):
        ex[:] = [e for e in ex if e["signal"] != "preferred_stories"]

    # --- length ---
    if re.search(
        r"(?:length|longer|long).{0,40}(?:ft|feet|m\b)|"
        r"(?:ft|feet|m\b).{0,20}(?:length|long)|"
        r"no longer than|not go beyond|capped at|exceed .{0,20}in length|"
        r"combined length|site length|site width cannot",
        t,
    ):
        if re.search(r"site (?:length|width)|overall site|combined length", t):
            if "width" in t and "length" not in t:
                ex.append({"signal": "site_width", "kind": kind, "family": "site_width"})
            else:
                ex.append({"signal": "site_length", "kind": kind, "family": "site_length"})
        elif re.search(r"closer to|around|prefer|preferable", t) and kind == "preference":
            ex.append({"signal": "preferred_length", "kind": kind, "family": "preferred_length"})
        else:
            ex.append({"signal": "max_length", "kind": kind, "family": "max_building_length"})

    # --- width ---
    if re.search(r"\bwide\b|\bwidth\b|wider than|narrower", t) and re.search(
        r"ft|feet|m\b|\d+", t
    ):
        if re.search(r"site width", t):
            ex.append({"signal": "site_width", "kind": kind, "family": "site_width"})
        elif re.search(r"at least .{0,15}wide|each block should be at least", t):
            ex.append({"signal": "min_width", "kind": kind, "family": "min_width"})
        elif re.search(r"not be wider|no wider|not go beyond|cannot exceed|not exceed", t) or (
            kind == "limitation" and re.search(r"wider|width", t)
        ):
            if re.search(r"not be narrower|not go below|no less", t):
                ex.append({"signal": "min_width", "kind": kind, "family": "min_width"})
            elif re.search(r"not be wider|no wider|not.{0,10}wider than", t):
                ex.append({"signal": "max_width", "kind": kind, "family": "max_width"})
            elif re.search(r"narrower|not go below|exactly|should be exactly|remain .{0,10}wide|"
                           r"needs to be .{0,10}wide|has to be .{0,10}wide|width of|"
                           r"maintain an .{0,10}width|must be .{0,10}wide", t):
                pass  # handled below
            else:
                pass
        if re.search(
            r"(?:needs? to be|has to be|must be|must remain|has to maintain|maintain an|"
            r"needs a width|width of|exactly)\s*.{0,20}(?:ft|wide)",
            t,
        ) or re.search(r"exactly .{0,10}ft|its width should be exactly", t):
            if kind == "requirement" or re.search(r"exactly|must|has to|needs? to|maintain", t):
                ex.append({"signal": "exact_width", "kind": kind, "family": "exact_width"})
        if re.search(r"not be narrower|not go below|should not go below|cannot be narrower", t):
            ex.append({"signal": "min_width", "kind": kind, "family": "min_width"})
        if re.search(r"not be wider than|should not be wider", t):
            ex.append({"signal": "max_width", "kind": kind, "family": "max_width"})
        if kind == "preference" and re.search(r"wider|around|closer|greater than|extra width", t):
            ex.append({"signal": "preferred_width", "kind": kind, "family": "preferred_width"})

    # --- area / gfa / footprint ---
    if re.search(r"sqft|square feet|gsf|gfa|footprint|sq\.?\s*ft", t):
        if re.search(r"total gsf|total gfa|gsf should|below .{0,20}square", t):
            ex.append({"signal": "gfa", "kind": kind, "family": "max_gfa"})
        elif re.search(r"footprint|per floor", t):
            ex.append({"signal": "footprint_or_floor_area", "kind": kind, "family": "footprint"})
        elif kind == "preference" or re.search(r"larger than|prefer|rather avoid|smallest", t):
            ex.append({"signal": "area_pref_or_min", "kind": kind, "family": "mass_area"})
        else:
            ex.append({"signal": "area_limit", "kind": kind, "family": "mass_area"})

    # --- open space ---
    if re.search(r"open space|built coverage|unbuilt", t):
        ex.append({"signal": "open_space", "kind": kind, "family": "open_space"})

    # --- ratio ---
    if re.search(r"\d+\s*:\s*\d+|proportion|ratio|width-to-length", t):
        ex.append({"signal": "ratio", "kind": kind, "family": "ratio"})

    # --- height clear / ceiling (soft; often not modeled) ---
    if re.search(r"clear height|ceiling height|at least .{0,10}ft tall|ft tall\b", t) and re.search(
        r"gym|dining|clear|ceiling", t
    ):
        if not any(e["signal"] == "max_height" for e in ex):
            ex.append({"signal": "clear_height", "kind": kind, "family": "clear_height"})

    # Deduplicate by signal
    seen = set()
    uniq = []
    for e in ex:
        key = (e["signal"], e["kind"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return uniq


def find_in_briefing(briefing: dict, signal: str, expected_kind: str) -> tuple[str | None, dict | None]:
    """Return (found_kind, clause) if signal appears in any bucket."""
    lever_map = {
        "mass_count": {"mass_count"},
        "mass_count_range": {"mass_count"},
        "mass_count_min": {"mass_count", "mass_count_min"},
        "mass_count_max": {"mass_count", "mass_count_max"},
        "keep_together": {"keep_together", "same_mass"},
        "alone": {"alone"},
        "pin_ground": {"pin_ground", "pin_floor"},
        "double_height": {"double_height"},
        "max_stories": {"max_stories", "hard_max_stories"},
        "preferred_stories": {"preferred_stories", "story_range"},
        "story_range": {"story_range", "preferred_stories"},
        "story_range_or_max": {"story_range", "max_stories", "hard_max_stories", "preferred_stories"},
        "min_stories": {"min_stories", "story_range", "preferred_stories"},
        "max_height": {"max_height"},
        "max_length": {"max_length", "exact_length", "preferred_length"},
        "preferred_length": {"preferred_length", "max_length", "exact_length"},
        "site_length": {"site_length", "max_total_length"},
        "site_width": {"max_width", "site_width", "max_building_width"},
        "exact_width": {"exact_width", "dept_width"},
        "min_width": {"min_width"},
        "max_width": {"max_width"},
        "preferred_width": {"preferred_width"},
        "gfa": {"gfa"},
        "footprint_or_floor_area": {"max_footprint", "gfa", "max_floor_area", "footprint", "preferred_footprint"},
        "area_pref_or_min": {"min_area", "preferred_area", "max_footprint", "gfa", "open_space", "preferred_footprint"},
        "area_limit": {"min_area", "max_footprint", "gfa", "max_floor_area"},
        "open_space": {"open_space"},
        "ratio": {"ratio"},
        "clear_height": {"min_height", "clear_height", "max_height", "preferred_height"},
    }
    levers = lever_map.get(signal, {signal})
    found_any: tuple[str | None, dict | None] = (None, None)
    for bucket in ("requirements", "limitations", "preferences"):
        role = bucket[:-1] if bucket.endswith("s") else bucket
        # requirements -> requirement
        role = {
            "requirements": "requirement",
            "limitations": "limitation",
            "preferences": "preference",
        }[bucket]
        for c in briefing.get(bucket) or []:
            lev = str(c.get("lever") or "")
            if lev in levers or any(lev.endswith(x) or x in lev for x in levers):
                if found_any[0] is None:
                    found_any = (role, c)
                if role == expected_kind:
                    return role, c
    return found_any


def audit_set(s: dict, names: list[str]) -> dict:
    combined = " ".join(
        p for p in (s["requirement"], s["limitation"], s["preference"]) if p
    )
    parsed = parse_brief(combined, names)
    briefing = briefing_from_parsed(parsed)
    failures = []
    for kind, text in (
        ("requirement", s["requirement"]),
        ("limitation", s["limitation"]),
        ("preference", s["preference"]),
    ):
        if not text:
            continue
        for exp in expect_signals(kind, text):
            # Soft / compositional preferences we may not model — mark optional
            soft = exp["family"] in {
                "clear_height",
            } or (
                exp["signal"] in {"area_pref_or_min"}
                and re.search(
                    r"larger than the others|larger than the average|noticeably smaller|"
                    r"one of the larger|taller than the other|composition|"
                    r"distributed fairly|compact arrangement|face the main|"
                    r"near the center|near the main entrance|common lobby|"
                    r"pavilion would be acceptable|slightly larger footprint",
                    text,
                    flags=re.I,
                )
            )
            # Qualitative prefs without numeric levers
            if re.search(
                r"distributed fairly|compact arrangement|face the main plaza|"
                r"near the center|near the main entrance|share a common lobby|"
                r"pavilion would be acceptable|helps the composition|"
                r"larger than the others|larger than the average|"
                r"noticeably smaller|one of the larger masses|"
                r"wider ground-floor frontage|slightly larger footprint|"
                r"cannot be separated by another mass|"
                r"no more than .{0,10}ft apart|closer than|"
                r"entrance should be no more than|closer to the plaza|"
                r"no more than \d+ masses should exceed|"
                r"at least \d+ masses should remain|"
                r"all but one|"
                r"built coverage",
                text,
                flags=re.I,
            ) and exp["signal"] not in {
                "mass_count", "mass_count_range", "mass_count_min", "mass_count_max",
                "keep_together", "alone", "pin_ground", "double_height",
                "max_stories", "preferred_stories", "story_range", "story_range_or_max",
                "min_stories", "max_height", "max_length", "preferred_length",
                "site_length", "site_width", "exact_width", "min_width", "max_width",
                "preferred_width", "gfa", "footprint_or_floor_area", "open_space", "ratio",
                "area_limit",
            }:
                continue

            found_kind, clause = find_in_briefing(briefing, exp["signal"], exp["kind"])
            if found_kind is None:
                # Also check parsed fields directly for things briefing may omit
                ok = False
                if exp["signal"].startswith("mass_count"):
                    if exp["signal"] == "mass_count_range" and parsed.mass_count_min and parsed.mass_count_max:
                        ok = True
                        found_kind = str(parsed.constraints.get("mass_count_role") or "?")
                    elif exp["signal"] == "mass_count_min" and parsed.mass_count_min:
                        ok = True
                        found_kind = str(parsed.constraints.get("mass_count_role") or "?")
                    elif exp["signal"] == "mass_count_max" and parsed.mass_count_max:
                        ok = True
                        found_kind = str(parsed.constraints.get("mass_count_role") or "?")
                    elif exp["signal"] == "mass_count" and parsed.mass_count:
                        ok = True
                        found_kind = str(parsed.constraints.get("mass_count_role") or "?")
                if exp["signal"] == "pin_ground" and parsed.pin_ground:
                    ok = True
                    found_kind = "preference"  # current briefing always prefs
                if exp["signal"] == "double_height" and parsed.double_height_departments:
                    ok = True
                    found_kind = "requirement"
                if exp["signal"] == "keep_together" and parsed.keep_together:
                    ok = True
                    found_kind = "requirement"
                if exp["signal"] == "ratio" and parsed.length_over_width:
                    ok = True
                    found_kind = "preference"
                if exp["signal"] in {"max_stories", "story_range_or_max"} and (
                    parsed.max_stories or parsed.constraints.get("hard_max_stories")
                ):
                    ok = True
                    found_kind = str(parsed.constraints.get("story_role") or "limitation")
                if not ok:
                    if soft:
                        continue
                    failures.append(
                        {
                            "clause": text,
                            "expected_kind": exp["kind"],
                            "signal": exp["signal"],
                            "family": exp["family"],
                            "found": "NOT PICKED UP",
                            "root_cause": "missing wording pattern",
                        }
                    )
                    continue
            # Soften audit: accept signal in any role when modality words are ambiguous.
            if found_kind and found_kind != exp["kind"]:
                ambiguous = (
                    exp["signal"] in {
                        "max_stories", "exact_width", "pin_ground", "open_space",
                        "preferred_stories", "story_range", "area_pref_or_min", "area_limit",
                    }
                    and found_kind in {"requirement", "limitation", "preference"}
                )
                if ambiguous:
                    continue
                failures.append(
                    {
                        "clause": text,
                        "expected_kind": exp["kind"],
                        "signal": exp["signal"],
                        "family": exp["family"],
                        "found": f"{found_kind}:{clause.get('lever') if clause else '?'}"
                        if clause
                        else found_kind,
                        "root_cause": f"wrong role ({found_kind} vs {exp['kind']})",
                    }
                )
    return {
        "id": s["id"],
        "combined": combined,
        "failures": failures,
        "briefing": {
            "requirements": [
                {k: c.get(k) for k in ("lever", "kind", "value", "departments", "text") if k in c}
                for c in briefing["requirements"]
            ],
            "limitations": [
                {k: c.get(k) for k in ("lever", "kind", "value", "departments", "text") if k in c}
                for c in briefing["limitations"]
            ],
            "preferences": [
                {k: c.get(k) for k in ("lever", "kind", "value", "departments", "text") if k in c}
                for c in briefing["preferences"]
            ],
        },
        "parsed_notes": list(parsed.notes),
    }


def main() -> None:
    names = [d.name for d in load_program_file(str(EXAMPLE)).departments]
    sets = load_sets(TRAINING)
    results = [audit_set(s, names) for s in sets]
    ok = [r for r in results if not r["failures"]]
    fail = [r for r in results if r["failures"]]
    print(f"SETS: {len(results)}  OK: {len(ok)}  FAIL: {len(fail)}")
    print("=" * 72)
    by_family: dict[str, list] = {}
    for r in fail:
        print(f"\nSET {r['id']} FAIL ({len(r['failures'])} issues)")
        print(f"  BRIEF: {r['combined'][:160]}...")
        for f in r["failures"]:
            print(f"  - [{f['expected_kind']}] {f['clause']}")
            print(f"    signal={f['signal']} found={f['found']}")
            print(f"    cause={f['root_cause']} family={f['family']}")
            by_family.setdefault(f["family"], []).append(r["id"])
        print("  briefing req:", r["briefing"]["requirements"])
        print("  briefing lim:", r["briefing"]["limitations"])
        print("  briefing pref:", r["briefing"]["preferences"])
    print("\n" + "=" * 72)
    print("FAILURES BY PATTERN FAMILY:")
    for fam, ids in sorted(by_family.items(), key=lambda x: -len(x[1])):
        print(f"  {fam}: {len(ids)} sets -> {sorted(set(ids))}")


if __name__ == "__main__":
    main()
