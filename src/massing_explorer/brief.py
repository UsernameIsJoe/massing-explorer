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
_UNIT = r"(?:\s*(ft|feet|foot|m|meter|meters|metre|metres))?"
_METERS_TO_FEET = 3.280839895


@dataclass
class ParsedBrief:
    text: str
    keep_together: list[tuple[str, str]] = field(default_factory=list)
    keep_apart: list[tuple[str, str]] = field(default_factory=list)
    constraints: dict[str, float] = field(default_factory=dict)
    max_stories: int | None = None
    named_masses: list[tuple[str, list[str]]] = field(default_factory=list)
    pair_length_ft: float | None = None
    mass_count: int | None = None
    pin_ground: list[str] = field(default_factory=list)
    free_departments: list[str] = field(default_factory=list)
    open_slots: int | None = None
    length_over_width: float | None = None
    double_height_departments: list[str] = field(default_factory=list)
    preference: str = "balanced"
    unmatched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keep_together": [list(p) for p in self.keep_together],
            "keep_apart": [list(p) for p in self.keep_apart],
            "constraints": self.constraints,
            "max_stories": self.max_stories,
            "named_masses": [
                {"name": name, "departments": list(depts)}
                for name, depts in self.named_masses
            ],
            "pair_length_ft": self.pair_length_ft,
            "preference": self.preference,
            "unmatched": self.unmatched,
            "pin_ground": list(self.pin_ground),
            "free_departments": list(self.free_departments),
            "open_slots": self.open_slots,
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


def _to_feet(value: float, unit: str | None) -> float:
    if unit and unit.lower().startswith("m"):
        return value * _METERS_TO_FEET
    return value


def _first_length(patterns: list[str], text: str) -> float | None:
    """Read a length. Meters are converted to feet. A bare number stays feet."""
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            unit = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            return _to_feet(float(match.group(1)), unit)
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

    per_len = _first_length(
        [
            rf"nothing\s+longer\s+than\s*{_NUM}{_UNIT}",
            rf"no\s+(?:wing|building|mass)\s+(?:over|longer than)\s*{_NUM}{_UNIT}",
            rf"(?:max(?:imum)?|length)\s+limit\s*(?:is|of|=|:)?\s*{_NUM}{_UNIT}",
            rf"max(?:imum)?\s+(?:building\s+)?length\s*(?:is|of|=|:)?\s*{_NUM}{_UNIT}",
        ],
        raw,
    )
    if per_len is not None:
        parsed.constraints["max_building_length_ft"] = per_len
        parsed.constraints["length_limit_is_cap"] = 1
        parsed.notes.append(f"max building length {per_len:g} ft (cap, not a target)")

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
            rf"(?:height|storey|story)\s+limit\s*(?:is|of|=|:)?\s*{_NUM}",
            rf"limit(?:ed)?\s+(?:height|to)\s*{_NUM}\s*(?:floor|stor)",
            rf"no\s+more\s+than\s*{_NUM}\s*(?:floor|stor)",
            rf"up\s+to\s*{_NUM}\s*(?:floor|stor)",
            rf"{_NUM}\s*(?:floor|stor(?:y|ies))(?:s)?\s+max",
        ],
        raw,
    )
    if stories is not None:
        parsed.max_stories = max(1, int(stories))
        parsed.notes.append(f"max stories {parsed.max_stories}")
    elif (hit := re.search(rf"max(?:imum)?\s+{_NUM}\s*stor", raw, flags=re.I)):
        parsed.max_stories = max(1, int(float(hit.group(1))))
        parsed.notes.append(f"max stories {parsed.max_stories}")

    shorter = _first_length(
        [
            rf"length\s+should be\s+shorter than\s+{_NUM}{_UNIT}",
            rf"length\s+(?:shorter|less)\s+than\s+{_NUM}{_UNIT}",
            rf"(?:each\s+)?(?:mass|wing|building)\s+length\s+(?:shorter|less|under)\s+than\s+{_NUM}{_UNIT}",
        ],
        raw,
    )
    if shorter is not None and "max_building_length_ft" not in parsed.constraints:
        parsed.constraints["max_building_length_ft"] = shorter
        parsed.constraints["length_limit_is_cap"] = 1
        parsed.notes.append(f"length shorter than {shorter:g} ft (cap, not a target)")

    preferred = _first_length(
        [
            rf"prefer(?:ably|red)?\s+(?:under|below|shorter than|less than)\s+{_NUM}{_UNIT}",
            rf"prefer(?:ably|red)?\s+length\s+(?:under|below|shorter than)\s+{_NUM}{_UNIT}",
        ],
        raw,
    )
    if preferred is not None:
        parsed.constraints["preferred_length_ft"] = preferred
        parsed.notes.append(
            f"prefer length under {preferred:g} ft (preference, not a target)"
        )

    # "length should be 50" is an exact length. "shorter than / under" is not.
    if shorter is None and not re.search(
        r"length\s+(?:should be\s+)?(?:shorter|less|under)|nothing\s+longer",
        raw,
        flags=re.I,
    ):
        exact = _first_length(
            [
                rf"length\s+should be\s+{_NUM}{_UNIT}",
                rf"length\s+(?:is|of|=)\s*{_NUM}{_UNIT}",
                rf"(?:each\s+)?(?:mass|wing|building)\s+length\s+(?:is|of|=|should be)\s*{_NUM}{_UNIT}",
            ],
            raw,
        )
        if exact is not None:
            parsed.constraints["exact_building_length_ft"] = exact
            parsed.notes.append(f"length should be {exact:g} ft (exact)")

    ratio = re.search(r"ratio\s+(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", raw, flags=re.I)
    if ratio:
        length_part = float(ratio.group(1))
        width_part = float(ratio.group(2))
        if width_part > 0:
            parsed.length_over_width = length_part / width_part
            parsed.notes.append(
                f"prefer length:width {length_part:g}:{width_part:g}"
            )

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
    parsed.named_masses = _extract_named_masses(raw, department_names)
    _apply_counted_masses(parsed, raw, department_names)
    parsed.pair_length_ft = _extract_named_pair_length(raw)
    if parsed.constraints.get("max_building_length_ft"):
        # "length shorter than N" is a cap on each bar, not a pairing to fill.
        parsed.pair_length_ft = None
    if parsed.pair_length_ft:
        parsed.notes.append(
            f"named wings together under {parsed.pair_length_ft:g} ft (cap)"
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


_ORDINAL = {
    "1": 1,
    "2": 2,
    "3": 3,
    "one": 1,
    "two": 2,
    "three": 3,
}

# Short words the user says for a department, mapped onto name keywords.
_HINTS = (
    (("gymnasium", "gym"), ("health", "physical", "gym")),
    (("art", "band", "music"), ("art", "music")),
    (("dining", "cafeteria", "kitchen"), ("dining", "food")),
    (("media", "library"), ("media",)),
    (("special education", "special ed"), ("special",)),
    (("academic", "classroom"), ("academic",)),
)


def _clause_departments(text: str, names: list[str]) -> list[str]:
    from .group import _norm

    found = _departments_mentioned(text, names)
    used = set(found)
    norm = f" {_norm(text)} "
    for triggers, keys in _HINTS:
        if not any(f" {t} " in norm or norm.strip() == t or f" {t}" in norm for t in triggers):
            # also allow trigger as a whole token
            if not any(t in norm for t in triggers):
                continue
        for name in names:
            if name in used:
                continue
            n = _norm(name)
            if any(k in n for k in keys):
                used.add(name)
                found.append(name)
                break
    return found


def _extract_named_masses(
    text: str, names: list[str]
) -> list[tuple[str, list[str]]]:
    """
    Wings the user named in the sentence itself.

    "the gym and art should be in mass one" and "mass two is dining and media"
    are organization, not something the chat model may invent later.
    """
    by_ord: dict[int, list[str]] = {}

    def take(ord_raw: str, body: str) -> None:
        ord_n = _ORDINAL.get(ord_raw.lower())
        if not ord_n:
            return
        depts = _clause_departments(body, names)
        if depts:
            by_ord[ord_n] = depts

    stop = (
        r"(?=\s*(?:\.|;|$)"
        r"|,\s*(?:mass|wing)\s+(?:one|two|three|1|2|3)\b"
        r"|\s+and\s+(?:mass|wing)\s+(?:one|two|three|1|2|3)\b)"
    )
    for match in re.finditer(
        r"(.+?)\s+should be in\s+(?:mass|wing)\s+(one|two|three|1|2|3)\b",
        text,
        flags=re.I,
    ):
        take(match.group(2), match.group(1))
    for match in re.finditer(
        rf"(?:mass|wing)\s+(one|two|three|1|2|3)\s+is\s+(.+?){stop}",
        text,
        flags=re.I,
    ):
        take(match.group(1), match.group(2))

    claimed: set[str] = set()
    out: list[tuple[str, list[str]]] = []
    for ord_n in sorted(by_ord):
        depts = [d for d in by_ord[ord_n] if d not in claimed]
        if not depts:
            continue
        claimed.update(depts)
        label = f"Mass {ord_n}"
        out.append((label, depts))
    return out


def _extract_named_pair_length(text: str) -> float | None:
    """Cap on the named wings, not the whole site frontage."""
    patterns = [
        rf"(?:mass|wing)\s+one\s+and\s+(?:mass|wing)\s+two\b.{{0,60}}?\bunder\s+{_NUM}{_FT}",
        rf"(?:these|those)\s+two\b.{{0,40}}?\bunder\s+{_NUM}{_FT}",
        rf"together\s+should be\s+under\s+{_NUM}{_FT}",
        rf"length of (?:these|those) two.{0,20}?\bunder\s+{_NUM}{_FT}",
    ]
    return _first_number(patterns, text)


def _apply_counted_masses(
    parsed: ParsedBrief, text: str, names: list[str]
) -> None:
    """
    "4 masses. Gym and dining in the same mass. Academic by itself.
    Art and music on the ground floor."
    """
    count = re.search(r"(\d+)\s+masses\b", text, flags=re.I)
    if count:
        parsed.mass_count = max(1, int(count.group(1)))

    same: list[list[str]] = []
    for match in re.finditer(
        r"([^.]+?)\s+should be\s+(?:double[\s-]?height\s+and\s+)?in the same mass",
        text,
        flags=re.I,
    ):
        depts = _clause_departments(match.group(1), names)
        if len(depts) >= 2:
            same.append(depts)
            if re.search(r"double[\s-]?height", match.group(0), flags=re.I):
                for dept in depts:
                    if dept not in parsed.double_height_departments:
                        parsed.double_height_departments.append(dept)

    solos: list[str] = []
    for match in re.finditer(
        r"([^.]+?)\s+by itself(?:\s+is one mass)?",
        text,
        flags=re.I,
    ):
        depts = _clause_departments(match.group(1), names)
        solos.extend(depts)

    for match in re.finditer(
        r"([^.]+?)\s+on the ground floor",
        text,
        flags=re.I,
    ):
        # Floor preference only. It does not create a mass.
        for dept in _clause_departments(match.group(1), names):
            if dept not in parsed.pin_ground:
                parsed.pin_ground.append(dept)

    claimed: set[str] = {d for group in same for d in group}
    claimed.update(solos)
    claimed.update(d for _, depts in parsed.named_masses for d in depts)

    groups: list[tuple[str, list[str]]] = list(parsed.named_masses)
    used_ids = {name for name, _ in groups}
    n = len(groups)

    def add(label: str, depts: list[str]) -> None:
        nonlocal n
        clean = [d for d in depts if d not in {x for _, ds in groups for x in ds}]
        if not clean:
            return
        n += 1
        name = label if label not in used_ids else f"{label} {n}"
        used_ids.add(name)
        groups.append((name, clean))

    for i, depts in enumerate(same, start=1):
        add(f"Mass {n + 1}", depts)
    for dept in solos:
        add(dept.title(), [dept])

    claimed_now = {d for _, ds in groups for d in ds}
    parsed.free_departments = [name for name in names if name not in claimed_now]
    if parsed.mass_count:
        parsed.open_slots = max(0, parsed.mass_count - len(groups))
    if parsed.pin_ground:
        parsed.notes.append(
            "ground floor is a floor preference, not a separate mass: "
            + ", ".join(parsed.pin_ground)
        )

    if groups:
        parsed.named_masses = groups
        parsed.notes.append(
            "required masses: "
            + "; ".join(f"{name} ({', '.join(depts)})" for name, depts in groups)
        )
    if parsed.free_departments:
        parsed.notes.append(
            "the model may group these into the remaining masses: "
            + ", ".join(parsed.free_departments)
        )


def _proposal_for_free(
    reading: Any,
    free: list[str],
    slots: int,
    pin_ground: list[str],
) -> list[list[str]] | None:
    """Accept a model grouping of the unassigned departments if it stays neat."""
    if reading is None or not free or slots <= 0:
        return None
    proposed: list[list[str]] = []
    used: set[str] = set()
    free_set = set(free)
    for _, depts in getattr(reading, "masses", []) or []:
        clean = [d for d in depts if d in free_set and d not in used]
        if clean:
            proposed.append(clean)
            used.update(clean)
    if set(used) != free_set or len(proposed) > slots:
        return None
    pinned = set(pin_ground)
    if any(len(group) == 1 and group[0] in pinned for group in proposed) and len(proposed) > 1:
        return None
    singletons = sum(1 for group in proposed if len(group) == 1)
    if singletons >= 2 and any(len(group) >= 3 for group in proposed):
        return None
    return proposed


def _pack_free(departments: list[str], slots: int) -> list[list[str]]:
    """Group leftovers by family. Do not isolate a floor preference as its own mass."""
    from .group import _family_of

    if not departments:
        return []
    if slots <= 1:
        return [list(departments)]
    buckets: dict[str, list[str]] = {}
    for name in departments:
        buckets.setdefault(_family_of(name), []).append(name)
    groups = list(buckets.values())
    while len(groups) > slots:
        groups.sort(key=len)
        groups.append(groups.pop(0) + groups.pop(0))
    return groups


def assign_open_departments(parsed: ParsedBrief, reading: Any = None) -> None:
    """
    Fill remaining mass slots after the sentence's hard wings.

    "On the ground floor" does not take a slot. A model proposal is used only
    if it assigns every free department once and does not scatter them.
    """
    free = list(parsed.free_departments)
    if not free:
        return
    slots = parsed.open_slots if parsed.open_slots is not None else 1
    proposal = _proposal_for_free(reading, free, slots, parsed.pin_ground)
    groups = proposal or _pack_free(free, max(1, slots))
    start = len(parsed.named_masses)
    for i, depts in enumerate(groups, start=1):
        parsed.named_masses.append((f"Mass {start + i}", depts))
    parsed.free_departments = []
    parsed.notes.append(
        "remaining masses: "
        + "; ".join(f"{name} ({', '.join(depts)})" for name, depts in parsed.named_masses[start:])
    )


def should_apply_brief(session: StudySession, parsed: ParsedBrief) -> bool:
    """Regroup when there is no grouping yet, or the brief changes grouping/site."""
    if not session.masses:
        return True
    if parsed.keep_together or parsed.keep_apart or parsed.named_masses:
        return True
    if parsed.constraints or parsed.max_stories:
        return True
    return False


def briefing_from_parsed(parsed: ParsedBrief) -> dict[str, list[dict[str, Any]]]:
    """The three roles, even when the model did not send a clause list."""
    requirements: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    preferences: list[dict[str, Any]] = []
    for name, depts in parsed.named_masses:
        if len(depts) == 1 and depts[0] not in parsed.pin_ground:
            requirements.append(
                {"kind": "requirement", "lever": "alone", "text": name, "departments": list(depts)}
            )
        elif len(depts) >= 2 and not set(depts) <= set(parsed.pin_ground):
            requirements.append(
                {
                    "kind": "requirement",
                    "lever": "same_mass",
                    "text": name,
                    "departments": list(depts),
                }
            )
    if parsed.mass_count:
        requirements.append(
            {"kind": "requirement", "lever": "mass_count", "text": "", "value": parsed.mass_count}
        )
    for dept in parsed.double_height_departments:
        requirements.append(
            {"kind": "requirement", "lever": "double_height", "text": "", "departments": [dept]}
        )
    if parsed.constraints.get("length_limit_is_cap") and parsed.constraints.get("max_building_length_ft"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "max_length",
                "text": "",
                "value": parsed.constraints["max_building_length_ft"],
                "unit": "ft",
            }
        )
    if parsed.max_stories:
        limitations.append(
            {"kind": "limitation", "lever": "max_stories", "text": "", "value": parsed.max_stories}
        )
    for dept in parsed.pin_ground:
        preferences.append(
            {"kind": "preference", "lever": "pin_ground", "text": "", "departments": [dept]}
        )
    if parsed.constraints.get("preferred_length_ft"):
        preferences.append(
            {
                "kind": "preference",
                "lever": "preferred_length",
                "text": "",
                "value": parsed.constraints["preferred_length_ft"],
                "unit": "ft",
            }
        )
    if parsed.length_over_width:
        preferences.append(
            {
                "kind": "preference",
                "lever": "ratio",
                "text": "",
                "value": parsed.length_over_width,
            }
        )
    return {
        "requirements": requirements,
        "limitations": limitations,
        "preferences": preferences,
    }


def apply_classified_clauses(parsed: ParsedBrief, reading: Any) -> dict[str, list[dict[str, Any]]]:
    """
    Write the model's classified clauses onto the brief.

    Requirements lock organization. Limitations become caps. Preferences are
    recorded and may be satisfied in more than one way.
    """
    clauses = list(getattr(reading, "clauses", []) or [])
    for clause in clauses:
        kind = clause.get("kind")
        lever = clause.get("lever")
        depts = list(clause.get("departments") or [])
        value = clause.get("value")
        if kind == "requirement":
            if lever in {"same_mass", "keep_together"} and len(depts) >= 2:
                for other in depts[1:]:
                    pair = (depts[0], other)
                    if pair not in parsed.keep_together and (other, depts[0]) not in parsed.keep_together:
                        parsed.keep_together.append((depts[0], other))
            elif lever == "alone":
                for dept in depts:
                    if not any(dept in mass_depts for _, mass_depts in parsed.named_masses):
                        parsed.named_masses.append((dept.title(), [dept]))
            elif lever == "mass_count" and value and parsed.mass_count is None:
                parsed.mass_count = max(1, int(value))
            elif lever == "double_height":
                for dept in depts:
                    if dept not in parsed.double_height_departments:
                        parsed.double_height_departments.append(dept)
            elif lever == "exact_length" and value and "exact_building_length_ft" not in parsed.constraints:
                parsed.constraints["exact_building_length_ft"] = float(value)
        elif kind == "limitation":
            if lever == "max_length" and value and "max_building_length_ft" not in parsed.constraints:
                parsed.constraints["max_building_length_ft"] = float(value)
                parsed.constraints["length_limit_is_cap"] = 1
                parsed.pair_length_ft = None
            elif lever == "max_height" and value and parsed.max_stories is None:
                parsed.max_stories = max(1, int(value))
            elif lever in {"site_length", "max_total_length"} and value:
                if "max_building_length_ft" not in parsed.constraints:
                    parsed.constraints["max_total_length_ft"] = float(value)
            elif lever == "max_width" and value and "max_building_width_ft" not in parsed.constraints:
                parsed.constraints["max_building_width_ft"] = float(value)
            elif lever == "max_stories" and value and parsed.max_stories is None:
                parsed.max_stories = max(1, int(value))
        elif kind == "preference":
            if lever == "pin_ground":
                for dept in depts:
                    if dept not in parsed.pin_ground:
                        parsed.pin_ground.append(dept)
            elif lever == "ratio" and parsed.length_over_width is None:
                length = clause.get("length")
                width = clause.get("width")
                if length and width:
                    parsed.length_over_width = float(length) / float(width)
            elif lever == "low_rise":
                parsed.preference = "low_rise"
    briefing = briefing_from_parsed(parsed)
    if clauses:
        briefing = {
            "requirements": [c for c in clauses if c.get("kind") == "requirement"] or briefing["requirements"],
            "limitations": [c for c in clauses if c.get("kind") == "limitation"] or briefing["limitations"],
            "preferences": [c for c in clauses if c.get("kind") == "preference"] or briefing["preferences"],
        }
    parsed.notes.append(
        "roles: "
        f"{len(briefing['requirements'])} requirements, "
        f"{len(briefing['limitations'])} limitations, "
        f"{len(briefing['preferences'])} preferences"
    )
    return briefing


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
    if (
        site
        and "max_total_length_ft" not in parsed.constraints
        and "max_building_length_ft" not in parsed.constraints
    ):
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


def _prefer_stated_wings(parsed: ParsedBrief, reading: Any) -> Any:
    """The sentence's named wings win over a later model regroup."""
    if not parsed.named_masses and parsed.pair_length_ft is None:
        return reading
    if reading is None:
        from .reading import DesignReading

        reading = DesignReading()
    if parsed.named_masses:
        reading.masses = list(parsed.named_masses)
    if parsed.constraints.get("max_building_length_ft"):
        reading.pair_length_ft = None
    elif parsed.pair_length_ft:
        reading.pair_length_ft = parsed.pair_length_ft
    return reading


def _mark_stated_double_height(session: StudySession, text: str) -> list[str]:
    if not re.search(r"double[\s-]?height", text or "", flags=re.I):
        return []
    from .tools import mark_double_height

    notes: list[str] = []
    lowered = (text or "").lower()
    for room in session.program.rooms:
        name = room.room_name.lower()
        if name in lowered or ("gym" in lowered and "gym" in name):
            mark_double_height(session, room.room_name)
            notes.append(f"{room.room_name} is double-height")
    return notes


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
    briefing = apply_classified_clauses(parsed, reading)
    assign_open_departments(parsed, reading)
    reading = _prefer_stated_wings(parsed, reading)
    session.constraints["briefing"] = briefing

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
    reading_notes.extend(_mark_stated_double_height(session, parsed.text))
    if parsed.length_over_width:
        session.constraints["length_over_width"] = parsed.length_over_width
    if parsed.double_height_departments:
        session.constraints["double_height_departments"] = list(
            parsed.double_height_departments
        )
    if parsed.pin_ground:
        from .tools import pin_department_to_floor

        for dept in parsed.pin_ground:
            pin_department_to_floor(session, dept, 0)
            reading_notes.append(f"pinned {dept} to ground")
    story_lock: dict[str, int] = {}
    dh = set(parsed.double_height_departments)
    grounded = set(parsed.pin_ground)
    for mass in session.masses:
        depts = set(mass.departments)
        if depts and depts <= dh:
            mass.story_count = 2
            story_lock[mass.id] = 2
        elif depts and depts <= grounded:
            # Only if the whole mass is ground-floor programs. Sharing a mass
            # with other programs does not force that mass to one story.
            mass.story_count = 1
            story_lock[mass.id] = 1
        elif parsed.max_stories:
            mass.story_count = min(mass.story_count or parsed.max_stories, parsed.max_stories)
    if story_lock:
        session.constraints["story_lock"] = story_lock
    for dept in parsed.double_height_departments:
        for room in session.program.rooms:
            if room.department == dept:
                from .tools import mark_double_height

                mark_double_height(session, room.room_name)
                break
    if parsed.named_masses or parsed.keep_together or parsed.mass_count:
        session.brief_locked = True
        session.save()

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
        "briefing": session.constraints.get("briefing"),
        "instruction": (
            "The brief is already split into requirements, limitations, and "
            "preferences. Execute requirements. Check limitations as caps; do not "
            "fill them. Preferences may be met in more than one way, and a floor "
            "preference is not a mass. Report each role, then the scheme and every "
            "failed check. Do not regroup a required wing."
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
