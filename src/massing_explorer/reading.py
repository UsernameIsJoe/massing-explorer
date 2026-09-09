"""
Design reading: the model's turn before the engine commits a brief.

The parser still owns every number. The model may only choose levers the
engine already has — stay-together, pair on the stated frontage, pin to
ground, search preference — and later pick one verified scheme or one
repair. Unknown departments and any invented dimensions are dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .group import match_department

def _resolve(name: str, department_names: list[str]) -> str | None:
    hit = match_department(name, department_names)
    if hit:
        return hit
    token = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    aliases = {
        "gym": ("health", "physical"),
        "pe": ("health", "physical"),
        "cafeteria": ("dining", "food"),
        "classroom": ("academic",),
        "classrooms": ("academic",),
    }
    keys = aliases.get(token)
    if not keys:
        return None
    for dept in department_names:
        low = dept.lower()
        if any(k in low for k in keys):
            return dept
    return None


PREFERENCES = ("low_rise", "compact", "balanced")
KINDS = ("requirement", "limitation", "preference")
REQUIREMENT_LEVERS = {
    "same_mass",
    "alone",
    "mass_count",
    "double_height",
    "keep_together",
    "keep_apart",
    "exact_length",
    "exact_width",
    "exact_depth",
    "dept_width",
}
LIMITATION_LEVERS = {
    "max_length",
    "max_width",
    "max_stories",
    "max_height",
    "site_length",
    "max_total_length",
    "min_width",
}
PREFERENCE_LEVERS = {
    "pin_ground",
    "pin_floor",
    "ratio",
    "loading",
    "low_rise",
    "compact",
    "spread",
    "preferred_width",
}
_LIMIT_TEXT = re.compile(
    r"\b(?:under|below|shorter than|less than|no more than|not more than|"
    r"at most|at maximum|cannot exceed|can't exceed|not exceed|not over|"
    r"no longer than|maximum|max(?:imum)?)\b",
    flags=re.I,
)
_REQUIRE_TEXT = re.compile(
    r"\b(?:must|shall|have to|need to|required|same mass|in the same|"
    r"by itself|is one mass|one mass|double[\s-]?height)\b",
    flags=re.I,
)
_PREFER_TEXT = re.compile(
    r"\b(?:prefer|preferably|ideally|if possible|would like|try to|"
    r"on the ground floor|ground floor|ratio)\b",
    flags=re.I,
)


@dataclass
class DesignReading:
    keep_together: list[tuple[str, str]] = field(default_factory=list)
    pair_departments: list[str] = field(default_factory=list)
    pin_ground: list[str] = field(default_factory=list)
    # Explicit wings the user named, in order. Leftover departments stay in families.
    masses: list[tuple[str, list[str]]] = field(default_factory=list)
    pair_length_ft: float | None = None
    site_length_ft: float | None = None
    max_width_ft: float | None = None
    # How a classroom bar should work. A length cap is not a width.
    loading: str | None = None
    corridor_ft: float | None = None
    classroom_depth_ft: float | None = None
    preferred_width_ft: float | None = None
    max_stories: int | None = None
    preference: str | None = None
    reasons: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    # Each user clause, already sorted into the role the pipeline must honor.
    clauses: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keep_together": [list(p) for p in self.keep_together],
            "pair_departments": list(self.pair_departments),
            "pin_ground": list(self.pin_ground),
            "masses": [
                {"name": name, "departments": list(depts)}
                for name, depts in self.masses
            ],
            "pair_length_ft": self.pair_length_ft,
            "site_length_ft": self.site_length_ft,
            "max_width_ft": self.max_width_ft,
            "loading": self.loading,
            "corridor_ft": self.corridor_ft,
            "classroom_depth_ft": self.classroom_depth_ft,
            "preferred_width_ft": self.preferred_width_ft,
            "max_stories": self.max_stories,
            "preference": self.preference,
            "reasons": list(self.reasons),
            "dropped": list(self.dropped),
            "clauses": list(self.clauses),
            "requirements": [c for c in self.clauses if c.get("kind") == "requirement"],
            "limitations": [c for c in self.clauses if c.get("kind") == "limitation"],
            "preferences": [c for c in self.clauses if c.get("kind") == "preference"],
        }

    @property
    def empty(self) -> bool:
        return not (
            self.keep_together
            or self.pair_departments
            or self.pin_ground
            or self.masses
            or self.pair_length_ft
            or self.site_length_ft
            or self.max_width_ft
            or self.loading
            or self.corridor_ft
            or self.preferred_width_ft
            or self.max_stories
            or self.preference
            or self.clauses
        )


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    if fence:
        raw = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _as_name_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def validate_reading(data: dict[str, Any] | None, department_names: list[str]) -> DesignReading:
    """Keep only legal choices. Drop widths, GSF, and unknown departments."""
    reading = DesignReading()
    if not data:
        return reading

    for key in data:
        if key in {
            "width_ft",
            "length_ft",
            "gsf",
            "area_sf",
            "dimensions",
            "story_count",
            "stories",
        }:
            reading.dropped.append(f"ignored {key}")

    seen_pairs: set[tuple[str, str]] = set()
    for item in data.get("keep_together") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            reading.dropped.append("keep_together entry is not a pair")
            continue
        a = _resolve(str(item[0]), department_names)
        b = _resolve(str(item[1]), department_names)
        if not a or not b or a == b:
            reading.dropped.append(f"keep_together {item}")
            continue
        key = tuple(sorted((a, b)))
        if key not in seen_pairs:
            seen_pairs.add(key)
            reading.keep_together.append((a, b))

    seen_depts: set[str] = set()
    for name in _as_name_list(data.get("pair_on_frontage") or data.get("pair_departments")):
        hit = _resolve(name, department_names)
        if not hit:
            reading.dropped.append(f"pair {name}")
            continue
        if hit not in seen_depts:
            seen_depts.add(hit)
            reading.pair_departments.append(hit)
    if len(reading.pair_departments) < 2:
        if reading.pair_departments:
            reading.dropped.append("pair needs two departments")
        reading.pair_departments = []

    for name in _as_name_list(data.get("pin_ground")):
        hit = _resolve(name, department_names)
        if not hit:
            reading.dropped.append(f"pin {name}")
            continue
        if hit not in reading.pin_ground:
            reading.pin_ground.append(hit)

    claimed: set[str] = set()
    used_ids: set[str] = set()
    for i, item in enumerate(data.get("masses") or []):
        if not isinstance(item, dict):
            reading.dropped.append("mass entry is not an object")
            continue
        label = str(item.get("name") or f"Mass {i + 1}").strip() or f"Mass {i + 1}"
        depts: list[str] = []
        for raw in _as_name_list(item.get("departments")):
            hit = _resolve(raw, department_names)
            if not hit:
                reading.dropped.append(f"mass department {raw}")
                continue
            if hit in claimed or hit in depts:
                reading.dropped.append(f"department already placed: {hit}")
                continue
            depts.append(hit)
        if not depts:
            reading.dropped.append(f"empty mass {label}")
            continue
        claimed.update(depts)
        mass_id = _slug(label, i + 1)
        base = mass_id
        n = 2
        while mass_id in used_ids:
            mass_id = f"{base}_{n}"
            n += 1
        used_ids.add(mass_id)
        reading.masses.append((label, depts))

    reading.pair_length_ft = _positive_number(
        data.get("pair_length_ft") or data.get("together_length_ft")
    )
    reading.site_length_ft = _positive_number(
        data.get("site_length_ft") or data.get("max_total_length_ft")
    )
    reading.max_width_ft = _positive_number(data.get("max_width_ft"))
    reading.preferred_width_ft = _positive_number(
        data.get("preferred_width_ft") or data.get("target_width_ft")
    )
    reading.corridor_ft = _positive_number(data.get("corridor_ft"))
    reading.classroom_depth_ft = _positive_number(data.get("classroom_depth_ft"))
    loading = data.get("loading")
    if isinstance(loading, str) and loading.strip():
        token = loading.strip().lower().replace("-", "_").replace(" ", "_")
        if token in {"single", "single_loaded"}:
            reading.loading = "single"
        elif token in {"double", "double_loaded"}:
            reading.loading = "double"
        else:
            reading.dropped.append(f"loading {loading}")
    stories = _positive_number(data.get("max_stories"))
    if stories is not None:
        reading.max_stories = max(1, int(stories))

    pref = data.get("preference")
    if isinstance(pref, str) and pref.strip():
        pref_l = pref.strip().lower().replace("-", "_").replace(" ", "_")
        if pref_l in PREFERENCES:
            reading.preference = pref_l
        else:
            reading.dropped.append(f"preference {pref}")

    reasons = data.get("reasons") or []
    if isinstance(reasons, list):
        reading.reasons = [str(r) for r in reasons if str(r).strip()][:6]
    reading.clauses = _read_clauses(data.get("clauses"), department_names, reading)
    return reading


def _stated_number(value: Any, unit: str | None = None) -> float | None:
    number = _positive_number(value)
    if number is None:
        return None
    if unit and str(unit).lower().startswith("m"):
        return number * 3.280839895
    return number


def _force_kind(kind: str, text: str, lever: str) -> str:
    """Modality words decide the role. Digits / spelled numbers are values only."""
    # Strip values so "prefer 3 stories" / "four masses" cannot flip the role by number.
    from massing_explorer.brief import _strip_values_for_role

    cues = _strip_values_for_role(text or "")
    if lever in LIMITATION_LEVERS or _LIMIT_TEXT.search(cues):
        if not re.search(r"\b(?:must be exactly|exactly|shall be)\b", cues, flags=re.I):
            return "limitation"
    if lever in PREFERENCE_LEVERS or (
        _PREFER_TEXT.search(cues) and not _REQUIRE_TEXT.search(cues)
    ):
        return "preference"
    if lever in REQUIREMENT_LEVERS or _REQUIRE_TEXT.search(cues):
        return "requirement"
    if kind in KINDS:
        return kind
    return "preference"


def _read_clauses(
    raw: Any,
    department_names: list[str],
    reading: DesignReading,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            reading.dropped.append("clause is not an object")
            continue
        text = str(item.get("text") or "").strip()
        lever = str(item.get("lever") or "").strip().lower().replace(" ", "_").replace("-", "_")
        kind = _force_kind(str(item.get("kind") or "").strip().lower(), text, lever)
        known = REQUIREMENT_LEVERS | LIMITATION_LEVERS | PREFERENCE_LEVERS
        if lever not in known:
            reading.dropped.append(f"unknown lever {lever or item.get('lever')}")
            continue
        if kind == "requirement" and lever in LIMITATION_LEVERS:
            kind = "limitation"
        if kind == "limitation" and lever in PREFERENCE_LEVERS:
            kind = "preference"
        departments: list[str] = []
        for name in _as_name_list(item.get("departments")):
            hit = _resolve(name, department_names)
            if hit and hit not in departments:
                departments.append(hit)
        unit = str(item.get("unit") or "").strip().lower()
        value = _stated_number(item.get("value"), unit)
        length = _stated_number(item.get("length"))
        width = _stated_number(item.get("width"))
        clause = {
            "kind": kind,
            "lever": lever,
            "text": text,
            "departments": departments,
            "value": value,
            "unit": "ft" if unit.startswith("m") else (unit or None),
            "length": length,
            "width": width,
        }
        out.append(clause)
        _fold_clause_into_reading(reading, clause)
    return out


def _fold_clause_into_reading(reading: DesignReading, clause: dict[str, Any]) -> None:
    """Pass a classified clause onto the levers the rest of the pipeline already uses."""
    lever = clause["lever"]
    depts = list(clause.get("departments") or [])
    value = clause.get("value")
    if clause["kind"] == "requirement":
        if lever in {"same_mass", "keep_together"} and len(depts) >= 2:
            for other in depts[1:]:
                pair = (depts[0], other)
                if pair not in reading.keep_together and (other, depts[0]) not in reading.keep_together:
                    reading.keep_together.append((depts[0], other))
        elif lever == "keep_apart" and len(depts) >= 2:
            pass
        elif lever == "alone" and depts:
            for dept in depts:
                if not any(dept in mass_depts for _, mass_depts in reading.masses):
                    reading.masses.append((dept.title(), [dept]))
        elif lever == "double_height":
            clause["double_height"] = True
    elif clause["kind"] == "limitation":
        if lever in {"max_length", "max_height"} and value and reading.pair_length_ft is None:
            # A length or height cap is not a pairing length to fill.
            reading.dropped.append(f"{lever} kept as a cap, not a target")
        if lever == "max_stories" and value and reading.max_stories is None:
            reading.max_stories = max(1, int(value))
        if lever in {"site_length", "max_total_length"} and value and reading.site_length_ft is None:
            reading.site_length_ft = float(value)
        if lever == "max_width" and value and reading.max_width_ft is None:
            reading.max_width_ft = float(value)
    elif clause["kind"] == "preference":
        if lever == "pin_ground":
            for dept in depts:
                if dept not in reading.pin_ground:
                    reading.pin_ground.append(dept)
        elif lever == "loading" and not reading.loading:
            token = str(clause.get("text") or "").lower()
            if "single" in token:
                reading.loading = "single"
            elif "double" in token:
                reading.loading = "double"
        elif lever == "low_rise" and not reading.preference:
            reading.preference = "low_rise"
        elif lever in {"compact", "spread"} and not reading.preference:
            reading.preference = "compact" if lever == "compact" else "balanced"


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        from massing_explorer.brief import _parse_num

        token = str(value).strip().lower().replace(",", "")
        token = re.split(r"\s+", token, maxsplit=1)[0]
        try:
            number = _parse_num(token)
        except (TypeError, ValueError):
            return None
    if number <= 0:
        return None
    return number


def _slug(name: str, index: int) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return token or f"mass_{index}"


def reading_prompt(text: str, department_names: list[str], parsed: dict[str, Any]) -> str:
    names = "\n".join(f"- {n}" for n in department_names)
    return (
        "Read the user's sentence the way an architect would. They will not use "
        "a fixed template. Split every clause into one of three roles, then map "
        "it onto a lever. Do not invent sizes they did not state. Reply with one "
        "JSON object only.\n"
        "NUMBERS: Digits (40, 85.5) and spelled forms (four, forty, twenty-five, "
        "eighty-five) are VALUES only — never the modality role. Always emit "
        "Arabic numerals in JSON numeric fields (value, length, width). "
        "You MUST account for EVERY number in the user text — none may be ignored. "
        "If a clause has two sizes (e.g. 3 stories and 210 ft), emit both. "
        "Example: 'under forty meters' → value 40, unit m. Do not invent sizes.\n"
        "Roles:\n"
        "- requirement / limitation / preference are decided ONLY by modality "
        "words. Digits and spelled numbers are VALUES only — never the role.\n"
        "- requirement cues: must, needs to, has to, requires, exactly "
        "(also maintain / same mass / together when they insist on organization).\n"
        "- limitation cues: should be, cannot exceed, no more than, at least, "
        "should stay under (also max / under / below / no longer than as caps). "
        "A limitation is a bound to check, never a target to fill.\n"
        "- preference cues: prefer, ideally, would rather, better if, if possible, "
        "around, closer to (also roughly / would like). Soft intent you may decide how.\n"
        "- If preference cues appear with a bound word, keep preference. "
        "If require cues appear with a bound word, keep requirement "
        "(except must not / cannot exceed → limitation).\n"
        "clauses: list of "
        '{"kind": "requirement|limitation|preference", "lever": "...", '
        '"text": "the user clause", "departments": ["..."], "value": number or null, '
        '"unit": "ft|m|null", "length": number or null, "width": number or null}.\n'
        "Levers: same_mass, alone, mass_count, double_height, keep_together, "
        "keep_apart, exact_length, exact_width, exact_depth, dept_width, "
        "max_length, max_width, max_stories, max_height, "
        "site_length, max_total_length, pin_ground, pin_floor, ratio, loading, "
        "low_rise, compact, preferred_width.\n"
        "For a stated size on one program (e.g. 'core academic width must be "
        "80 ft'), use exact_width / preferred_width / max_width with "
        "departments filled and value in feet (convert m→ft only if unit is m).\n"
        "Also fill, only when they said it:\n"
        "- masses: required wings only, not leftover programs and not a floor preference\n"
        "- pair_length_ft: a cap on named wings together, never a length to fill\n"
        "- loading, corridor_ft, classroom_depth_ft: only if stated or needed to explain loading\n"
        "- pin_ground: floor preference only\n"
        "- reasons: one short line per role you used\n"
        "Use only these department names:\n"
        f"{names}\n"
        f"Regex already extracted (may be incomplete): {json.dumps(parsed)}\n"
        f"User: {text}"
    )


def request_reading(client: Any, text: str, department_names: list[str], parsed: dict[str, Any]) -> DesignReading:
    """Ask the model for a reading. Empty reading on any failure."""
    try:
        response = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Reply with JSON only. Classify each clause as a requirement, "
                        "a limitation, or a preference, and pass it on with a lever. "
                        "Process every stated number — digits and spelled (forty, four). "
                        "Put Arabic numerals in value/length/width. Never ignore a number "
                        "in the user text; if unsure, omit that clause rather than invent. "
                        "Do not invent sizes. A limitation is a cap, not a length to design to."
                    ),
                },
                {
                    "role": "user",
                    "content": reading_prompt(text, department_names, parsed),
                },
            ]
        )
        content = (response.get("message") or {}).get("content") or ""
    except Exception:
        return DesignReading(dropped=["model reading failed"])
    reading = validate_reading(_extract_json(content), department_names)
    if reading.empty and not reading.dropped:
        reading.dropped.append("model returned no usable choices")
    return reading


def scheme_choice_prompt(text: str, schemes: list[dict[str, Any]], reading: DesignReading) -> str:
    compact = [
        {
            "index": s.get("index"),
            "summary": s.get("summary"),
            "total_length_ft": s.get("total_length_ft"),
            "masses": s.get("masses"),
        }
        for s in schemes
    ]
    return (
        "Pick exactly one verified scheme. Do not invent a new width. "
        "Reply with JSON: {\"index\": <int>, \"reason\": \"...\"}.\n"
        f"User: {text}\n"
        f"Reading: {json.dumps(reading.to_dict())}\n"
        f"Schemes: {json.dumps(compact)}"
    )


def request_scheme_index(
    client: Any,
    text: str,
    schemes: list[dict[str, Any]],
    reading: DesignReading,
) -> int | None:
    if not schemes or len(schemes) < 2:
        return None
    try:
        response = client.chat(
            [
                {"role": "system", "content": "Reply with JSON only."},
                {
                    "role": "user",
                    "content": scheme_choice_prompt(text, schemes, reading),
                },
            ]
        )
        content = (response.get("message") or {}).get("content") or ""
    except Exception:
        return None
    data = _extract_json(content) or {}
    try:
        index = int(data.get("index"))
    except (TypeError, ValueError):
        return None
    if index < 0 or index >= len(schemes):
        return None
    return index


def cap_reasoning_prompt(
    cap_ft: float,
    masses: list[dict[str, Any]],
    max_stories: int,
    locked: dict[str, int],
) -> str:
    return (
        "The user set a length cap. It is not a length to design to. "
        "The bars below were sized from classroom loading or the stated ratio, "
        "and some are longer than the cap. Reason about a better way to mass "
        "under that limit: classroom loading, and story counts up to the max. "
        "Do not return a width or a length. Do not make any bar the cap long. "
        "Reply with JSON only:\n"
        '{"loading": "single" | "double" | null, "stories": {"mass_id": <int>}, '
        '"reason": "..."}\n'
        f"Cap: shorter than {cap_ft:g} ft. Max stories: {max_stories}. "
        f"Story counts already fixed by the brief: {json.dumps(locked)}.\n"
        f"Masses: {json.dumps(masses)}"
    )


def request_cap_reasoning(
    client: Any,
    cap_ft: float,
    masses: list[dict[str, Any]],
    max_stories: int,
    locked: dict[str, int],
) -> dict[str, Any] | None:
    """Ask how to mass under a length cap without using the cap as the length."""
    if not masses or cap_ft <= 0:
        return None
    try:
        response = client.chat(
            [
                {"role": "system", "content": "Reply with JSON only. Do not invent a length."},
                {
                    "role": "user",
                    "content": cap_reasoning_prompt(cap_ft, masses, max_stories, locked),
                },
            ]
        )
        content = (response.get("message") or {}).get("content") or ""
    except Exception:
        return None
    data = _extract_json(content) or {}
    loading = str(data.get("loading") or "").strip().lower()
    if loading not in {"single", "double"}:
        loading = ""
    stories: dict[str, int] = {}
    raw_stories = data.get("stories") or {}
    if isinstance(raw_stories, dict):
        known = {m["id"] for m in masses}
        for mid, count in raw_stories.items():
            if str(mid) not in known or str(mid) in locked:
                continue
            try:
                n = int(count)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= max_stories:
                stories[str(mid)] = n
    if not loading and not stories:
        return None
    return {
        "loading": loading or None,
        "stories": stories,
        "reason": str(data.get("reason") or ""),
    }


def repair_prompt(failed: list[str], masses: list[dict[str, Any]]) -> str:
    return (
        "A verified solve failed. Choose one existing repair, or none. "
        "Do not invent a width. Reply with JSON only:\n"
        '{"action": "add_story" | "none", "mass_id": "<id or empty>", "reason": "..."}\n'
        "add_story adds one story to that mass. Use none if no listed mass should change.\n"
        f"Failures: {json.dumps(failed[:8])}\n"
        f"Masses: {json.dumps(masses)}"
    )


def request_repair(
    client: Any,
    failed: list[str],
    masses: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not failed or not masses:
        return None
    try:
        response = client.chat(
            [
                {"role": "system", "content": "Reply with JSON only."},
                {"role": "user", "content": repair_prompt(failed, masses)},
            ]
        )
        content = (response.get("message") or {}).get("content") or ""
    except Exception:
        return None
    data = _extract_json(content) or {}
    action = str(data.get("action") or "none").strip().lower()
    if action != "add_story":
        return None
    mass_id = str(data.get("mass_id") or "").strip()
    known = {m["id"] for m in masses}
    if mass_id not in known:
        return None
    return {"action": "add_story", "mass_id": mass_id, "reason": str(data.get("reason") or "")}
