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
    return reading


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
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
        "a fixed template. Map whatever organization they describe into the JSON "
        "below. Do not invent sizes they did not state. Reply with one JSON object "
        "only.\n"
        "Fields:\n"
        "- masses: wings the user named, in order. Each is "
        '{"name": "Mass 1", "departments": ["..."]}. Use this when they say '
        '"mass one is gym and core academic", "wing A contains ...", '
        '"put dining and media in the second building", and similar.\n'
        "- pair_length_ft: a cap on the combined length of the named wings, when "
        'they say "these two together under 500" or "fit them in 500 feet". '
        "This is a maximum, not a length to fill. Only for the named masses, "
        "not the whole campus, unless they clearly mean the whole site.\n"
        "- loading: single or double, when they say how classrooms are loaded, "
        "or when you must choose so the bar can function. single = one "
        "classroom depth plus a corridor; double = classrooms both sides.\n"
        "- corridor_ft, classroom_depth_ft: only if they stated them, or the "
        "minimum you need to explain the loading. Do not invent a length to "
        "use up a cap.\n"
        "- preferred_width_ft: only if they asked for a width or a length/width ratio\n"
        "- site_length_ft: only if the whole site or all masses together are capped\n"
        "- max_width_ft, max_stories: only if stated\n"
        "- keep_together: pairs that must share a mass when they did not list full wings\n"
        "- pair_on_frontage: departments in adjacent bars, if they did not give pair_length_ft\n"
        "- pin_ground: departments that must sit on the ground\n"
        "- preference: low_rise, compact, balanced, or null\n"
        "- reasons: short paraphrases of the user sentence\n"
        "Leave a field null or empty if they did not say it. Use only these "
        f"department names:\n{names}\n"
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
                        "Reply with JSON only. Copy numbers the user stated "
                        "(shared length, site length, width, stories). Do not invent any."
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
