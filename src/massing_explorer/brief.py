"""
Parse a short user brief into engine constraints and grouping rules.

Users type things like "custodial and dining should stay together, site
length is 300, width is 100, max story is 4". The LLM is unreliable at
translating that into the right tool calls, so this module does it
deterministically and the pipeline groups / searches from the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .group import GroupingResult, group_departments, match_department
from .session import StudySession

_NUM = r"(\d+(?:\.\d+)?)"
_FT = r"(?:\s*(?:ft|feet|foot))?"


@dataclass
class ParsedBrief:
    text: str
    keep_together: list[tuple[str, str]] = field(default_factory=list)
    keep_apart: list[tuple[str, str]] = field(default_factory=list)
    constraints: dict[str, float] = field(default_factory=dict)
    max_stories: int | None = None
    preference: str = "balanced"
    unmatched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keep_together": [list(p) for p in self.keep_together],
            "keep_apart": [list(p) for p in self.keep_apart],
            "constraints": self.constraints,
            "max_stories": self.max_stories,
            "preference": self.preference,
            "unmatched": self.unmatched,
            "notes": self.notes,
        }

    @property
    def has_site(self) -> bool:
        return any(
            k in self.constraints
            for k in (
                "max_total_length_ft",
                "max_building_length_ft",
                "max_building_width_ft",
            )
        )


def _first_number(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return float(match.group(1))
    return None


def parse_brief(text: str, department_names: list[str]) -> ParsedBrief:
    parsed = ParsedBrief(text=text or "")
    raw = text or ""

    total = _first_number(
        [
            rf"site\s+length\s*(?:is|of|=|:)?\s*{_NUM}{_FT}",
            rf"site\s+frontage\s*(?:is|of|=|:)?\s*{_NUM}{_FT}",
            rf"frontage\s*(?:is|of|=|:)?\s*{_NUM}{_FT}",
            rf"{_NUM}{_FT}\s+of\s+frontage",
            rf"(?:total|combined|overall)\s+length\s*(?:is|of|=|:)?\s*{_NUM}{_FT}",
        ],
        raw,
    )
    if total is not None:
        parsed.constraints["max_total_length_ft"] = total
        parsed.notes.append(f"site / total length {total:g} ft")

    per_len = _first_number(
        [
            rf"nothing\s+longer\s+than\s*{_NUM}{_FT}",
            rf"no\s+(?:wing|building|mass)\s+(?:over|longer than)\s*{_NUM}{_FT}",
            rf"max(?:imum)?\s+(?:building\s+)?length\s*(?:is|of|=|:)?\s*{_NUM}{_FT}",
        ],
        raw,
    )
    if per_len is not None:
        parsed.constraints["max_building_length_ft"] = per_len
        parsed.notes.append(f"max building length {per_len:g} ft")

    width = _first_number(
        [
            rf"nothing\s+wider\s+than\s*{_NUM}{_FT}",
            rf"no\s+(?:wing|building|mass)\s+wider\s+than\s*{_NUM}{_FT}",
            rf"max(?:imum)?\s+(?:building\s+)?width\s*(?:is|of|=|:)?\s*{_NUM}{_FT}",
            rf"(?:site|building)\s+width\s*(?:is|of|=|:)?\s*{_NUM}{_FT}",
            rf"width\s*(?:is|of|=|:)?\s*{_NUM}{_FT}",
        ],
        raw,
    )
    if width is not None:
        parsed.constraints["max_building_width_ft"] = width
        parsed.notes.append(f"max width {width:g} ft")

    stories = _first_number(
        [
            rf"max(?:imum)?\s+stor(?:y|ies|eys)?\s+is\s+{_NUM}",
            rf"max(?:imum)?\s+stor(?:y|ies|eys)?\s*(?:of|count|=|:)?\s*{_NUM}",
            rf"no\s+more\s+than\s*{_NUM}\s*(?:floor|stor)",
            rf"up\s+to\s*{_NUM}\s*(?:floor|stor)",
            rf"{_NUM}\s*(?:floor|stor(?:y|ies))(?:s)?\s+max",
        ],
        raw,
    )
    if stories is not None:
        parsed.max_stories = max(1, int(stories))
        parsed.notes.append(f"max stories {parsed.max_stories}")

    low = re.search(r"low[\s-]?rise|as low as|spread\s+out|keep it low", raw, flags=re.I)
    compact = re.search(r"\bcompact\b|small footprint|tight footprint", raw, flags=re.I)
    if low:
        parsed.preference = "low_rise"
    elif compact:
        parsed.preference = "compact"

    # --- stay together / keep apart ---------------------------------------
    parsed.keep_together = _extract_pairs(
        raw,
        department_names,
        parsed.unmatched,
        together=True,
    )
    parsed.keep_apart = _extract_pairs(
        raw,
        department_names,
        parsed.unmatched,
        together=False,
    )
    return parsed


_TOGETHER = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,40}?)\s+(?:and|&|/)\s+([a-z][a-z0-9&/' \-]{1,40}?)"
    r"\s+(?:should\s+|must\s+|need to\s+|have to\s+)?"
    r"(?:stay|remain|go|be|sit)?\s*(?:together|with each other|in the same|"
    r"in one|in a single)",
    flags=re.I,
)
_KEEP_TOGETHER = re.compile(
    r"(?:keep|put|place)\s+([a-z][a-z0-9&/' \-]{1,40}?)\s+"
    r"(?:and|&|with|/)\s+([a-z][a-z0-9&/' \-]{1,40}?)"
    r"\s+together",
    flags=re.I,
)
_APART = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,40}?)\s+(?:and|&|/)\s+([a-z][a-z0-9&/' \-]{1,40}?)"
    r"\s+(?:should\s+|must\s+)?"
    r"(?:stay|remain|be|kept)?\s*(?:apart|separate|split)",
    flags=re.I,
)


_STOP = {
    "should", "shall", "must", "need", "have", "to", "stay", "remain", "go",
    "be", "sit", "keep", "put", "place", "together", "with", "each", "other",
    "in", "the", "same", "one", "a", "an", "and", "nd", "or", "of", "hsould",
}


def _departments_mentioned(text: str, names: list[str]) -> list[str]:
    """Walk a phrase and pick up department names, including typos."""
    from .group import _norm

    words = [w for w in _norm(text).split() if w not in _STOP]
    found: list[str] = []
    used: set[str] = set()
    i = 0
    while i < len(words):
        matched = None
        span_used = 1
        for span in (3, 2, 1):
            if i + span > len(words):
                continue
            phrase = " ".join(words[i : i + span])
            hit = match_department(phrase, names)
            if hit and hit not in used:
                matched = hit
                span_used = span
                break
        if matched:
            used.add(matched)
            found.append(matched)
            i += span_used
        else:
            i += 1
    return found


def _extract_pairs(
    text: str,
    names: list[str],
    unmatched: list[str],
    together: bool,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    keyword = r"together" if together else r"(?:apart|separate)"
    for match in re.finditer(keyword, text, flags=re.I):
        window = text[max(0, match.start() - 90) : match.start()]
        hits = _departments_mentioned(window, names)
        if len(hits) >= 2:
            a, b = hits[-2], hits[-1]
            key = tuple(sorted((a, b)))
            if key not in seen and a != b:
                seen.add(key)
                pairs.append((a, b))
        elif together and window.strip():
            leftover = window.strip()
            if leftover.lower() not in unmatched:
                unmatched.append(leftover)

    patterns = (_TOGETHER, _KEEP_TOGETHER) if together else (_APART,)
    for pattern in patterns:
        for match in pattern.finditer(text):
            a = match_department(match.group(1), names)
            b = match_department(match.group(2), names)
            if a and b and a != b:
                key = tuple(sorted((a, b)))
                if key not in seen:
                    seen.add(key)
                    pairs.append((a, b))
    return pairs


def should_apply_brief(session: StudySession, parsed: ParsedBrief) -> bool:
    """Regroup when there is no grouping yet, or the brief changes grouping/site."""
    if not session.masses:
        return True
    if parsed.keep_together or parsed.keep_apart:
        return True
    if parsed.constraints or parsed.max_stories:
        return True
    return False


def _merge_reading(parsed: ParsedBrief, reading: Any) -> None:
    if reading is None:
        return
    seen = {tuple(sorted(p)) for p in parsed.keep_together}
    for pair in getattr(reading, "keep_together", []) or []:
        key = tuple(sorted(pair))
        if key not in seen and pair[0] != pair[1]:
            seen.add(key)
            parsed.keep_together.append((pair[0], pair[1]))
    pref = getattr(reading, "preference", None)
    if pref in {"low_rise", "compact", "balanced"}:
        parsed.preference = pref
    site = getattr(reading, "site_length_ft", None)
    if site and "max_total_length_ft" not in parsed.constraints:
        parsed.constraints["max_total_length_ft"] = float(site)
        parsed.notes.append(f"site / total length {float(site):g} ft")
    width = getattr(reading, "max_width_ft", None)
    if width and "max_building_width_ft" not in parsed.constraints:
        parsed.constraints["max_building_width_ft"] = float(width)
        parsed.notes.append(f"max width {float(width):g} ft")
    stories = getattr(reading, "max_stories", None)
    if stories and parsed.max_stories is None:
        parsed.max_stories = int(stories)
        parsed.notes.append(f"max stories {parsed.max_stories}")


def _apply_reading_levers(session: StudySession, reading: Any) -> list[str]:
    """Pair and pin only after grouping exists. Numbers stay engine-owned."""
    from .tools import pair_masses, pin_department_to_floor

    notes: list[str] = []
    if reading is None:
        return notes

    loading = getattr(reading, "loading", None)
    if loading in {"single", "double"}:
        session.constraints["loading"] = loading
        notes.append(f"classroom loading: {loading}")
    corridor = getattr(reading, "corridor_ft", None)
    if corridor:
        session.constraints["corridor_ft"] = float(corridor)
        notes.append(f"corridor {float(corridor):g} ft")
    depth = getattr(reading, "classroom_depth_ft", None)
    if depth:
        session.constraints["classroom_depth_ft"] = float(depth)
    preferred = getattr(reading, "preferred_width_ft", None)
    if preferred:
        session.constraints["preferred_width_ft"] = float(preferred)
        notes.append(f"preferred width {float(preferred):g} ft")

    for dept in getattr(reading, "pin_ground", []) or []:
        pinned = pin_department_to_floor(session, dept, 0)
        if pinned.get("ok"):
            notes.append(f"pinned {dept} to ground")

    named = list(getattr(reading, "masses", []) or [])
    pair_length = getattr(reading, "pair_length_ft", None)
    if pair_length and len(named) >= 2:
        mass_ids: list[str] = []
        for label, depts in named:
            mass = next(
                (
                    m
                    for m in session.masses
                    if depts and all(d in m.departments for d in depts)
                ),
                None,
            )
            if mass and mass.id not in mass_ids:
                mass_ids.append(mass.id)
        if len(mass_ids) >= 2:
            session.constraints["stack_above_double_height"] = True
            paired = pair_masses(
                session, mass_ids, float(pair_length), length_is_cap=True
            )
            if paired.get("ok"):
                notes.append(
                    "paired "
                    + " + ".join(mass_ids)
                    + f" under {float(pair_length):g} ft (cap, not a target length)"
                )
                return notes

    pair_depts = list(getattr(reading, "pair_departments", []) or [])
    total = session.constraints.get("max_total_length_ft")
    if len(pair_depts) >= 2 and total:
        mass_ids: list[str] = []
        for dept in pair_depts:
            mass = next((m for m in session.masses if dept in m.departments), None)
            if mass and mass.id not in mass_ids:
                mass_ids.append(mass.id)
        if len(mass_ids) >= 2:
            paired = pair_masses(session, mass_ids, float(total))
            if paired.get("ok"):
                notes.append(
                    "paired "
                    + " + ".join(mass_ids)
                    + f" on {float(total):g} ft frontage"
                )
    return notes


def _slug_id(name: str, index: int, used: set[str]) -> str:
    import re

    token = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or f"mass_{index}"
    mass_id = token
    n = 2
    while mass_id in used:
        mass_id = f"{token}_{n}"
        n += 1
    used.add(mass_id)
    return mass_id


def _grouping_payload(session: StudySession, grouping: GroupingResult, reading: Any) -> list[dict[str, Any]]:
    """Named wings from the reading, plus family masses for everyone left over."""
    named = list(getattr(reading, "masses", []) or []) if reading is not None else []
    if not named:
        return [
            {
                "id": m.id,
                "name": m.name,
                "departments": m.departments,
                "story_count": m.story_count,
                "notes": m.notes,
            }
            for m in grouping.masses
        ]

    claimed = {d for _, depts in named for d in depts}
    used: set[str] = set()
    payload: list[dict[str, Any]] = []
    for i, (label, depts) in enumerate(named, start=1):
        payload.append(
            {
                "id": _slug_id(label, i, used),
                "name": label,
                "departments": list(depts),
                "story_count": 2,
                "notes": "named by the user",
            }
        )
    for mass in grouping.masses:
        left = [d for d in mass.departments if d not in claimed]
        if not left:
            continue
        payload.append(
            {
                "id": _slug_id(mass.id, len(payload) + 1, used),
                "name": mass.name,
                "departments": left,
                "story_count": mass.story_count,
                "notes": mass.notes,
            }
        )
    known = set(session.department_names())
    placed = {d for item in payload for d in item["departments"]}
    for name in sorted(known - placed):
        payload.append(
            {
                "id": _slug_id(name, len(payload) + 1, used),
                "name": name.title(),
                "departments": [name],
                "story_count": 2,
                "notes": "",
            }
        )
    return payload


def apply_parsed_brief(
    session: StudySession,
    parsed: ParsedBrief,
    regroup: bool = True,
    search: bool = True,
    top_n: int = 3,
    reading: Any = None,
) -> dict[str, Any]:
    """Write the parsed brief onto the session and optionally search for a scheme."""
    from .config import load_project_config
    from .tools import search_site_schemes, set_constraint, set_grouping, solve_dimensions

    config = load_project_config(session.config_path or None)
    _merge_reading(parsed, reading)

    for key, value in parsed.constraints.items():
        set_constraint(session, key, value)
    if parsed.max_stories is not None:
        set_constraint(session, "max_stories", parsed.max_stories)

    grouping: GroupingResult | None = None
    if regroup:
        grouping = group_departments(
            session.program,
            keep_together=parsed.keep_together,
            keep_apart=parsed.keep_apart,
            config=config,
            default_stories=2,
        )
        set_grouping(session, _grouping_payload(session, grouping, reading))
        for a, b in parsed.keep_together:
            note = f"{a} stays with {b}"
            if note not in session.adjacency_notes:
                session.adjacency_notes.append(note)
        session.save()

    reading_notes = _apply_reading_levers(session, reading)

    searched = None
    solved = None
    if search and session.masses:
        max_stories = int(
            parsed.max_stories
            or session.constraints.get("max_stories")
            or 4
        )
        if parsed.has_site or session.constraints.get("max_total_length_ft"):
            searched = search_site_schemes(
                session,
                max_total_length_ft=session.constraints.get("max_total_length_ft"),
                max_length_ft=session.constraints.get("max_building_length_ft"),
                max_width_ft=session.constraints.get("max_building_width_ft"),
                max_stories=max_stories,
                preference=parsed.preference,
                top_n=top_n,
            )
            if searched.get("ok") and searched.get("found"):
                from .tools import apply_scheme

                solved = apply_scheme(session, 0)
        else:
            solved = solve_dimensions(session)

    return {
        "ok": True,
        "parsed": parsed.to_dict(),
        "reading": reading.to_dict() if reading is not None and hasattr(reading, "to_dict") else None,
        "reading_notes": reading_notes,
        "grouping": (
            [
                {
                    "id": m.id,
                    "name": m.name,
                    "departments": m.departments,
                    "notes": m.notes,
                }
                for m in session.masses
            ]
            if session.masses
            else None
        ),
        "search": searched,
        "solved": solved,
        "instruction": (
            "Facts were parsed by the engine. Any reading choices (stay-together, "
            "frontage pairing, ground pins, search preference) were applied through "
            "existing tools and then checked. Report the reading and the scheme. "
            "Do not invent a different split unless the user asks to regroup."
        ),
    }


def apply_brief(session: StudySession, text: str, **kwargs: Any) -> dict[str, Any]:
    parsed = parse_brief(text, session.department_names())
    if not should_apply_brief(session, parsed) and session.masses:
        return {
            "ok": True,
            "skipped": True,
            "reason": "No grouping or site change in this message.",
            "parsed": parsed.to_dict(),
        }
    return apply_parsed_brief(session, parsed, **kwargs)
