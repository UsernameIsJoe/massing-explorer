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

_NUM = r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
# Digits or common spelled lengths/sizes (wording patterns; not hard-coded call sites).
_SPELLED_LEN = (
    r"(?:fourteen|fifteen|sixteen|eighteen|twenty(?:-five|-two)?|"
    r"thirty(?:-five)?|forty(?:-five)?|fifty(?:-five)?|sixty(?:-five)?|"
    r"seventy|eighty|ninety|hundred)"
)
_NUM_OR_WORD = rf"({_NUM[1:-1]}|{_SPELLED_LEN})"
_FT = r"(?:\s*(?:ft|feet|foot|'|′))?"
_UNIT = r"(?:\s*(ft|feet|foot|'|′|m|meter|meters|metre|metres))?"
_LEN_UNIT = r"(?:\s*(ft|feet|foot|'|′|m|meter|meters|metre|metres))?"
_AREA_UNIT = (
    r"(?:\s*(sq\.?\s*ft|sqft|sf|square\s*feet|square\s*foot|"
    r"sq\.?\s*m|sq\s*m|sqm|m\u00b2|m2|square\s*meters|square\s*metres))?"
)
_METERS_TO_FEET = 3.280839895
_SQM_TO_SF = 10.76391041671
_STORY_HEIGHT_FT = 14.0
_MASS_NOUN = (
    r"(?:massing\s+(?:elements|blocks|pieces|forms)|pieces\s+of\s+massing|"
    r"built\s+(?:forms|volumes)|building\s+(?:forms|volumes)|standalone\s+forms|"
    r"chunky\s+masses|small\s+(?:structures|blocks|pavilions)|"
    r"(?:primary\s+|main\s+)?(?:masses|buildings|volumes|blocks|pavilions|wings|boxes|structures)|"
    r"slabs|bars|cubes|forms|pieces|towers|elements)"
)
_LEVEL_NOUN = (
    r"(?:stor(?:y|ies|eys)|floors?|levels?|layers?(?:\s+above\s+grade)?"
    r"|(?:layers|levels)\s+of\s+occupied\s+space|occupied\s+levels?)"
)
_WORD_COUNTS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twelve": 12,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "eighteen": 18,
    "twenty": 20,
    "twenty-two": 22,
    "twenty-five": 25,
    "thirty": 30,
    "thirty-five": 35,
    "forty": 40,
    "forty-five": 45,
    "fifty": 50,
    "fifty-five": 55,
    "sixty": 60,
    "sixty-five": 65,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}
_WORD = (
    r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|"
    r"fourteen|fifteen|sixteen|eighteen|twenty(?:-five|-two)?|"
    r"thirty(?:-five)?|forty(?:-five)?|fifty(?:-five)?|sixty(?:-five)?|"
    r"seventy|eighty|ninety|hundred|\d+)"
)


@dataclass
class ParsedBrief:
    text: str
    keep_together: list[tuple[str, str]] = field(default_factory=list)
    keep_apart: list[tuple[str, str]] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    max_stories: int | None = None
    named_masses: list[tuple[str, list[str]]] = field(default_factory=list)
    pair_length_ft: float | None = None
    mass_count: int | None = None
    mass_count_min: int | None = None
    mass_count_max: int | None = None
    pin_ground: list[str] = field(default_factory=list)
    free_departments: list[str] = field(default_factory=list)
    open_slots: int | None = None
    length_over_width: float | None = None
    double_height_departments: list[str] = field(default_factory=list)
    # Scoped size clauses: dept/mass/site × width|depth|length|height × exact|max|min|preferred.
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    preference: str = "balanced"
    unmatched: list[str] = field(default_factory=list)
    unknown_programs: list[str] = field(default_factory=list)
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
            "mass_count": self.mass_count,
            "mass_count_min": self.mass_count_min,
            "mass_count_max": self.mass_count_max,
            "preference": self.preference,
            "unmatched": self.unmatched,
            "unknown_programs": list(self.unknown_programs),
            "pin_ground": list(self.pin_ground),
            "free_departments": list(self.free_departments),
            "open_slots": self.open_slots,
            "dimensions": list(self.dimensions),
            "double_height_departments": list(self.double_height_departments),
            "length_over_width": self.length_over_width,
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
                "max_height_ft",
                "max_gfa_sf",
            )
        )


def _parse_num(token: str) -> float:
    t = str(token).strip().lower().replace(",", "")
    if t in _WORD_COUNTS:
        return float(_WORD_COUNTS[t])
    return float(t)


def _word_or_digit(token: str) -> int | None:
    t = (token or "").lower().strip()
    if t in _WORD_COUNTS:
        return _WORD_COUNTS[t]
    if re.fullmatch(r"\d+", t):
        return int(t)
    return None


def _first_number(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            if match.lastindex and match.lastindex >= 1:
                return _parse_num(match.group(1))
            token = re.search(
                rf"{_NUM}|one|two|three|four|five|six|seven|eight|nine|ten|twelve",
                match.group(0),
                flags=re.I,
            )
            if token:
                return _parse_num(token.group(0))
    return None


def _to_feet(value: float, unit: str | None) -> float:
    if not unit:
        return value
    u = unit.lower().replace("′", "'")
    if u.startswith("m"):
        return value * _METERS_TO_FEET
    return value


def _to_sf(value: float, unit: str | None) -> float:
    if not unit:
        return value
    u = unit.lower().replace("²", "2").replace("\u00b2", "2").replace(" ", "")
    if u in {"sqm", "m2"} or (u.startswith("squaremetre") or u.startswith("squaremeter")):
        return value * _SQM_TO_SF
    if "m" in u and "ft" not in u and "sf" not in u and "foot" not in u and "feet" not in u:
        return value * _SQM_TO_SF
    return value


def _first_length(
    patterns: list[str],
    text: str,
    *,
    skip_spans: list[tuple[int, int]] | None = None,
) -> float | None:
    """Read a length. Meters are converted to feet. A bare number stays feet."""
    spans = skip_spans or []

    def _overlaps(start: int, end: int) -> bool:
        return any(a <= start < b or a < end <= b or start <= a < end for a, b in spans)

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            if _overlaps(match.start(), match.end()):
                continue
            after = text[match.end() : match.end() + 8].lstrip().lower()
            if after.startswith("%") or after.startswith("percent"):
                continue
            if "%" in match.group(0) or re.search(r"\bpercent", match.group(0), flags=re.I):
                continue
            unit = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            return _to_feet(_parse_num(match.group(1)), unit)
    return None


def _scoped_length_max_spans(parsed: ParsedBrief, text: str) -> list[tuple[int, int]]:
    """Spans already captured as a department-targeted length cap."""
    spans: list[tuple[int, int]] = []
    lowered = (text or "").lower()
    for clause in parsed.dimensions:
        if clause.get("lever") != "length" or not clause.get("departments"):
            continue
        snippet = str(clause.get("text") or "").strip()
        if not snippet:
            continue
        start = lowered.find(snippet.lower())
        if start >= 0:
            spans.append((start, start + len(snippet)))
    return spans


def _first_area(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            # Require an area unit so story counts are not treated as sqft.
            span = match.group(0)
            if not re.search(
                r"sq\.?\s*ft|sqft|\bsf\b|square\s*feet|square\s*foot|"
                r"sq\.?\s*m|sqm|m\u00b2|m2|square\s*meters|square\s*metres",
                span,
                flags=re.I,
            ):
                continue
            unit = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            return _to_sf(_parse_num(match.group(1)), unit)
    return None



def _g_plus_to_stories(n: int) -> int:
    """G+2 / ground+2 means ground plus two upper floors → 3 occupied levels."""
    return max(1, int(n) + 1)


def _set_max_stories(
    parsed: ParsedBrief,
    value: int,
    *,
    note: str | None = None,
    source_text: str | None = None,
    role: str | None = None,
) -> None:
    value = max(1, int(value))
    parsed.max_stories = value if parsed.max_stories is None else min(parsed.max_stories, value)
    if role or source_text:
        parsed.constraints["story_role"] = role or _role_from_words(
            source_text or "", default="limitation"
        )
    elif "story_role" not in parsed.constraints:
        parsed.constraints["story_role"] = "limitation"
    if note:
        parsed.notes.append(note)
    else:
        parsed.notes.append(f"max stories {parsed.max_stories}")


def _extract_vertical_limits(
    parsed: ParsedBrief,
    text: str,
    height_implied_stories: int | None,
) -> None:
    """Floors / storeys / levels / layers / G+n language."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")

    # Hard ceiling first — "no occupied layer above level 5"
    hard = re.search(
        rf"(?:no\s+occupied\s+layer\s+should\s+rise\s+above|rise\s+above|"
        rf"not\s+(?:rise|go)\s+above|nothing\s+(?:above|over))\s+"
        rf"(?:level\s+)?({_WORD}|\d+)"
        rf"|above\s+level\s+(\d+)"
        rf"|no\s+occupied\s+layer\s+should\s+rise\s+above\s+level\s+(\d+)",
        text,
        flags=re.I,
    )
    hard_max: int | None = None
    if hard:
        token = next((g for g in hard.groups() if g), None)
        hard_max = _word_or_digit(str(token)) if token else None
        if hard_max is None and token and re.fullmatch(r"\d+", str(token)):
            hard_max = int(token)
        if hard_max:
            parsed.constraints["hard_max_stories"] = float(hard_max)
            parsed.max_stories = hard_max
            parsed.constraints["story_role"] = _role_from_words(
                hard.group(0), default="limitation"
            )
            parsed.notes.append(f"hard max stories {hard_max} (level cap)")

    # Soft story ranges / preferences: "2–3 floors", "keep most boxes to only 2–3"
    range_m = re.search(
        rf"(?:range\s+from|vary\s+between|between)\s+({_WORD})\s+and\s+({_WORD})\s+{_LEVEL_NOUN}"
        rf"|only\s+({_WORD})\s*(?:[–\-—/]|\sto\s+)\s*({_WORD})\s+{_LEVEL_NOUN}"
        rf"|({_WORD})\s*(?:[–\-—/]|\sto\s+)\s*({_WORD})\s+{_LEVEL_NOUN}"
        rf"|mix\s+of\s+({_WORD})-level,\s*({_WORD})-level(?:,\s*and\s+({_WORD})-level)?"
        rf"|mix\s+of\s+({_WORD})-floor\s+and\s+({_WORD})-floor"
        rf"|only\s+({_WORD})\s+or\s+({_WORD})\s+stories?\s+tall"
        rf"|({_WORD})\s+or\s+({_WORD})\s+(?:levels|floors|stories|storeys)",
        text,
        flags=re.I,
    )
    soft_preference = bool(
        re.search(
            r"if\s+you\s+can|prefer|preferably|ideally|most\s+boxes|keep\s+most|"
            r"without\s+sacrificing|would\s+be\s+nice",
            text,
            flags=re.I,
        )
    )
    if range_m:
        nums = [_word_or_digit(g) for g in range_m.groups() if g]
        nums = [n for n in nums if n]
        if len(nums) >= 2:
            lo, hi = min(nums), max(nums)
            parsed.constraints["stories_min"] = float(lo)
            parsed.constraints["stories_max"] = float(hi)
            parsed.notes.append(f"story preference range {lo}-{hi}")
            # Soft preference must not override a harder legal ceiling.
            if hard_max is None and not soft_preference:
                _set_max_stories(
                    parsed, hi, note=f"story range {lo}-{hi}", source_text=range_m.group(0)
                )
            elif hard_max is None and soft_preference and parsed.max_stories is None:
                _set_max_stories(
                    parsed,
                    hi,
                    note=f"preferred stories up to {hi}",
                    source_text=range_m.group(0),
                    role="preference",
                )
                parsed.constraints["stories_min"] = float(lo)
                parsed.constraints["stories_max"] = float(hi)

    # G+n / ground + n / ground plus n (typical = preferred; taller G+ as exception)
    g_matches = list(
        re.finditer(
            rf"(?:mostly\s+)?(?:G|ground)\s*(?:\+|plus)\s*({_NUM})",
            text,
            flags=re.I,
        )
    )
    if g_matches:
        vals = [_g_plus_to_stories(int(_parse_num(m.group(1)))) for m in g_matches]
        typical = vals[0]
        taller = max(vals)
        if hard_max is None:
            _set_max_stories(parsed, typical, note=f"typical G+ → {typical} stories")
        else:
            parsed.constraints["stories_min"] = float(min(typical, hard_max))
            parsed.notes.append(f"typical G+ → {typical} stories (under hard cap {hard_max})")
        if taller > typical:
            parsed.constraints["max_stories_exception"] = float(
                min(taller, hard_max) if hard_max else taller
            )
            parsed.notes.append(f"taller exception {taller} stories")

    # "two 3-storey blocks and two 4-storey blocks" → max
    dual = re.search(
        rf"{_NUM}\s*-?\s*storeys?\w*.{{0,60}}?{_NUM}\s*-?\s*storeys?",
        text,
        flags=re.I,
    )
    if dual and hard_max is None:
        a, b = int(_parse_num(dual.group(1))), int(_parse_num(dual.group(2)))
        _set_max_stories(parsed, max(a, b), note=f"storey mix up to {max(a, b)}")

    stories = _first_number(
        [
            rf"all\s+masses\s+max(?:imum)?\s+stor(?:y|ies|eys)?\s+is\s+{_NUM}",
            rf"max(?:imum)?\s+stor(?:y|ies|eys)?\s+is\s+{_NUM}",
            rf"max(?:imum)?\s+stor(?:y|ies|eys)?\s*(?:of|count|=|:)?\s*{_NUM}",
            rf"(?:height|storey|story)\s+limit\s*(?:is|of|=|:)?\s*{_NUM}",
            rf"limit(?:ed)?\s+(?:height|to)\s*{_NUM}\s*(?:floor|stor|level)",
            rf"no\s+more\s+than\s*{_NUM}\s*(?:floor|stor|level)",
            rf"no\s+(?:form|piece|cube|mass|building|block|volume)\s+should\s+have\s+more\s+than\s*{_NUM}\s+(?:levels?|floors?|stories?|storeys?)",
            rf"(?:each|every|any|no)\s+(?:mass|wing|building|bar|volume|block|piece)\s+"
            rf"(?:should\s+|must\s+|can\s+|may\s+)?"
            rf"(?:have\s+)?(?:no\s+more\s+than|not\s+have\s+more\s+than|not\s+exceed)\s+"
            rf"({_WORD}|{_NUM})\s+(?:levels?|floors?|stories?|storeys?)",
            rf"(?:each|every|any|shared|their|that|the)\s+(?:mass|wing|building|volume|block)?\s*"
            rf"(?:should\s+|must\s+)?"
            rf"(?:be\s+)?(?:no\s+taller\s+than|not\s+taller\s+than|no\s+higher\s+than)\s+"
            rf"({_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?|levels?)",
            rf"no\s+taller\s+than\s+({_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?|levels?)",
            rf"(?:all|each|every)\s+(?:masses|mass|buildings?|volumes?|blocks?)\s+"
            rf"(?:should\s+be\s+|must\s+be\s+)?({_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?)\s+or\s+less",
            rf"({_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?)\s+or\s+(?:less|fewer|below)",
            rf"(?:should\s+not|must\s+not|cannot|can't)\s+exceed\s+({_WORD}|{_NUM})\s+"
            rf"(?:stories?|storeys?|floors?|levels?)",
            rf"not\s+exceed\s+({_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?|levels?)",
            rf"stay\s+at\s+({_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?)\s+or\s+below",
            rf"(?:each|every|any)\s+(?:with\s+)?no\s+more\s+than\s+({_WORD})\s+occupied\s+levels?",
            rf"no\s+more\s+than\s+({_WORD})\s+occupied\s+levels?",
            rf"to\s+({_WORD})\s+storeys?\s+or\s+fewer",
            rf"keep\s+every\s+mass\s+to\s+({_WORD})\s+storeys?\s+or\s+fewer",
            rf"don'?t\s+let\s+any\s+single\s+(?:volume|cube|box|form)\s+exceed\s+({_WORD})\s+layers?",
            rf"don'?t\s+make\s+any\s+(?:cube|box|form)\s+more\s+than\s+({_WORD})\s+levels?(?:\s+above\s+ground)?",
            rf"up\s+to\s*{_NUM}\s*(?:floor|stor|level)",
            rf"{_NUM}\s*(?:floor|stor(?:y|ies)|levels?)(?:s)?\s+max",
            rf"(?:do\s+not|don't)\s+go\s+above\s+({_WORD})\s+(?:floor|stor|level)",
            rf"(?:remain|stay)\s+under\s+({_WORD})\s+{_LEVEL_NOUN}",
            rf"under\s+({_WORD})\s+{_LEVEL_NOUN}",
            rf"most\s+of\s+the\s+project\s+to\s+({_WORD})\s+(?:stories?|levels?|floors?)",
            rf"try\s+to\s+keep\s+most\s+of\s+the\s+project\s+to\s+({_WORD})\s+(?:stories?|levels?|floors?)",
            rf"no\s+more\s+than\s+({_WORD})\s+floors?",
            rf"stay\s+around\s+({_WORD})\s+stories?",
            rf"around\s+({_WORD})\s+stories?\s+if\s+possible",
            rf"buildings\s+to\s+stay\s+around\s+({_WORD})\s+stories?",
            rf"keep\s+the\s+(?:boxes|wings|buildings|masses|structures)\s+"
            rf"(?:to\s+about|around)\s+({_WORD})\s+(?:layers?|floors?|stories?|levels?)",
            rf"about\s+({_WORD})\s+layers?\s+above\s+grade",
            rf"around\s+({_WORD})\s+floors?\s+tall",
            rf"keep\s+the\s+wings\s+around\s+({_WORD})\s+floors?\s+tall",
            rf"mostly\s+({_WORD})-floor",
            rf"imagining\s+mostly\s+({_WORD})-floor",
            rf"ideally\s+({_WORD})\s+storeys?\s+each",
            rf"stay\s+below\s+({_WORD})\s+(?:and\s+)?(?:floors?|stories?|levels?)",
            rf"should\s+stay\s+below\s+({_WORD})\s+(?:and\s+)?(?:floors?|stories?|levels?)",
            rf"other\s+two\s+should\s+stay\s+below\s+({_WORD})",
            rf"tallest\s+to\s+({_WORD})\s+levels?\s+above\s+grade",
            rf"keep\s+the\s+tallest\s+to\s+({_WORD})\s+levels?",
            rf"podium\s+to\s+({_WORD})\s+floors?",
            rf"keep\s+the\s+podium\s+to\s+({_WORD})\s+floors?",
            rf"roughly\s+({_WORD})\s+floors?",
            rf"or\s+roughly\s+({_WORD})\s+floors?",
            rf"stay\s+at\s+({_WORD})\s+storeys?",
            rf"most\s+of\s+the\s+massing\s+should\s+stay\s+at\s+({_WORD})\s+storeys?",
            rf"should\s+stay\s+at\s+({_WORD})\s+storeys?",
            rf"most\s+(?:of\s+them|buildings|blocks|pieces)\s+should\s+"
            rf"(?:stay\s+)?(?:around|under|at)\s+({_WORD})\s+(?:or\s+({_WORD})\s+)?"
            rf"(?:floors?|stories?|levels?|storeys?)",
            rf"should\s+stay\s+around\s+({_WORD})\s+stories?"
            rf"(?:\s+with\s+maybe\s+one\s+taller)?",
            rf"ideally\s+all\s+masses\s+stay\s+at\s+({_WORD}|{_NUM})\s+"
            rf"(?:stories?|storeys?|floors?)\s+or\s+below",
            rf"(?:i(?:'d| would)\s+prefer|prefer)\s+most\s+(?:of\s+them|masses)\s+to\s+be\s+"
            rf"({_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?)",
            rf"({_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?)\s+would\s+be\s+preferable",
        ],
        text,
    )
    story_clause = None
    if stories is None:
        word_s = re.search(
            rf"(?:under|above|below|to|around|about|roughly)\s+"
            rf"(one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+"
            rf"(?:floor|stor|storey|level|layer)",
            text,
            flags=re.I,
        )
        if word_s:
            stories = float(_WORD_COUNTS[word_s.group(1).lower()])
            story_clause = word_s.group(0)
    if story_clause is None and stories is not None:
        # Recover the clause that produced the numeric story cap.
        for pattern in [
            rf"[^.!?]{{0,40}}no\s+taller\s+than\s+(?:{_WORD}|{_NUM})\s+(?:stories?|storeys?|floors?|levels?)",
            rf"[^.!?]{{0,40}}(?:stories?|storeys?|floors?)\s+or\s+(?:less|fewer|below)",
            rf"[^.!?]{{0,40}}(?:should\s+not|must\s+not|cannot|can't|not)\s+exceed\s+(?:{_WORD}|{_NUM})\s+"
            rf"(?:stories?|storeys?|floors?|levels?)",
            rf"[^.!?]{{0,40}}no\s+more\s+than\s+(?:{_WORD}|{_NUM})\s+(?:floors?|stories?|levels?|storeys?)",
            rf"[^.!?]{{0,40}}have\s+more\s+than\s+(?:{_WORD}|{_NUM})\s+(?:floors?|stories?|levels?)",
            rf"all\s+masses\s+max(?:imum)?\s+stor(?:y|ies|eys)?\s+is\s+{_NUM}",
            rf"max(?:imum)?\s+stor(?:y|ies|eys)?\s+is\s+{_NUM}",
            rf"max(?:imum)?\s+stor(?:y|ies|eys)?\s*(?:of|count|=|:)?\s*{_NUM}",
            rf"around\s+({_WORD})\s+stories?\s+if\s+possible",
            rf"ideally\s+({_WORD})\s+storeys?\s+each",
            rf"prefer(?:ably|red)?\s+(?:about\s+|around\s+)?({_WORD})\s+(?:to\s+({_WORD})\s+)?(?:stories?|floors?|levels?)",
        ]:
            m = re.search(pattern, text, flags=re.I)
            if m:
                story_clause = m.group(0)
                break
    if story_clause is None:
        for pattern in [
            rf"all\s+masses\s+max(?:imum)?\s+stor(?:y|ies|eys)?\s+is\s+{_NUM}",
            rf"max(?:imum)?\s+stor(?:y|ies|eys)?\s+is\s+{_NUM}",
            rf"max(?:imum)?\s+stor(?:y|ies|eys)?\s*(?:of|count|=|:)?\s*{_NUM}",
            rf"around\s+({_WORD})\s+stories?\s+if\s+possible",
            rf"ideally\s+({_WORD})\s+storeys?\s+each",
            rf"prefer(?:ably|red)?\s+(?:about\s+|around\s+)?({_WORD})\s+(?:to\s+({_WORD})\s+)?(?:stories?|floors?|levels?)",
        ]:
            m = re.search(pattern, text, flags=re.I)
            if m:
                story_clause = m.group(0)
                break
    if stories is not None and hard_max is None:
        # Role from the story clause words only — never from other brief numbers/words.
        # Stay inside the same sentence so prior "must/have to" clauses do not leak.
        role_src = story_clause or "maximum"
        if story_clause:
            idx = text.lower().find(story_clause.lower())
            if idx >= 0:
                role_src = _sentence_at(text, idx, idx + len(story_clause))
        role = _role_from_words(role_src, default="limitation")
        _set_max_stories(
            parsed, int(stories), source_text=role_src, role=role
        )
    elif stories is not None and hard_max is not None:
        # Soft numeric preference under a hard cap.
        parsed.constraints.setdefault("stories_max", float(min(int(stories), hard_max)))
        parsed.constraints["story_role"] = "preference"
        parsed.notes.append(f"preferred stories around {int(stories)} (hard cap {hard_max})")
    elif hard_max is None and (
        hit := re.search(rf"max(?:imum)?\s+{_NUM}\s*(?:stor|level|floor)", text, flags=re.I)
    ):
        _set_max_stories(
            parsed,
            int(_parse_num(hit.group(1))),
            source_text=hit.group(0),
            role="limitation",
        )

    # Preferred height target (not a hard cap): "each mass prefer to be 2 floors"
    pref_n: int | None = None
    pref_clause = None
    for pattern in [
        rf"(?:each|every|any)\s+(?:mass|wing|building|bar|volume|block)\s+"
        rf"prefer(?:s|ably|red)?\s+(?:to\s+be\s+|be\s+|at\s+|around\s+|about\s+)?"
        rf"({_WORD})\s+{_LEVEL_NOUN}",
        rf"prefer(?:s|ably|red)?\s+(?:each|every|any)\s+(?:mass|wing|building|bar|volume|block)\s+"
        rf"(?:to\s+be\s+|be\s+|at\s+|around\s+|about\s+)?({_WORD})\s+{_LEVEL_NOUN}",
        rf"(?:each|every|any)\s+(?:mass|wing|building|bar)\s+"
        rf"(?:should\s+)?(?:ideally|preferably)\s+(?:be\s+|at\s+|around\s+|about\s+)?"
        rf"({_WORD})\s+{_LEVEL_NOUN}",
        rf"i(?:'d| would)\s+like\s+(?:each|every)\s+(?:mass|wing|building)\s+"
        rf"(?:to\s+be\s+)?({_WORD})\s+{_LEVEL_NOUN}",
        rf"prefer(?:ably|red)?\s+(?:to\s+be\s+|be\s+)?({_WORD})\s+(?:stories?|floors?|levels?)"
        rf"(?:\s+(?:each|tall|high))?",
        rf"(?:i(?:'d| would)\s+prefer|prefer)\s+(?:most\s+(?:of\s+them|masses)\s+to\s+be\s+)?"
        rf"({_WORD})\s+(?:stories?|floors?|levels?)",
        rf"({_WORD})\s+(?:stories?|floors?|levels?)\s+would\s+be\s+preferable",
        rf"ideally\s+all\s+(?:masses|buildings)\s+stay\s+at\s+({_WORD})\s+"
        rf"(?:stories?|floors?)\s+or\s+below",
    ]:
        m = re.search(pattern, text, flags=re.I)
        if m:
            pref_n = _word_or_digit(str(m.group(1)))
            if pref_n is None and re.fullmatch(r"[\d.]+", str(m.group(1))):
                pref_n = int(_parse_num(m.group(1)))
            pref_clause = m.group(0)
            break
    if pref_n:
        if hard_max is not None:
            pref_n = min(pref_n, hard_max)
        if parsed.max_stories is not None and str(
            parsed.constraints.get("story_role") or ""
        ) != "preference":
            # Keep the hard/legal ceiling; preference is a separate target.
            pref_n = min(pref_n, int(parsed.max_stories))
        parsed.constraints["preferred_stories"] = float(pref_n)
        parsed.notes.append(f"prefer {pref_n} stories per mass")
        if parsed.max_stories is None and hard_max is None:
            parsed.constraints["story_role"] = "preference"
            parsed.constraints.setdefault("stories_min", float(pref_n))
            parsed.constraints.setdefault("stories_max", float(pref_n))
        elif str(parsed.constraints.get("story_role") or "") == "preference":
            parsed.constraints.setdefault("stories_min", float(pref_n))
            parsed.constraints.setdefault("stories_max", float(pref_n))

    # Minimum stories: "every mass should be at least 2 stories"
    min_story = re.search(
        rf"(?:each|every|any|all)\s+(?:mass|wing|building|volume|block|piece)e?s?\s+"
        rf"(?:should\s+be\s+|must\s+be\s+|shall\s+be\s+)?"
        rf"(?:at\s+least|no\s+fewer\s+than|minimum\s+of)\s+({_WORD}|{_NUM})\s+"
        rf"(?:stories?|storeys?|floors?|levels?)",
        text,
        flags=re.I,
    )
    if not min_story:
        min_story = re.search(
            rf"(?:at\s+least|no\s+fewer\s+than)\s+({_WORD}|{_NUM})\s+"
            rf"(?:stories?|storeys?|floors?|levels?)",
            text,
            flags=re.I,
        )
    if min_story:
        mn = _word_or_digit(str(min_story.group(1)))
        if mn is None and re.fullmatch(r"[\d.]+", str(min_story.group(1) or "")):
            mn = int(_parse_num(min_story.group(1)))
        if mn:
            parsed.constraints["stories_min"] = float(mn)
            parsed.constraints.setdefault(
                "story_role",
                _role_from_words(min_story.group(0), default="limitation"),
            )
            parsed.notes.append(f"min stories {mn}")

    # "step back after the third floor"
    step = re.search(
        r"after\s+the\s+(second|third|fourth|fifth|sixth)\s+floor",
        text,
        flags=re.I,
    )
    if step and hard_max is None:
        n = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}[
            step.group(1).lower()
        ]
        _set_max_stories(parsed, n)

    # Soft / exception ups: "up to 6 stories", "partial fourth floor",
    # "allowed to go up another two levels"
    another = re.search(
        rf"(?:go\s+up\s+)?another\s+({_WORD}|\d+)\s+(?:levels?|floors?|stor(?:y|ies|eys))",
        text,
        flags=re.I,
    )
    if another:
        extra = _word_or_digit(another.group(1))
        if extra is None and re.fullmatch(r"\d+", another.group(1)):
            extra = int(another.group(1))
        base = parsed.max_stories or int(parsed.constraints.get("stories_max") or 0) or None
        if extra and base:
            soft_n = base + extra
            if hard_max is not None:
                soft_n = min(soft_n, hard_max)
            parsed.constraints["max_stories_exception"] = float(soft_n)
            parsed.notes.append(f"story exception up to {soft_n} (+{extra} levels)")

    soft = re.search(
        rf"(?:could\s+go\s+)?up\s+to\s+({_WORD})\s+(?:floor|stor|level)|"
        rf"(?:one|may)\s+(?:may\s+)?have\s+a\s+partial\s+(fourth|fifth|sixth|third|{_NUM})\s+floor|"
        rf"allowed\s+to\s+reach\s+(?:ground\s*\+\s*|{_WORD}\s+)?"
        rf"(?:G\s*\+\s*)?({_NUM})|"
        rf"one\s+taller\s+(?:G\s*\+\s*|ground\s*\+\s*)({_NUM})",
        text,
        flags=re.I,
    )
    if soft:
        token = next((g for g in soft.groups() if g), None)
        soft_n = _word_or_digit(str(token)) if token else None
        if soft_n is None and token and re.fullmatch(r"[\d.]+", str(token)):
            soft_n = int(_parse_num(token))
        if soft_n is None and token:
            soft_n = {
                "third": 3,
                "fourth": 4,
                "fifth": 5,
                "sixth": 6,
            }.get(str(token).lower())
        if soft_n:
            if re.search(r"G\s*\+|ground\s*\+", soft.group(0), flags=re.I):
                soft_n = _g_plus_to_stories(soft_n)
            if hard_max is not None:
                soft_n = min(soft_n, hard_max)
            parsed.constraints["max_stories_exception"] = float(soft_n)
            if parsed.max_stories is None:
                _set_max_stories(parsed, soft_n, note=f"max stories {soft_n} (upper exception)")
            else:
                parsed.notes.append(f"story exception up to {soft_n}")

    if parsed.max_stories is None and height_implied_stories is not None:
        _set_max_stories(
            parsed,
            height_implied_stories,
            note=f"~{height_implied_stories} stories from height",
        )


_DIM_LEVER = r"(?:width|depth|length|height)"
# Modality cues only — never classify role from digits or spelled numbers.
_DIM_MODE_EXACT = (
    r"(?:has\s+to\s+be|have\s+to\s+be|must\s+be|needs?\s+to\s+be|"
    r"need\s+to\s+maintain|needs?\s+to\s+keep|maintain(?:s|ing)?|"
    r"keep(?:s|ing)?(?:\s+at|\s+to)?|hold(?:s|ing)?(?:\s+at|\s+to)?|"
    r"fixed\s+at|set\s+to|should\s+be|is\s+to\s+be|to\s+be)"
)
_DIM_MODE_MAX = (
    r"(?:no\s+wider\s+than|no\s+deeper\s+than|no\s+longer\s+than|"
    r"not\s+wider\s+than|not\s+deeper\s+than|not\s+(?:be\s+)?longer\s+than|"
    r"should\s+not\s+be\s+longer\s+than|must\s+not\s+be\s+longer\s+than|"
    r"at\s+most|under|below|"
    r"less\s+than|shorter\s+than|cannot\s+exceed|can't\s+exceed|"
    r"max(?:imum)?(?:\s+of)?)"
)
_DIM_MODE_PREF = r"(?:prefer(?:ably|red)?|ideally|about|around|roughly|target|better)"
_DIM_MODE_LINK = r"(?:is|=|:|of)"  # weak link words — not enough alone for a role
_SITE_OR_BUILDING = re.compile(
    r"\b(?:site|building|project|scheme|campus|overall|total)\b",
    flags=re.I,
)
_BAR_NOUN = re.compile(
    r"\b(?:bars?|wings?|masses?|volumes?|blocks?|slabs?|boxes?|forms?)\b",
    flags=re.I,
)

# Role = modality words only. Digits / spelled numbers are values, never the role.
# Canonical cues (user training):
#   requirement: must, needs to, has to, requires, exactly
#   limitation:  should be, cannot exceed, no more than, at least, should stay under
#   preference:  prefer, ideally, would rather, better if, if possible, around, closer to
_ROLE_REQUIRE = re.compile(
    r"\b(?:must(?!\s+not)|shall(?!\s+not)|requires?|required|requirement|"
    r"has\s+to|have\s+to|needs?\s+to|need\s+to|needs?(?!\s+not)|exactly|"
    r"maintain(?:s|ing)?)\b",
    flags=re.I,
)
_ROLE_LIMIT = re.compile(
    r"\b(?:should\s+be|should\s+stay\s+under|should\s+remain|should\s+exceed|"
    r"cannot|can't|must\s+not|shall\s+not|should\s+not|"
    r"no\s+more\s+than|not\s+more\s+than|at\s+least|at\s+most|"
    r"max(?:imum)?|cap(?:ped)?|limit(?:ed|ation)?|"
    r"(?:stay|remain)\s+(?:under|below|within)|"
    r"no\s+longer\s+than|no\s+wider\s+than|not\s+wider\s+than|"
    r"no\s+taller\s+than|not\s+taller\s+than|no\s+higher\s+than|"
    r"not\s+higher\s+than|shorter\s+than|less\s+than|or\s+less|"
    r"go\s+beyond|not\s+go\s+beyond|not\s+over|not\s+exceed|"
    r"narrower\s+than|may\s+(?:not\s+)?be\s+(?:longer|taller|wider|higher)|"
    r"no\s+(?:mass|building|wing|block|volume|form|other)\s+"
    r"(?:can|may|should)\b)",
    flags=re.I,
)
_ROLE_PREFER = re.compile(
    r"\b(?:prefer(?:s|ably|red|ence)?|ideally|ideal|"
    r"would\s+rather|rather|better\s+if|would\s+be\s+better|"
    r"if\s+possible|if\s+the\s+site\s+allows|if\s+space\s+permits|"
    r"if\s+it\s+helps|around|closer\s+to|"
    r"i['’]d\s+like|would\s+like|try\s+to|nice\s+to|preferable|"
    r"could\s+be|"
    r"would\s+be\s+(?:nice|better|ideal|preferable|acceptable))\b",
    flags=re.I,
)
_SPELLED_NUMBER = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)"
    r"(?:-|\s+)?(?:one|two|three|four|five|six|seven|eight|nine)?\b",
    flags=re.I,
)


def _strip_values_for_role(text: str) -> str:
    """Remove digits and spelled numbers so they cannot influence role."""
    t = (text or "").replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"\d[\d,]*(?:\.\d+)?", " ", t)
    t = _SPELLED_NUMBER.sub(" ", t)
    return t


def _role_from_words(
    text: str,
    *,
    default: str = "requirement",
    allow_unknown: bool = False,
    use_memory: bool = True,
) -> str:
    """
    requirement / limitation / preference from modality words only.

    Digits and word-numbers (four, thirty-five) are values, never the role.
    Canonical cues:
      requirement — must, needs to, has to, requires, exactly
      limitation  — should be, cannot exceed, no more than, at least, should stay under
      preference  — prefer, ideally, would rather, better if, if possible, around, closer to
    Also consults cross-project modality memory for learned cues/phrases.
    """
    t = _strip_values_for_role(text)
    has_req = bool(_ROLE_REQUIRE.search(t))
    has_lim = bool(_ROLE_LIMIT.search(t))
    has_pref = bool(_ROLE_PREFER.search(t))

    # Soft preference language outranks a co-occurring bound word.
    if has_pref and not has_req:
        return "preference"
    # "exactly" is a requirement cue and outranks co-occurring "should be".
    if has_req:
        return "requirement"
    if has_lim:
        return "limitation"
    if has_pref:
        return "preference"

    if use_memory:
        try:
            from .modality_memory import role_from_memory

            memorized = role_from_memory(text)
            if memorized:
                return memorized
        except Exception:
            pass

    if allow_unknown:
        return "unknown"
    return default


def _sentence_at(text: str, start: int, end: int | None = None) -> str:
    """Nearest sentence/clause window for role classification (avoids neighbor leakage)."""
    end = len(text) if end is None else end
    left = max(
        text.rfind(".", 0, start),
        text.rfind(";", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
    )
    rights = [i for i in (text.find(c, end) for c in ".;!?") if i >= 0]
    right = min(rights) if rights else len(text)
    return text[left + 1 : right].strip()


def _dimension_mode_from_cue(cue: str) -> str:
    c = (cue or "").lower()
    if re.search(_DIM_MODE_MAX, c, flags=re.I):
        return "max"
    if re.search(_DIM_MODE_PREF, c, flags=re.I):
        return "preferred"
    if re.search(r"at\s+least|no\s+less|minimum|min(?:imum)?", c, flags=re.I):
        return "min"
    if re.search(_DIM_MODE_EXACT, c, flags=re.I):
        return "exact"
    # Bare is/of/= — size link only; role comes from surrounding words.
    if re.fullmatch(_DIM_MODE_LINK, c.strip()):
        return "exact"
    return "exact"


def _append_dimension(
    parsed: ParsedBrief,
    *,
    lever: str,
    mode: str,
    value_ft: float,
    departments: list[str] | None = None,
    scope: str = "building",
    text: str = "",
    kind: str | None = None,
) -> None:
    # Bar/wing "depth" is the short plan dimension (= width in this engine).
    if lever == "depth" and scope != "site":
        lever = "width"
    default_kind = {
        "exact": "requirement",
        "max": "limitation",
        "min": "limitation",
        "preferred": "preference",
    }.get(mode, "requirement")
    role = kind or _role_from_words(text, default=default_kind)
    clause = {
        "kind": role,
        "lever": lever,
        "mode": mode,
        "scope": scope,
        "departments": list(departments or []),
        "value": round(float(value_ft), 4),
        "unit": "ft",
        "text": (text or "").strip(),
    }
    key = (
        clause["lever"],
        clause["mode"],
        clause["scope"],
        tuple(clause["departments"]),
        clause["value"],
        clause["kind"],
    )
    existing = {
        (
            c.get("lever"),
            c.get("mode"),
            c.get("scope"),
            tuple(c.get("departments") or []),
            c.get("value"),
            c.get("kind"),
        )
        for c in parsed.dimensions
    }
    if key in existing:
        return
    parsed.dimensions.append(clause)
    who = ", ".join(clause["departments"]) if clause["departments"] else scope
    parsed.notes.append(
        f"{clause['kind']}: {clause['mode']} {clause['lever']} "
        f"{clause['value']:g} ft ({who})"
    )


def _subject_departments(subject: str, names: list[str]) -> list[str]:
    subject = (subject or "").strip(" ,.;:")
    if not subject or _SITE_OR_BUILDING.search(subject):
        return []
    subject = re.sub(r"^(?:for|the|a|an)\s+", "", subject, flags=re.I)
    subject = re.sub(r"\s+(?:the|its|their)$", "", subject, flags=re.I)
    if _BAR_NOUN.fullmatch(subject.strip()):
        return []
    hits = _clause_departments(subject, names)
    if hits:
        return hits
    one = match_department(subject, names)
    return [one] if one else []


def _extract_scoped_dimensions(
    parsed: ParsedBrief, text: str, names: list[str]
) -> None:
    """
    General (subject × dimension × relation × value) extractor.

    Covers department, bar/wing, and building sizes so width/depth/length are
    not special-cased per phrase. Site-depth-as-frontage stays in the site
    length block.
    """
    raw = text

    patterns: list[tuple[str, str]] = [
        # for core academic the width has to be 80ft
        (
            "dept_for",
            r"(?:for|on)\s+([A-Za-z][A-Za-z0-9&/' \-]{1,48}?)\s+"
            r"(?:the\s+|its\s+|their\s+)?(" + _DIM_LEVER + r")\s+"
            r"(" + _DIM_MODE_EXACT + r"|" + _DIM_MODE_MAX + r"|" + _DIM_MODE_PREF + r"|" + _DIM_MODE_LINK + r")\s*"
            + _NUM + _LEN_UNIT,
        ),
        # core academic width has to be / must be / is 80 ft
        (
            "dept_lever",
            r"([A-Za-z][A-Za-z0-9&/' \-]{1,48}?)\s+"
            r"(?:bar\s+|wing\s+)?(" + _DIM_LEVER + r")\s+"
            r"(" + _DIM_MODE_EXACT + r"|" + _DIM_MODE_MAX + r"|" + _DIM_MODE_PREF + r"|" + _DIM_MODE_LINK + r")\s*"
            + _NUM + _LEN_UNIT,
        ),
        # width of core academic has to be 80ft
        (
            "lever_of_dept",
            r"(" + _DIM_LEVER + r")\s+(?:of|for)\s+([A-Za-z][A-Za-z0-9&/' \-]{1,48}?)\s+"
            r"(" + _DIM_MODE_EXACT + r"|" + _DIM_MODE_MAX + r"|" + _DIM_MODE_PREF + r"|" + _DIM_MODE_LINK + r")\s*"
            + _NUM + _LEN_UNIT,
        ),
        # core academic needs to maintain a width of 85 ft
        (
            "dept_maintain",
            r"([A-Za-z][A-Za-z0-9&/' \-]{1,48}?)\s+"
            r"(?:needs?\s+to\s+|must\s+|should\s+)?"
            r"(?:maintain|keep|hold)\s+(?:a\s+|an\s+|its\s+)?"
            r"(" + _DIM_LEVER + r")\s+(?:of\s+|at\s+|to\s+)?"
            + _NUM + _LEN_UNIT,
        ),
        # Core Academic should maintain an 80 ft width
        (
            "dept_maintain_num_first",
            r"([A-Za-z][A-Za-z0-9&/' \-]{1,48}?)\s+"
            r"(?:needs?\s+to\s+|must\s+|should\s+)?"
            r"(?:maintain|keep|hold)\s+(?:a\s+|an\s+|its\s+)?"
            + _NUM + _LEN_UNIT + r"\s+"
            r"(" + _DIM_LEVER + r")\b",
        ),
        # core academic 80 ft wide / deep
        (
            "dept_adj",
            r"([A-Za-z][A-Za-z0-9&/' \-]{1,48}?)\s+"
            r"(?:at\s+|to\s+|of\s+)?"
            + _NUM + _LEN_UNIT + r"\s+"
            r"(wide|deep|long|tall)\b",
        ),
        # core academic should not be longer than 50 m / gym no longer than 80 ft
        (
            "dept_longer",
            r"(?!each\b|every\b|any\b|all\b)"
            r"([A-Za-z][A-Za-z0-9&/' \-]{0,32}?)"
            r"(?=\s+(?:bar\s+|wing\s+|mass\s+)?"
            r"(?:should\b|must\b|can\b|may\b|no\s+longer|not\s+be\s+longer|longer\s+than))"
            r"\s+"
            r"(?:bar\s+|wing\s+|mass\s+)?"
            r"(?:should\s+|must\s+|can\s+|may\s+)?"
            r"(?:not\s+be\s+|no\s+|not\s+)?"
            r"(?:be\s+)?"
            r"(?:longer\s+than|no\s+longer\s+than)\s*"
            + _NUM + _LEN_UNIT,
        ),
        # bars / wings no wider than 60 ft; each mass should not be longer than 75 m
        (
            "bars_max",
            r"\b((?:each\s+|every\s+|any\s+)?"
            r"(?:bars?|wings?|masses?|volumes?|blocks?|slabs?|boxes?))\s+"
            r"(?:should\s+be\s+|must\s+be\s+|should\s+not\s+be\s+|must\s+not\s+be\s+)?"
            r"(no\s+wider\s+than|not\s+wider\s+than|no\s+longer\s+than|"
            r"not\s+(?:be\s+)?longer\s+than|longer\s+than|"
            r"at\s+most|under|below)\s*"
            + _NUM + _LEN_UNIT,
        ),
        # wing depth 45' / bar width 60 ft
        (
            "bars_exact",
            r"\b((?:bar|wing|mass|volume|block|slab|box))\s+"
            r"(" + _DIM_LEVER + r")\s+"
            r"(?:" + _DIM_MODE_EXACT + r"\s*)?" + _NUM + _LEN_UNIT,
        ),
        # ideally / prefer wider than N ft; not wider than; not narrower than
        (
            "dept_wider",
            r"([A-Za-z][A-Za-z0-9&/' \-]{1,48}?)\s+"
            r"(?:would\s+)?(?:ideally\s+|preferably\s+|prefer(?:ably|red)?\s+)?"
            r"(?:be\s+)?(?:wider|narrower|greater|less)\s+than\s*"
            + _NUM + _LEN_UNIT,
        ),
        (
            "blocks_wider",
            r"\b((?:blocks?|masses?|volumes?|bars?|wings?|buildings?))\s+"
            r"(?:to\s+be\s+|be\s+)?"
            r"(?:wider|narrower)\s+than\s*"
            + _NUM + _LEN_UNIT,
        ),
        (
            "not_wider",
            r"(?:it|its\s+width|the\s+width|width|mass|building|block).{0,40}?"
            r"(?:should\s+not|must\s+not|cannot|can't)\s+be\s+"
            r"(?:wider|narrower)\s+than\s*"
            + _NUM + _LEN_UNIT,
        ),
        (
            "exactly_width",
            r"(?:its\s+|their\s+|the\s+)?width\s+"
            r"(?:should\s+be\s+|must\s+be\s+|is\s+)?exactly\s*"
            + _NUM + _LEN_UNIT,
        ),
        (
            "preferred_width_range",
            r"(?:around|about|roughly|closer\s+to|something\s+around)\s*"
            + _NUM
            + r"(?:\s*[–\-—/to]+\s*"
            + _NUM
            + r")?"
            + _LEN_UNIT
            + r"\s+would\s+be\s+prefer",
        ),
        (
            "preferred_extra_width",
            r"(?:extra\s+width|width).{0,30}?(?:around|about|roughly)\s*"
            + _NUM
            + _LEN_UNIT,
        ),
        (
            "min_block_width",
            r"(?:each|every|any)\s+(?:block|mass|volume|building)\s+"
            r"(?:should\s+be\s+)?at\s+least\s*"
            + _NUM
            + _LEN_UNIT
            + r"\s+wide",
        ),
        (
            "not_below_width",
            r"(?:its\s+|their\s+|the\s+)?(?:width|mass).{0,40}?"
            r"(?:should\s+not|must\s+not|cannot|can't)\s+"
            r"(?:go\s+)?(?:below|under|be\s+narrower\s+than)\s*"
            + _NUM + _LEN_UNIT,
        ),
    ]

    for kind, pattern in patterns:
        for match in re.finditer(pattern, raw, flags=re.I):
            g = match.groups()
            if kind == "dept_for":
                depts = _subject_departments(g[0], names)
                if not depts:
                    continue
                lever, mode_cue, num, unit = g[1], g[2], g[3], g[4]
                _append_dimension(
                    parsed,
                    lever=lever.lower(),
                    mode=_dimension_mode_from_cue(mode_cue),
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department",
                    text=match.group(0),
                )
            elif kind == "dept_lever":
                depts = _subject_departments(g[0], names)
                if not depts:
                    continue
                lever, mode_cue, num, unit = g[1], g[2], g[3], g[4]
                _append_dimension(
                    parsed,
                    lever=lever.lower(),
                    mode=_dimension_mode_from_cue(mode_cue),
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department",
                    text=match.group(0),
                )
            elif kind == "lever_of_dept":
                lever = g[0].lower()
                depts = _subject_departments(g[1], names)
                if not depts:
                    continue
                mode_cue, num, unit = g[2], g[3], g[4]
                _append_dimension(
                    parsed,
                    lever=lever,
                    mode=_dimension_mode_from_cue(mode_cue),
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department",
                    text=match.group(0),
                )
            elif kind == "dept_maintain":
                depts = _subject_departments(g[0], names)
                if not depts:
                    continue
                lever, num, unit = g[1].lower(), g[2], g[3]
                _append_dimension(
                    parsed,
                    lever=lever,
                    mode="exact",
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department",
                    text=match.group(0),
                )
            elif kind == "dept_maintain_num_first":
                depts = _subject_departments(g[0], names)
                if not depts:
                    continue
                num, unit, lever = g[1], g[2], g[3].lower()
                _append_dimension(
                    parsed,
                    lever=lever,
                    mode="exact",
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department",
                    text=match.group(0),
                )
            elif kind == "dept_adj":
                depts = _subject_departments(g[0], names)
                if not depts:
                    continue
                num, unit, adj = g[1], g[2], g[3].lower()
                lever = {
                    "wide": "width",
                    "deep": "depth",
                    "long": "length",
                    "tall": "height",
                }[adj]
                _append_dimension(
                    parsed,
                    lever=lever,
                    mode="exact",
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department",
                    text=match.group(0),
                )
            elif kind == "dept_longer":
                subj = g[0]
                if re.search(
                    r"\b(?:each|every|any|all)\s+(?:mass|wing|bar|building)",
                    subj,
                    flags=re.I,
                ) or re.search(r"\b(?:stories?|floors?|than|more)\b", subj, flags=re.I):
                    continue
                depts = _subject_departments(subj, names)
                if not depts:
                    continue
                num, unit = g[1], g[2]
                _append_dimension(
                    parsed,
                    lever="length",
                    mode="max",
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department",
                    text=match.group(0),
                )
            elif kind == "bars_max":
                cue, num, unit = (g[1] or "").lower(), g[2], g[3]
                lever = (
                    "length"
                    if re.search(r"longer", cue)
                    else "width"
                )
                _append_dimension(
                    parsed,
                    lever=lever,
                    mode="max",
                    value_ft=_to_feet(_parse_num(num), unit),
                    scope="building",
                    text=match.group(0),
                )
            elif kind == "bars_exact":
                lever, num, unit = g[1].lower(), g[2], g[3]
                _append_dimension(
                    parsed,
                    lever=lever,
                    mode="exact",
                    value_ft=_to_feet(_parse_num(num), unit),
                    scope="building",
                    text=match.group(0),
                )
            elif kind == "dept_wider":
                depts = _subject_departments(g[0], names)
                num, unit = g[1], g[2]
                cue = match.group(0).lower()
                if "narrower" in cue or "less than" in cue:
                    mode = "max" if "not" in cue else "min"
                    # "narrower than" alone is unusual; "not narrower" → min
                    if "not" in cue and "narrower" in cue:
                        mode = "min"
                    elif "narrower" in cue:
                        mode = "max"
                else:
                    # wider than → preferred min unless "not wider"
                    mode = "max" if re.search(r"\bnot\b", cue) else "preferred"
                    if mode == "preferred":
                        mode = "min" if _role_from_words(cue, default="preference") == "limitation" else "preferred"
                if not depts:
                    # fall through to building-scoped
                    _append_dimension(
                        parsed,
                        lever="width",
                        mode=mode if mode != "preferred" else "preferred",
                        value_ft=_to_feet(_parse_num(num), unit),
                        scope="building",
                        text=match.group(0),
                    )
                else:
                    _append_dimension(
                        parsed,
                        lever="width",
                        mode="preferred" if "ideal" in cue or "prefer" in cue else mode,
                        value_ft=_to_feet(_parse_num(num), unit),
                        departments=depts,
                        scope="department",
                        text=match.group(0),
                    )
            elif kind == "blocks_wider":
                num, unit = g[1], g[2]
                cue = match.group(0).lower()
                mode = "preferred"
                if "narrower" in cue:
                    mode = "max"
                _append_dimension(
                    parsed,
                    lever="width",
                    mode=mode,
                    value_ft=_to_feet(_parse_num(num), unit),
                    scope="building",
                    text=match.group(0),
                    kind=_role_from_words(match.group(0), default="preference"),
                )
            elif kind == "not_wider":
                num, unit = g[0], g[1]
                cue = match.group(0).lower()
                mode = "min" if "narrower" in cue else "max"
                # Carry last department if "it/its"
                depts = []
                for prev in reversed(parsed.dimensions):
                    if prev.get("departments"):
                        depts = list(prev["departments"])
                        break
                _append_dimension(
                    parsed,
                    lever="width",
                    mode=mode,
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department" if depts else "building",
                    text=match.group(0),
                )
            elif kind == "exactly_width":
                num, unit = g[0], g[1]
                depts = []
                for prev in reversed(parsed.dimensions):
                    if prev.get("departments"):
                        depts = list(prev["departments"])
                        break
                if not depts:
                    # look back in text for a department before this match
                    before = raw[max(0, match.start() - 120) : match.start()]
                    for token in ("core academic", "academic", "art", "gym", "dining", "music"):
                        if re.search(token, before, flags=re.I):
                            hit = _subject_departments(token, names)
                            if hit:
                                depts = hit
                                break
                _append_dimension(
                    parsed,
                    lever="width",
                    mode="exact",
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department" if depts else "building",
                    text=match.group(0),
                    kind=_role_from_words(match.group(0), default="limitation"),
                )
            elif kind in {"preferred_width_range", "preferred_extra_width"}:
                nums = []
                for x in g:
                    if x is None:
                        continue
                    xs = str(x).replace(",", "")
                    if re.fullmatch(r"\d+(?:\.\d+)?", xs):
                        nums.append(float(xs))
                if not nums:
                    continue
                unit_m = re.search(
                    r"(ft|feet|foot|'|′|m|meter|meters|metre|metres)\b",
                    match.group(0),
                    flags=re.I,
                )
                unit = unit_m.group(1) if unit_m else None
                val = sum(nums) / len(nums)
                depts = []
                for prev in reversed(parsed.dimensions):
                    if prev.get("departments"):
                        depts = list(prev["departments"])
                        break
                _append_dimension(
                    parsed,
                    lever="width",
                    mode="preferred",
                    value_ft=_to_feet(val, unit),
                    departments=depts,
                    scope="department" if depts else "building",
                    text=match.group(0),
                )
            elif kind == "min_block_width":
                num, unit = g[0], g[1]
                _append_dimension(
                    parsed,
                    lever="width",
                    mode="min",
                    value_ft=_to_feet(_parse_num(num), unit),
                    scope="building",
                    text=match.group(0),
                )
            elif kind == "not_below_width":
                num, unit = g[0], g[1]
                depts = []
                for prev in reversed(parsed.dimensions):
                    if prev.get("departments"):
                        depts = list(prev["departments"])
                        break
                _append_dimension(
                    parsed,
                    lever="width",
                    mode="min",
                    value_ft=_to_feet(_parse_num(num), unit),
                    departments=depts,
                    scope="department" if depts else "building",
                    text=match.group(0),
                )

    # Fold department exact/preferred widths into a lookup the solver can use
    # before mass ids exist; mass keys are written after grouping.
    dept_widths: dict[str, float] = {}
    for clause in parsed.dimensions:
        if (
            clause.get("lever") == "width"
            and clause.get("mode") in {"exact", "preferred"}
            and clause.get("departments")
        ):
            for dept in clause["departments"]:
                dept_widths[str(dept)] = float(clause["value"])
        if (
            clause.get("lever") == "width"
            and clause.get("mode") == "max"
            and clause.get("scope") == "building"
            and "max_building_width_ft" not in parsed.constraints
        ):
            parsed.constraints["max_building_width_ft"] = float(clause["value"])
    if dept_widths:
        parsed.constraints["department_widths"] = dept_widths  # type: ignore[assignment]
    dept_edges: dict[str, float] = {}
    for clause in parsed.dimensions:
        if clause.get("lever") != "length" or clause.get("mode") != "max":
            continue
        value = float(clause["value"])
        depts = clause.get("departments") or []
        if depts:
            for dept in depts:
                dept_edges[str(dept)] = value
        elif (
            clause.get("scope") == "building"
            and "max_building_length_ft" not in parsed.constraints
        ):
            _stamp_all_edge_cap(parsed, value)
    if dept_edges:
        parsed.constraints["department_max_edge_ft"] = dept_edges  # type: ignore[assignment]


def _stamp_all_edge_cap(parsed: ParsedBrief, value_ft: float, *, note: bool = True) -> None:
    """All-masses length limitation: every plan edge ≤ value, not only the short side."""
    cap = round(float(value_ft), 4)
    existing_l = parsed.constraints.get("max_building_length_ft")
    existing_w = parsed.constraints.get("max_building_width_ft")
    if existing_l is not None:
        cap = min(cap, float(existing_l))
    parsed.constraints["max_building_length_ft"] = cap
    parsed.constraints["max_edge_ft"] = cap
    parsed.constraints["length_limit_is_cap"] = 1
    if existing_w is None:
        parsed.constraints["max_building_width_ft"] = cap
    else:
        parsed.constraints["max_building_width_ft"] = min(float(existing_w), cap)
    if note:
        parsed.notes.append(
            f"max mass edge {cap:g} ft (every side of every mass)"
        )


def _stamp_all_edge_floor(parsed: ParsedBrief, value_ft: float, *, note: bool = True) -> None:
    """All-masses length floor: every plan edge ≥ value (pairs with max_edge)."""
    floor = round(float(value_ft), 4)
    existing = parsed.constraints.get("min_edge_ft")
    if existing is not None:
        floor = max(floor, float(existing))
    parsed.constraints["min_edge_ft"] = floor
    parsed.constraints["min_building_length_ft"] = floor
    if note:
        parsed.notes.append(
            f"min mass edge {floor:g} ft (every side of every mass)"
        )


def _extract_min_edge_length(parsed: ParsedBrief, raw: str) -> None:
    """length min / max-and-min / between — same all-edge sense as max length."""
    if parsed.constraints.get("min_edge_ft") is not None:
        return
    compound = re.search(
        rf"lengths?\s+max(?:imum)?(?:\s+of|\s+is|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}"
        rf"\s*(?:and|,|;)\s*min(?:imum)?(?:\s+of|\s+is|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}",
        raw,
        flags=re.I,
    )
    if compound:
        _stamp_all_edge_floor(
            parsed, _to_feet(_parse_num(compound.group(3)), compound.group(4))
        )
        return
    compound_gap = re.search(
        rf"lengths?\s+max(?:imum)?(?:\s+of|\s+is|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}"
        rf"[\s.,;]{{0,24}}?min(?:imum)?(?:\s+of|\s+is|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}",
        raw,
        flags=re.I | re.S,
    )
    if compound_gap:
        _stamp_all_edge_floor(
            parsed, _to_feet(_parse_num(compound_gap.group(3)), compound_gap.group(4))
        )
        return
    between = re.search(
        rf"lengths?\s+between\s*{_NUM_OR_WORD}{_LEN_UNIT}\s+and\s*{_NUM_OR_WORD}{_LEN_UNIT}",
        raw,
        flags=re.I,
    )
    if between:
        a = _to_feet(_parse_num(between.group(1)), between.group(2) or between.group(4))
        b = _to_feet(_parse_num(between.group(3)), between.group(4))
        lo, hi = (a, b) if a <= b else (b, a)
        _stamp_all_edge_floor(parsed, lo)
        if parsed.constraints.get("max_edge_ft") is None:
            _stamp_all_edge_cap(parsed, hi)
        return
    min_only = _first_length(
        [
            rf"lengths?\s+min(?:imum)?(?:\s+of|\s+is|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"min(?:imum)?\s+(?:building\s+)?lengths?(?:\s+of|\s+is|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:each|every|any)\s+(?:mass|wing|building)\s+(?:at\s+least|no\s+shorter\s+than)\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:at\s+least|no\s+shorter\s+than|no\s+less\s+than)\s*{_NUM_OR_WORD}{_LEN_UNIT}\s*(?:long|in\s+length)",
            rf"(?:each|every|any)\s+(?:mass|wing|building)\s+(?:should\s+be\s+)?at\s+least\s*{_NUM_OR_WORD}{_LEN_UNIT}\s+long",
        ],
        raw,
    )
    if min_only is not None:
        _stamp_all_edge_floor(parsed, min_only)


def parse_brief(text: str, department_names: list[str]) -> ParsedBrief:
    parsed = ParsedBrief(text=text or "")
    raw = text or ""

    _extract_scoped_dimensions(parsed, raw, department_names)

    total = _first_length(
        [
            rf"site\s+length\s*(?:is|of|=|:)?\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+frontage\s*(?:is|of|=|:)?\s*{_NUM}{_LEN_UNIT}",
            rf"frontage\s*(?:is|of|=|:)?\s*{_NUM}{_LEN_UNIT}",
            rf"{_NUM}{_LEN_UNIT}\s+of\s+frontage",
            rf"(?:total|combined|overall)\s+length\s*(?:is|of|=|:)?\s*{_NUM}{_LEN_UNIT}",
            # "site length max 400 ft" / "max site length 400" / "site length maximum of 400"
            rf"site\s+(?:length|frontage)\s+max(?:imum)?(?:\s+of|\s+is|=|:)?\s*{_NUM}{_LEN_UNIT}",
            rf"max(?:imum)?\s+site\s+(?:length|frontage)(?:\s+of|\s+is|=|:)?\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+(?:length|frontage)\s+(?:cap|limit)(?:\s+of|\s+is|=|:)?\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+(?:length|frontage)\s+(?:at\s+most|no\s+more\s+than|not\s+more\s+than)\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+(?:is\s+)?(?:no|not)\s+longer\s+than\s*{_NUM}{_LEN_UNIT}",
            # Bare "no longer than N" is per-mass language elsewhere — do not treat as site.
            rf"site\s+(?:under|below|within)\s*{_NUM}{_LEN_UNIT}",
            rf"(?:total|combined|overall|site)\s+(?:length\s+)?(?:under|below|within)\s*{_NUM}{_LEN_UNIT}",
            rf"(?:total|combined|overall)\s+(?:is\s+)?(?:no|not)\s+longer\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"(?:should\s+)?not\s+stretch\s+beyond\s*{_NUM}{_LEN_UNIT}",
            rf"(?:full\s+)?site\s+length\s+cannot\s+exceed\s*{_NUM}{_LEN_UNIT}",
            rf"hold\s+the\s+total\s+site\s+length\s+below\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+depth\s+(?:must\s+)?(?:stay\s+)?under\s*{_NUM}{_LEN_UNIT}",
            rf"push\s+the\s+site\s+beyond\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+(?:about|around|roughly|of)?\s*{_NUM}{_LEN_UNIT}\s+long",
            rf"on\s+a\s+site\s+(?:about|around|roughly)?\s*{_NUM}{_LEN_UNIT}\s+long",
            rf"(?:overall\s+)?site\s+should\s+stay\s+under\s*{_NUM}{_LEN_UNIT}\s+long",
            rf"site\s+(?:should\s+)?(?:stay\s+)?under\s*{_NUM}{_LEN_UNIT}\s+long",
            rf"(?:overall\s+)?site\s+length\s+(?:should\s+)?(?:stay\s+)?under\s*{_NUM}{_LEN_UNIT}",
            rf"(?:combined|total)\s+length\s+of\s+the\s+masses\s+"
            rf"(?:should\s+)?(?:stay\s+)?under\s*{_NUM}{_LEN_UNIT}",
            rf"(?:combined|total)\s+length\s+(?:should\s+)?(?:stay\s+)?under\s*{_NUM}{_LEN_UNIT}",
            rf"(?:overall\s+)?site\s+length\s+(?:should\s+)?(?:remain|be)\s+(?:under|below)\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+(?:length|frontage)\s+(?:should\s+)?(?:stay\s+|remain\s+)?(?:under|below)\s*{_NUM}{_LEN_UNIT}",
        ],
        raw,
    )
    # "The site is about 130 m by 360 ft"
    site_by = re.search(
        rf"site\s+is\s+(?:about|around|roughly)?\s*{_NUM}{_LEN_UNIT}\s+by\s+{_NUM}{_LEN_UNIT}",
        raw,
        flags=re.I,
    )
    if site_by:
        a = _to_feet(_parse_num(site_by.group(1)), site_by.group(2))
        b = _to_feet(_parse_num(site_by.group(3)), site_by.group(4))
        longer, shorter = max(a, b), min(a, b)
        parsed.constraints["max_total_length_ft"] = round(longer, 4)
        parsed.constraints["max_building_width_ft"] = round(shorter, 4)
        parsed.constraints["site_width_ft"] = round(shorter, 4)
        parsed.constraints["site_depth_ft"] = round(longer, 4)
        parsed.notes.append(
            f"site envelope {shorter:g} × {longer:g} ft (width × length)"
        )
    elif total is not None:
        parsed.constraints["max_total_length_ft"] = round(total, 4)
        parsed.notes.append(f"site / total length {total:g} ft")

    per_len = _first_length(
        [
            # "each length should be under 40 m" / "lengths stay under forty meters"
            rf"(?:each|every|any|per(?:-|\s+)?mass)\s+lengths?\s+"
            rf"(?:should\s+be|must\s+be|should\s+stay|must\s+stay|stay|remain|be)?\s*"
            rf"(?:under|below|at\s+most|no\s+more\s+than|not\s+more\s+than)\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"lengths?\s+(?:should\s+be|must\s+be|should\s+stay|must\s+stay|stay|remain)\s+"
            rf"(?:under|below|at\s+most|no\s+more\s+than)\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:each|every|any)\s+(?:mass|wing|building|bar|volume|block|piece)\s+"
            rf"lengths?\s+(?:should\s+be|must\s+be|stay|remain|be)?\s*"
            rf"(?:under|below|at\s+most|no\s+more\s+than)\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"nothing\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"no\s+(?:wing|building|mass)\s+(?:over|longer than)\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:max(?:imum)?|length)\s+limit\s*(?:is|of|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"max(?:imum)?\s+(?:building\s+)?length\s*(?:is|of|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            # "length max 40 meters" / "length maximum of 40 m"
            rf"lengths?\s+max(?:imum)?(?:\s+of|\s+is|=|:)?\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:building\s+)?lengths?\s+(?:at\s+most|under|below|no\s+more\s+than)\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            # "each mass should not be longer than 75 meters"
            rf"(?:each|every|any)\s+(?:mass|wing|building|bar|volume|block|piece)\s+"
            rf"(?:should|must)\s+not\s+be\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:masses|wings|buildings|bars|volumes|blocks)\s+"
            rf"(?:should|must)\s+not\s+be\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            # "each mass can be no more than 3 stories and no longer than 210 ft"
            rf"(?:each|every|any)\s+(?:mass|wing|building|bar|volume|block|piece)"
            rf".{{0,90}}?(?:no|not)\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:each|every|any)\s+(?:mass|wing|building|bar)"
            rf".{{0,60}}?no\s+more\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}\s+long",
            rf"no\s+(?:mass|wing|building|bar|volume|block|piece)\s+"
            rf"should\s+be\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:each|every)\s+(?:mass|wing|building|bar)\s+"
            rf"(?:no\s+longer\s+than|at\s+most|under|below)\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:each|every)\s+(?:mass|wing|building|bar)\s+"
            rf"(?:cannot|can't|must\s+not|should\s+not)\s+exceed\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"no\s+building\s+should\s+be\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:that\s+)?mass\s+should\s+stay\s+under\s*{_NUM_OR_WORD}{_LEN_UNIT}\s+long",
            rf"stay\s+under\s*{_NUM_OR_WORD}{_LEN_UNIT}\s+long",
            rf"no\s+building\s+over\s*{_NUM}(?=\s*['′]|\s*(?:ft|feet)\b)",
            rf"no\s+building\s+over\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"no\s+piece\s+over\s*{_NUM}(?=\s*['′]|\s*(?:ft|feet)\b)",
            rf"no\s+(?:piece|form|bar|box|cube|slab|block)\s+over\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:main\s+)?bar\s+should\s+stay\s+under\s*{_NUM_OR_WORD}{_LEN_UNIT}\s+long",
            rf"(?:that\s+)?block\s+should\s+stay\s+under\s*{_NUM_OR_WORD}{_LEN_UNIT}\s+long",
            rf"no\s+bar\s+should\s+be\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            # "no mass can exceed N ft in length" / "should not exceed N ft in length"
            rf"no\s+(?:mass|wing|building|bar|volume|block|piece)\s+"
            rf"(?:can|may|should|must)\s+exceed\s*{_NUM_OR_WORD}{_LEN_UNIT}"
            rf"(?:\s+in\s+length)?",
            rf"(?:mass|wing|building|volume|block|piece).{{0,40}}?"
            rf"(?:should\s+not|must\s+not|cannot|can't)\s+exceed\s*{_NUM_OR_WORD}{_LEN_UNIT}"
            rf"(?:\s+in\s+length)?",
            rf"(?:longest|any|each|every|shared|their|that|the)\s+"
            rf"(?:mass|wing|building|volume|block|piece).{{0,30}}?"
            rf"(?:should\s+not|must\s+not|cannot|not)\s+(?:go\s+)?beyond\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:not\s+go\s+beyond|go\s+beyond)\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:mass|building)?\s*length\s+(?:should\s+be\s+)?capped\s+at\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"capped\s+at\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:no|not)\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"should\s+be\s+no\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:stories?|floors?|levels?).{{0,20}}?or\s+{_NUM_OR_WORD}{_LEN_UNIT}\s+in\s+length",
            rf"or\s+{_NUM_OR_WORD}{_LEN_UNIT}\s+in\s+length",
            # Prefer the length number when paired with a story cap in one clause.
            rf"(?:stories?|floors?|levels?).{{0,30}}?or\s+{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:should\s+not|must\s+not)\s+exceed\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"no\s+(?:primary\s+|main\s+)?(?:mass|wing|building|volume|block)\s+"
            rf"(?:may|can|should|must)\s+be\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:may|can)\s+be\s+longer\s+than\s*{_NUM_OR_WORD}{_LEN_UNIT}",
        ],
        raw,
        skip_spans=_scoped_length_max_spans(parsed, raw),
    )
    # "N stories or M ft in length" — if we captured the story count as length, fix it.
    story_or_len = re.search(
        rf"({_WORD}|\d+)\s+(?:stories?|floors?|levels?)\s+or\s+{_NUM}{_LEN_UNIT}"
        rf"(?:\s+in\s+length)?",
        raw,
        flags=re.I,
    )
    if story_or_len and per_len is not None:
        story_n = _word_or_digit(str(story_or_len.group(1)))
        length_val = _to_feet(_parse_num(story_or_len.group(2)), story_or_len.group(3))
        if story_n is not None and abs(per_len - float(story_n)) < 0.01:
            per_len = length_val

    if per_len is not None:
        already = "max_building_length_ft" in parsed.constraints
        _stamp_all_edge_cap(parsed, per_len, note=not already)

    _extract_min_edge_length(parsed, raw)

    width = _first_length(
        [
            rf"nothing\s+wider\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"no\s+(?:wing|building|mass|bar)\s+wider\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"max(?:imum)?\s+(?:building\s+)?width\s*(?:is|of|=|:)?\s*{_NUM}{_LEN_UNIT}",
            rf"(?:site|building)\s+width\s*(?:is|of|=|:)?\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+is\s+no\s+wider\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"no\s+wider\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+width\s+(?:cannot|can't|should\s+not|must\s+not)\s+exceed\s*{_NUM}{_LEN_UNIT}",
            rf"site\s+width\s+(?:at\s+most|under|below|no\s+more\s+than)\s*{_NUM}{_LEN_UNIT}",
        ],
        raw,
    )
    # Standalone "width is 100" only when the cue is not a department size
    # ("maintain a width of 85", "academic width …").
    if width is None:
        for match in re.finditer(
            rf"\bwidth\s*(?:is|=|:)\s*{_NUM}{_LEN_UNIT}",
            raw,
            flags=re.I,
        ):
            before = raw[max(0, match.start() - 48) : match.start()].lower()
            if re.search(
                r"(?:maintain|keeping|keep|holding|hold|fixed|a|the|its|their|for)\s+$",
                before,
            ) or re.search(
                r"[a-z]{3,}\s+$",
                before,
            ):
                # Preceded by a noun phrase / maintain cue → scoped, not global.
                continue
            width = _to_feet(_parse_num(match.group(1)), match.group(2))
            break
    if width is not None:
        # Do not treat a department exact width as a global building max.
        if not any(
            c.get("lever") == "width"
            and c.get("departments")
            and abs(float(c["value"]) - width) < 0.01
            for c in parsed.dimensions
        ):
            rounded = round(width, 4)
            existing = parsed.constraints.get("max_building_width_ft")
            if existing is None:
                parsed.constraints["max_building_width_ft"] = rounded
                parsed.notes.append(f"max width {width:g} ft")
                _append_dimension(
                    parsed,
                    lever="width",
                    mode="max",
                    value_ft=width,
                    scope="building",
                    text=f"max width {width:g} ft",
                )
            else:
                parsed.constraints["max_building_width_ft"] = min(float(existing), rounded)

    height = _first_length(
        [
            rf"nothing\s+(?:should\s+be\s+)?taller\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"no\s+building\s+(?:can\s+be\s+|should\s+be\s+)?(?:taller|higher)\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"no\s+(?:mass|building|volume|block)\s+(?:should\s+be\s+|can\s+be\s+)?"
            rf"(?:taller|higher)\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"(?:overall\s+)?height\s+below\s+(?:about\s+)?{_NUM}{_LEN_UNIT}",
            rf"keep\s+the\s+overall\s+height\s+below\s+(?:about\s+)?{_NUM}{_LEN_UNIT}",
            rf"tallest\s+(?:one|point|building|tower)?\s*(?:must\s+)?(?:stay\s+)?below\s*{_NUM}{_LEN_UNIT}",
            rf"hold\s+the\s+tallest\s+(?:tower|building|one)\s+below\s*{_NUM}{_LEN_UNIT}",
            rf"keep\s+the\s+tallest\s+one\s+below\s*{_NUM}{_LEN_UNIT}",
            rf"nothing\s+much\s+taller\s+than\s*{_NUM}{_LEN_UNIT}",
            rf"exceed\s+{_NUM}{_LEN_UNIT}\s+in\s+height",
            rf"(?:each|every|any)\s+(?:mass|building|volume|block)\s+"
            rf"(?:should\s+)?(?:remain|stay)\s+below\s*{_NUM}{_LEN_UNIT}"
            rf"(?:\s+in\s+height)?",
            rf"remain\s+below\s*{_NUM}{_LEN_UNIT}\s+in\s+height",
            rf"(?:under|below)\s*{_NUM}{_LEN_UNIT}\s+tall",
            rf"(?:no|not)\s+higher\s+than\s*{_NUM}{_LEN_UNIT}",
        ],
        raw,
    )
    # Word-number height: "sixty feet in height" — never steal length clauses.
    if height is None:
        word_h = re.search(
            rf"(?:exceed|taller than|below|under|over)\s+"
            rf"(sixty-five|sixty|fifty-five|fifty|forty|thirty|twenty|eighteen|fourteen)\s*"
            rf"(feet|ft|foot|m|meters|metres)?(?:\s+in\s+height)?",
            raw,
            flags=re.I,
        )
        if word_h:
            window = raw[max(0, word_h.start() - 48) : word_h.end() + 24].lower()
            cue = word_h.group(0).lower()
            lengthish = bool(re.search(r"\blength|\blonger|\blong\b|\bwidth|\bwider\b", window))
            heightish = bool(re.search(r"\bheight|\btall(?:er|est)?\b|\btower\b", window))
            under_bare = cue.startswith("under") or cue.startswith("below")
            # "each length … under forty meters" is length, not height.
            if lengthish and not heightish:
                word_h = None
            elif under_bare and not heightish and "taller" not in cue and "exceed" not in cue:
                word_h = None
        if word_h:
            words = {
                "fourteen": 14,
                "eighteen": 18,
                "twenty": 20,
                "thirty": 30,
                "forty": 40,
                "fifty": 50,
                "fifty-five": 55,
                "sixty": 60,
                "sixty-five": 65,
            }
            height = _to_feet(float(words[word_h.group(1).lower()]), word_h.group(2))
    # Height-derived story estimate is soft; explicit floor/G+ language wins later.
    height_implied_stories: int | None = None
    if height is not None:
        parsed.constraints["max_height_ft"] = round(height, 4)
        height_implied_stories = max(1, int(height // _STORY_HEIGHT_FT))
        parsed.notes.append(f"max height {height:g} ft")

    _extract_vertical_limits(parsed, raw, height_implied_stories)

    # Exception / soft upper story caps are handled in _extract_vertical_limits.
    shorter = _first_length(
        [
            rf"length\s+should be\s+shorter than\s+{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"length\s+(?:shorter|less)\s+than\s+{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:each\s+)?(?:mass|wing|building)\s+length\s+(?:shorter|less|under)\s+than\s+{_NUM_OR_WORD}{_LEN_UNIT}",
            rf"(?:each|every|any)?\s*lengths?\s+(?:should\s+be\s+)?(?:under|below)\s+{_NUM_OR_WORD}{_LEN_UNIT}",
        ],
        raw,
    )
    if shorter is not None and "max_building_length_ft" not in parsed.constraints:
        _stamp_all_edge_cap(parsed, shorter)

    preferred = _first_length(
        [
            rf"prefer(?:ably|red)?\s+(?:under|below|shorter than|less than)\s+{_NUM}{_LEN_UNIT}",
            rf"prefer(?:ably|red)?\s+length\s+(?:under|below|shorter than)\s+{_NUM}{_LEN_UNIT}",
            rf"(?:lengths?|length)\s+closer\s+to\s+{_NUM}(?:\s*[–\-—/to]+\s*{_NUM})?{_LEN_UNIT}",
            rf"closer\s+to\s+{_NUM}(?:\s*[–\-—/to]+\s*{_NUM})?{_LEN_UNIT}\s+would\s+be\s+prefer",
            rf"shorter\s+length\s+around\s+{_NUM}{_LEN_UNIT}",
            rf"length\s+around\s+{_NUM}{_LEN_UNIT}\s+would\s+be\s+prefer",
            rf"around\s+{_NUM}{_LEN_UNIT}\s+would\s+be\s+prefer",
            rf"a\s+length\s+around\s+{_NUM}{_LEN_UNIT}",
        ],
        raw,
    )
    if preferred is not None:
        parsed.constraints["preferred_length_ft"] = round(preferred, 4)
        parsed.notes.append(
            f"prefer length under {preferred:g} ft (preference, not a target)"
        )

    gfa = _first_area(
        [
            rf"(?:total\s+)?(?:floor\s+area|gfa|gsf|program)\s+(?:of\s+)?(?:roughly|about|around)?\s*{_NUM}{_AREA_UNIT}",
            rf"(?:combined|total)\s+area\s+of\s+(?:roughly|about|around)?\s*{_NUM}{_AREA_UNIT}",
            rf"about\s+{_NUM}{_AREA_UNIT}\s+of\s+total\s+program",
            rf"at\s+least\s+{_NUM}{_AREA_UNIT}\s+of\s+total\s+floor\s+area",
            rf"no\s+more\s+than\s+{_NUM}{_AREA_UNIT}\s+total\s+gfa",
            rf"total\s+gfa\s+should\s+stay\s+under\s+{_NUM}{_AREA_UNIT}",
            rf"keep\s+the\s+total\s+gfa\s+under\s+{_NUM}{_AREA_UNIT}",
            rf"under\s+{_NUM}{_AREA_UNIT}\s+total\s+gfa",
            rf"total\s+footprint\s+under\s+{_NUM}{_AREA_UNIT}",
            rf"around\s+{_NUM}{_AREA_UNIT}\s+of\s+total\s+floor\s+area",
            rf"totaling\s+around\s+{_WORD}[-\s]?thousand\s+square\s+meters",
            rf"with\s+a\s+combined\s+area\s+of\s+(?:roughly|about)?\s*{_NUM}{_AREA_UNIT}",
            rf"cap\s+the\s+project\s+at\s+(?:about|around|roughly)?\s*{_NUM}{_AREA_UNIT}",
            rf"keep\s+the\s+total\s+gfa\s+under\s+{_NUM}{_AREA_UNIT}",
            rf"targeting\s+(?:about|around|roughly)?\s*{_NUM}{_AREA_UNIT}\s+total",
            rf"(?:about|around|roughly)\s+{_NUM}{_AREA_UNIT}\s+total\b",
            rf"target(?:ing)?\s+(?:about|around|roughly)?\s*{_NUM}{_AREA_UNIT}",
            rf"(?:total\s+)?(?:gsf|gfa)\s+(?:should\s+)?(?:stay\s+)?(?:below|under)\s*{_NUM}{_AREA_UNIT}",
            rf"(?:stay\s+)?(?:below|under)\s*{_NUM}\s+square\s+feet",
            rf"(?:total\s+)?(?:gsf|gfa).{{0,30}}?(?:below|under)\s*{_NUM}",
            rf"(?:shared\s+)?(?:building|mass).{{0,40}}?(?:cannot|can't|should\s+not|must\s+not)\s+exceed\s*{_NUM}{_AREA_UNIT}",
            rf"cannot\s+exceed\s*{_NUM}{_AREA_UNIT}\s*(?:gsf|gfa)?",
        ],
        raw,
    )
    # "twenty-two thousand square meters"
    if gfa is None:
        word_area = re.search(
            r"(twenty-two|twenty|thirty|forty|fifty)\s+thousand\s+square\s+(meters|metres|feet)",
            raw,
            flags=re.I,
        )
        if word_area:
            words = {"twenty": 20, "twenty-two": 22, "thirty": 30, "forty": 40, "fifty": 50}
            gfa = _to_sf(words[word_area.group(1).lower()] * 1000.0, word_area.group(2))
    if gfa is not None:
        key = "max_gfa_sf"
        if re.search(r"\bcap\b|under|no\s+more\s+than|stay\s+under", raw, flags=re.I):
            key = "max_gfa_sf"
        elif re.search(r"at\s+least|around|roughly|about|totaling|targeting|target", raw, flags=re.I):
            key = "target_gfa_sf"
        parsed.constraints[key] = round(gfa, 1)
        parsed.notes.append(f"{key.replace('_', ' ')} {gfa:g} sf")

    footprint = _first_area(
        [
            rf"no\s+single\s+footprint\s+should\s+exceed\s+{_NUM}{_AREA_UNIT}",
            rf"(?:single|each|any)\s+(?:building\s+)?footprint\s+(?:should\s+)?"
            rf"(?:not\s+exceed|under|below)\s+{_NUM}{_AREA_UNIT}",
            rf"footprint\s+(?:cap|limit)\s*(?:of|is|=|:)?\s*{_NUM}{_AREA_UNIT}",
            rf"(?:no|not)\s+(?:main\s+)?(?:mass|volume|block|building).{{0,30}}?"
            rf"exceed\s+{_NUM}{_AREA_UNIT}\s+footprint",
            rf"exceed\s+{_NUM}{_AREA_UNIT}\s+footprint",
            rf"(?:no|not)\s+(?:volume|mass|block|building).{{0,40}}?"
            rf"exceed\s+{_NUM}{_AREA_UNIT}\s+per\s+floor",
            rf"exceed\s+{_NUM}{_AREA_UNIT}\s+per\s+floor",
            rf"no\s+more\s+than\s+{_NUM}{_AREA_UNIT}\s+per\s+floor",
            rf"(?:combined\s+)?mass\s+should\s+be\s+no\s+more\s+than\s+{_NUM}{_AREA_UNIT}\s+per\s+floor",
        ],
        raw,
    )
    if footprint is not None:
        parsed.constraints["max_footprint_sf"] = round(footprint, 1)
        parsed.notes.append(f"max single footprint {footprint:g} sf")
    pref_foot = _first_area(
        [
            rf"footprints?\s+closer\s+to\s+{_NUM}{_AREA_UNIT}",
            rf"(?:rather|prefer).{{0,40}}?footprints?\s+closer\s+to\s+{_NUM}{_AREA_UNIT}",
            rf"(?:rather|prefer).{{0,60}}?closer\s+to\s+{_NUM}{_AREA_UNIT}",
        ],
        raw,
    )
    if pref_foot is not None:
        parsed.constraints["preferred_footprint_sf"] = round(pref_foot, 1)
        parsed.notes.append(f"preferred footprint {pref_foot:g} sf")
    elif re.search(
        r"(?:slightly\s+)?larger\s+(?:ground[- ]floor\s+)?footprint",
        raw,
        flags=re.I,
    ):
        parsed.constraints["prefer_larger_footprint"] = 1
        parsed.notes.append("prefer larger footprint")

    # Soft min / preferred mass floor area
    min_area = _first_area(
        [
            rf"(?:masses|mass|volumes?|blocks?).{{0,40}}?"
            rf"(?:larger\s+than|at\s+least|no\s+smaller\s+than|not\s+be\s+under|"
            r"not\s+be\s+smaller\s+than)\s*{_NUM}{_AREA_UNIT}",
            rf"(?:smallest\s+mass).{{0,40}}?(?:not\s+be\s+under|larger\s+than|at\s+least)\s*{_NUM}{_AREA_UNIT}",
            rf"(?:rather\s+avoid|prefer).{{0,40}}?(?:smaller\s+than|under)\s*{_NUM}{_AREA_UNIT}",
            rf"larger\s+than\s+{_NUM}{_AREA_UNIT}\s+if\s+possible",
        ],
        raw,
    )
    if min_area is not None:
        area_m = None
        for pat in [
            rf"(?:masses|mass|volumes?|blocks?).{{0,40}}?"
            rf"(?:larger\s+than|at\s+least|no\s+smaller\s+than|not\s+be\s+under|"
            r"not\s+be\s+smaller\s+than)\s*{_NUM}{_AREA_UNIT}",
            rf"(?:smallest\s+mass).{{0,40}}?(?:not\s+be\s+under|larger\s+than|at\s+least)\s*{_NUM}{_AREA_UNIT}",
            rf"(?:rather\s+avoid|prefer).{{0,40}}?(?:smaller\s+than|under)\s*{_NUM}{_AREA_UNIT}",
            rf"larger\s+than\s+{_NUM}{_AREA_UNIT}\s+if\s+possible",
        ]:
            area_m = re.search(pat, raw, flags=re.I)
            if area_m:
                break
        clause = area_m.group(0) if area_m else "if possible"
        role = _role_from_words(clause, default="preference")
        if re.search(r"if\s+possible|prefer|rather|avoid", clause, flags=re.I):
            role = "preference"
        key = "preferred_min_area_sf" if role == "preference" else "min_mass_area_sf"
        parsed.constraints[key] = round(min_area, 1)
        parsed.notes.append(f"{key.replace('_', ' ')} {min_area:g} sf")
    # Preferred floor-area floor when a separate prefer clause exists
    pref_area = _first_area(
        [
            rf"(?:prefer|rather).{{0,60}}?(?:larger\s+than|at\s+least)\s*{_NUM}{_AREA_UNIT}",
            rf"(?:prefer|rather).{{0,60}}?avoid.{{0,30}}?smaller\s+than\s*{_NUM}{_AREA_UNIT}",
            rf"prefer.{{0,40}}?smallest.{{0,40}}?larger\s+than\s*{_NUM}{_AREA_UNIT}",
        ],
        raw,
    )
    if pref_area is not None:
        parsed.constraints["preferred_min_area_sf"] = round(pref_area, 1)
        parsed.notes.append(f"preferred min area {pref_area:g} sf")

    open_pct = re.search(
        rf"(?:at\s+least|preserve\s+at\s+least|remain)\s*{_NUM}\s*%\s*"
        rf"(?:of\s+the\s+site\s+)?(?:should\s+remain\s+)?(?:unbuilt|open)",
        raw,
        flags=re.I,
    )
    if not open_pct:
        open_pct = re.search(
            rf"(?:roughly|around|about)\s*{_NUM}\s*(?:percent|%)\s+open\s+space",
            raw,
            flags=re.I,
        )
    if not open_pct:
        open_pct = re.search(
            rf"(?:around|about|roughly)?\s*{_NUM}\s*%\s+open\s+space",
            raw,
            flags=re.I,
        )
    if not open_pct:
        open_pct = re.search(
            rf"site\s+needs\s+at\s+least\s*{_NUM}\s*%\s+open\s+space",
            raw,
            flags=re.I,
        )
    if not open_pct:
        open_pct = re.search(
            rf"(?:at\s+least|ideally\s+at\s+least)\s+(thirty|forty|twenty-five|25|30|40)\s*"
            rf"(?:percent|%)\s+of\s+the\s+site\s+remains\s+open",
            raw,
            flags=re.I,
        )
    if not open_pct:
        open_pct = re.search(
            r"(?:at\s+least\s+)?(one[\s-]third|one[\s-]quarter|two[\s-]thirds)\s+"
            r"(?:should\s+remain\s+)?(?:unbuilt|open)",
            raw,
            flags=re.I,
        )
    # Built coverage cap → implied open-space floor
    coverage = re.search(
        rf"built\s+coverage\s+(?:should\s+not|must\s+not|cannot)\s+exceed\s*{_NUM}\s*%",
        raw,
        flags=re.I,
    )
    if open_pct:
        token = open_pct.group(1)
        pct = {
            "twenty-five": 25.0,
            "thirty": 30.0,
            "forty": 40.0,
            "one-third": 100.0 / 3.0,
            "one third": 100.0 / 3.0,
            "one-quarter": 25.0,
            "one quarter": 25.0,
            "two-thirds": 200.0 / 3.0,
            "two thirds": 200.0 / 3.0,
        }.get(str(token).lower().replace("  ", " "))
        if pct is None:
            pct = _parse_num(token)
        sent = _sentence_at(raw, open_pct.start(), open_pct.end())
        role = _role_from_words(sent, default="preference")
        if re.search(r"\b(?:needs?|must|required|at\s+least)\b", sent, flags=re.I) and not re.search(
            r"prefer|better|around|ideally", sent, flags=re.I
        ):
            role = "requirement"
        if re.search(r"prefer|better|around|ideally|would\s+be", sent, flags=re.I):
            role = "preference"
        parsed.constraints["min_open_space_pct"] = round(pct, 2)
        parsed.constraints["open_space_role"] = role
        parsed.notes.append(f"open space {pct:g}% ({role})")
    # Preferred open space (may coexist with a required minimum)
    pref_open = re.search(
        rf"(?:around|about|roughly)\s*{_NUM}\s*%\s+open\s+space\s+would\s+be\s+(?:better|preferred|preferable|ideal)"
        rf"|(?:around|about|roughly)\s*{_NUM}\s*%\s+open\s+space",
        raw,
        flags=re.I,
    )
    if pref_open and re.search(r"prefer|better|ideal|would", pref_open.group(0), flags=re.I):
        parsed.constraints["preferred_open_space_pct"] = round(_parse_num(pref_open.group(1)), 2)
        parsed.notes.append(
            f"preferred open space {parsed.constraints['preferred_open_space_pct']:g}%"
        )
    if coverage:
        cov = _parse_num(coverage.group(1))
        pct = max(0.0, 100.0 - cov)
        parsed.constraints.setdefault("min_open_space_pct", round(pct, 2))
        parsed.constraints.setdefault("open_space_role", "limitation")
        parsed.notes.append(f"built coverage <={cov:g}% -> open >={pct:g}%")

    # "length should be 50" is an exact length. "shorter than / under" is not.
    if shorter is None and not re.search(
        r"length\s+(?:should be\s+)?(?:shorter|less|under)|nothing\s+longer|not\s+stretch",
        raw,
        flags=re.I,
    ):
        exact = _first_length(
            [
                rf"length\s+should be\s+{_NUM}{_LEN_UNIT}",
                rf"length\s+(?:is|of|=)\s*{_NUM}{_LEN_UNIT}",
                rf"(?:each\s+)?(?:mass|wing|building)\s+length\s+(?:is|of|=|should be)\s*{_NUM}{_LEN_UNIT}",
            ],
            raw,
        )
        if exact is not None:
            parsed.constraints["exact_building_length_ft"] = round(exact, 4)
            parsed.notes.append(f"length should be {exact:g} ft (exact)")

    ratio_band = _extract_ratio_band(raw)
    if ratio_band:
        lo, hi = ratio_band["lo"], ratio_band["hi"]
        role = str(ratio_band.get("role") or "limitation")
        parsed.constraints["ratio_band"] = [lo, hi]
        parsed.constraints["ratio_band_parts"] = ratio_band["parts"]
        parsed.constraints["ratio_band_role"] = role
        # Soft sizing target at the band midpoint so bars start inside the band.
        if parsed.length_over_width is None:
            parsed.length_over_width = (lo + hi) / 2.0
            parsed.constraints["preferred_ratio"] = [
                ratio_band["parts"][0][0],
                ratio_band["parts"][0][1],
            ]
            parsed.constraints["ratio_role"] = role if role == "preference" else "preference"
        parsed.notes.append(
            f"{'prefer' if role == 'preference' else 'require'} mass length:width between "
            f"{ratio_band['parts'][0][0]:g}:{ratio_band['parts'][0][1]:g} "
            f"and {ratio_band['parts'][1][0]:g}:{ratio_band['parts'][1][1]:g}"
        )

    ratio = None if ratio_band else re.search(
        r"(?:mass\s+)?ratio\b.{0,40}?(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
        raw,
        flags=re.I | re.S,
    )
    if not ratio_band and not ratio:
        ratio = re.search(
            r"(?:mass\s+)?proportion\b.{0,40}?(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
            raw,
            flags=re.I | re.S,
        )
    if not ratio_band and not ratio:
        ratio = re.search(
            r"(?:stay\s+)?close\s+to\s+(?:a\s+)?(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
            raw,
            flags=re.I,
        )
    if not ratio_band and not ratio:
        ratio = re.search(
            r"(?:follow\s+)?roughly\s+(?:a\s+)?(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
            raw,
            flags=re.I,
        )
    if not ratio_band and not ratio:
        ratio = re.search(
            r"(?:around|about|roughly)\s+(?:a\s+)?(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)"
            r"\s*(?:proportion|ratio)?",
            raw,
            flags=re.I,
        )
    if not ratio_band and not ratio:
        ratio = re.search(
            r"prefer(?:red)?(?:\s+to\s+be|\s+of|\s+as)?\s+(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
            raw,
            flags=re.I,
        )
    if not ratio_band and not ratio:
        ratio = re.search(
            r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*(?:proportion|ratio|width-to-length)",
            raw,
            flags=re.I,
        )
    if not ratio_band and not ratio:
        # "width-to-length ratio" / "1:4 width-to-length"
        ratio = re.search(
            r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s+width-to-length",
            raw,
            flags=re.I,
        )
    if ratio:
        length_part = float(ratio.group(1))
        width_part = float(ratio.group(2))
        if width_part > 0:
            # Prefer the preference-phrased ratio when both limit + prefer appear.
            pref_ratio = re.search(
                rf"(?:prefer|around|roughly|about|close\s+to|follow).{{0,40}}?"
                rf"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
                raw,
                flags=re.I,
            )
            lim_ratio = re.search(
                rf"(?:exceed|no\s+more|not\s+exceed|maximum|at\s+most).{{0,40}}?"
                rf"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
                raw,
                flags=re.I,
            )
            if pref_ratio and lim_ratio:
                length_part = float(pref_ratio.group(1))
                width_part = float(pref_ratio.group(2))
                parsed.constraints["max_ratio"] = [
                    float(lim_ratio.group(1)),
                    float(lim_ratio.group(2)),
                ]
            parsed.length_over_width = length_part / width_part
            parsed.constraints["preferred_ratio"] = [length_part, width_part]
            parsed.constraints["ratio_role"] = _role_from_words(
                _sentence_at(raw, ratio.start(), ratio.end()),
                default="preference",
            )
            parsed.notes.append(
                f"prefer length:width {length_part:g}:{width_part:g}"
            )

    low = re.search(
        r"low[\s-]?rise|as low as|spread\s+out|keep it low|neighborhood-scale|"
        r"fairly low|urban village|village rather than",
        raw,
        flags=re.I,
    )
    compact = re.search(r"\bcompact\b|small footprint|tight footprint|denser option", raw, flags=re.I)
    if low:
        parsed.preference = "low_rise"
    elif compact:
        parsed.preference = "compact"

    if re.search(r"\bcourtyard\b|\blawn\b|\bopen space\b|\bvillage\b", raw, flags=re.I):
        parsed.notes.append("open-space / courtyard preference noted (drawing may be unsupported)")

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
    _extract_double_height_with_together(parsed, raw, department_names)
    _extract_near_relations(parsed, raw, department_names)
    _extract_cluster_and_plaza_rules(parsed, raw, department_names)
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
    edge_sums = _extract_edge_sum_limits(raw)
    if edge_sums:
        parsed.constraints["edge_sum_limits"] = edge_sums
        for rule in edge_sums:
            parsed.notes.append(rule.get("note") or "cross-mass edge sum cap")
    _note_unknown_programs(parsed, raw, department_names)
    return parsed


_TOGETHER = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,40}?)\s+(?:and|&|/)\s+([a-z][a-z0-9&/' \-]{1,40}?)"
    r"\s+(?:should\s+|must\s+|need to\s+|have to\s+|needs?\s+to\s+)?"
    r"(?:stay|remain|go|be|sit)?\s*(?:together|with each other|in the same|"
    r"in one|in a single|close together|nearby|beside(?:\s+each\s+other)?|"
    r"next to(?:\s+each\s+other)?|near(?:\s+each\s+other)?|"
    r"grouped\s+together|share\s+one\s+mass)",
    flags=re.I,
)
_SAME_BUILDING = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,48}?)\s+(?:and|&|/)\s+([a-z][a-z0-9&/' \-]{1,48}?)"
    r"\s+(?:should\s+be\s+|must\s+be\s+|have to\s+be\s+|need to\s+be\s+)?"
    r"(?:in|share|sharing)?\s*(?:the\s+)?same\s+(?:mass|building|wing|volume|one|block|box|structure|form)"
    r"|([a-z][a-z0-9&/' \-]{1,48}?)\s+(?:must|should|has to|have to|needs? to)\s+be\s+"
    r"in\s+the\s+same\s+(?:building|mass|wing|volume)\s+as\s+"
    r"([A-Za-z][A-Za-z0-9&/' \-]{1,48})\b",
    flags=re.I,
)
_KEEP_TOGETHER = re.compile(
    r"(?:keep|put|place)\s+([a-z][a-z0-9&/' \-]{1,40}?)\s+"
    r"(?:and|&|with|/)\s+([a-z][a-z0-9&/' \-]{1,40}?)"
    r"\s+(?:together|close(?:\s+together)?|nearby)",
    flags=re.I,
)
_BESIDE = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,48}?)\s+"
    r"(?:beside|next to|near|close to|with|adjacent to)\s+"
    r"([a-z][a-z0-9&/' \-]{1,48})\b",
    flags=re.I,
)
_ADJACENT_MUST = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,48}?)\s+"
    r"(?:must|should|needs? to|has to|have to)\s+be\s+adjacent\s+to\s+"
    r"([a-z][a-z0-9&/' \-]{1,48})\b",
    flags=re.I,
)
# "custodial should be attached to dining" / "media paired with administration"
_ATTACHED_OR_PAIRED = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,48}?)\s+"
    r"(?:should\s+|must\s+|needs?\s+to\s+|has\s+to\s+|have\s+to\s+)?"
    r"(?:be\s+)?"
    r"(?:attached|paired|joined|linked|connected|grouped|tied)\s+"
    r"(?:to|with)\s+"
    r"([a-z][a-z0-9&/' \-]{1,48})\b",
    flags=re.I,
)
_APART = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,40}?)\s+(?:and|&|/)\s+([a-z][a-z0-9&/' \-]{1,40}?)"
    r"\s+(?:should\s+|must\s+)?"
    r"(?:stay|remain|be|kept)?\s*(?:apart|separate|split|away from)",
    flags=re.I,
)
_KEEP_AWAY = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,48}?)\s+(?:kept\s+)?away\s+from\s+([a-z][a-z0-9&/' \-]{1,48}?)",
    flags=re.I,
)
_SEPARATED = re.compile(
    r"([a-z][a-z0-9&/' \-]{1,48}?)\s+separated\b",
    flags=re.I,
)


_STOP = {
    "should", "shall", "must", "need", "have", "to", "stay", "remain", "go",
    "be", "sit", "keep", "put", "place", "together", "with", "each", "other",
    "in", "the", "same", "one", "a", "an", "and", "nd", "or", "of", "hsould",
    "min", "max", "minimum", "maximum", "length", "lengths", "width", "widths",
    "height", "heights", "depth", "meter", "meters", "metre", "metres",
    "foot", "feet", "ft", "stories", "floors", "levels", "mass", "masses",
    "double", "prefer", "preferred", "ratio", "long", "wide", "tall",
}


def _phrase_fits_department(phrase: str, hit: str) -> bool:
    """True when every token in phrase is justified by the matched department."""
    from .group import _norm

    name_tokens = _norm(hit).split()
    for tok in _norm(phrase).split():
        if tok in name_tokens:
            continue
        if any(nt.startswith(tok) and len(tok) >= 2 for nt in name_tokens):
            continue
        if any(tok.startswith(nt) and len(nt) >= 3 for nt in name_tokens):
            continue
        if match_department(tok, [hit]) == hit:
            continue
        return False
    return True


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
            if hit and hit not in used and _phrase_fits_department(phrase, hit):
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
        # "4 separate masses" is a count, not a keep-apart relation.
        after = text[match.end() : match.end() + 24]
        if not together and re.match(
            r"\s+(?:mass|building|volume|pavilion|block)e?s?\b", after, flags=re.I
        ):
            continue
        window = text[max(0, match.start() - 90) : match.start()]
        # Only the clause before "together", not a prior sentence's departments.
        clause = window.rsplit(".", 1)[-1]
        hits = _departments_mentioned(clause, names)
        if len(hits) >= 2:
            a, b = hits[-2], hits[-1]
            key = tuple(sorted((a, b)))
            if key not in seen and a != b:
                seen.add(key)
                pairs.append((a, b))
        elif together and clause.strip():
            leftover = clause.strip()
            if leftover.lower() not in unmatched:
                unmatched.append(leftover)

    patterns = (
        (_TOGETHER, _KEEP_TOGETHER, _SAME_BUILDING, _BESIDE, _ADJACENT_MUST, _ATTACHED_OR_PAIRED)
        if together
        else (_APART, _KEEP_AWAY)
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            groups = [g for g in match.groups() if g]
            if len(groups) < 2:
                continue
            left = re.sub(r"^(?:and|&|or|,)\s+", "", groups[0].strip(), flags=re.I)
            right = re.sub(r"^(?:and|&|or|,)\s+", "", groups[1].strip(), flags=re.I)
            a = match_department(left, names)
            b = match_department(right, names)
            if a and b and a != b:
                key = tuple(sorted((a, b)))
                if key not in seen:
                    seen.add(key)
                    pairs.append((a, b))
    return pairs


def _extract_cluster_and_plaza_rules(
    parsed: ParsedBrief, text: str, names: list[str]
) -> None:
    """Loose clusters, loading/plaza separation, visibility, housing side."""
    text_n = text.replace("\u2019", "'").replace("\u2018", "'")

    # "Food, student life, and fitness should form a loose cluster"
    for match in re.finditer(
        r"([^.!;]+?)\s+should form a (?:loose\s+)?cluster",
        text_n,
        flags=re.I,
    ):
        depts = _clause_departments(match.group(1), names)
        for i in range(len(depts)):
            for j in range(i + 1, len(depts)):
                pair = (depts[i], depts[j])
                key = tuple(sorted(pair))
                existing = {tuple(sorted(p)) for p in parsed.keep_together}
                if key not in existing:
                    parsed.keep_together.append(pair)
        if depts:
            parsed.notes.append("loose cluster: " + ", ".join(depts))

    # service/loading must not touch the main plaza
    if re.search(
        r"(?:service|loading).{0,60}?(?:must not|should not|cannot|not)\s+"
        r"(?:touch|front|adjoin|face)\s+(?:the\s+)?(?:main\s+)?plaza",
        text_n,
        flags=re.I,
    ) or re.search(
        r"(?:service|loading).{0,40}?away from\s+(?:the\s+)?(?:main\s+)?plaza",
        text_n,
        flags=re.I,
    ):
        svc = match_department("loading", names) or match_department("custodial", names)
        if svc:
            parsed.notes.append(f"{svc} must stay away from main plaza")
            for public in ("gallery", "library", "art"):
                other = match_department(public, names)
                if other and other != svc:
                    key = tuple(sorted((svc, other)))
                    existing = {tuple(sorted(p)) for p in parsed.keep_apart}
                    if key not in existing:
                        parsed.keep_apart.append((svc, other))
                    break

    # gallery should be visible from the entrance
    if re.search(
        r"(?:gallery|art).{0,40}?visible from\s+(?:the\s+)?entrance",
        text_n,
        flags=re.I,
    ):
        gal = match_department("gallery", names) or match_department("art", names)
        if gal:
            parsed.notes.append(f"{gal} should be visible from the entrance")
            parsed.notes.append(f"prefer public frontage for {gal}")

    # housing farther south / quieter
    if re.search(
        r"housing.{0,60}?(?:farther|further)\s+south|housing.{0,40}?quieter",
        text_n,
        flags=re.I,
    ):
        parsed.notes.append("housing preference: quieter, farther south")

    # art studios need north light
    if re.search(r"(?:art|studio).{0,40}?north\s+light", text_n, flags=re.I):
        art = match_department("art", names)
        if art:
            parsed.notes.append(f"{art} needs north light")

    # auditorium must have direct ground-floor access
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,40}?)\s+"
        r"(?:must\s+have|needs?|should\s+have)\s+direct\s+ground[- ]floor\s+access",
        text_n,
        flags=re.I,
    ):
        phrase = match.group(1).strip()
        dept = match_department(phrase, names) or (
            None if match_department(phrase.split()[-1], names) is None
            else match_department(phrase.split()[-1], names)
        )
        # Prefer last noun token (e.g. "the auditorium")
        if dept is None:
            dept = match_department(re.sub(r"^(?:the|a|an)\s+", "", phrase, flags=re.I), names)
        if dept:
            if dept not in parsed.pin_ground:
                parsed.pin_ground.append(dept)
            parsed.notes.append(f"{dept} needs direct ground-floor access")
        else:
            parsed.notes.append(f"{phrase.strip()} needs direct ground-floor access (unmapped)")
            if phrase.lower().strip() not in {u.lower() for u in parsed.unknown_programs}:
                # leave unknown scan to pick it up; reinforce
                pass

    # gym near dining but not share the same entrance
    near = re.search(
        r"(?:the\s+|a\s+|an\s+)?([A-Za-z][A-Za-z0-9&/' \-]{0,30}?)\s+should be near\s+"
        r"(?:the\s+|a\s+|an\s+)?([A-Za-z][A-Za-z0-9&/' \-]{0,30}?)(?:\s+but|\s*;|,|\.|$)",
        text_n,
        flags=re.I,
    )
    if near:
        def _clean(phrase: str) -> str:
            return re.sub(
                r"^(?:and|or|but|with|,)\s+(?:the\s+|a\s+|an\s+)?",
                "",
                phrase.strip(),
                flags=re.I,
            )

        a = match_department(_clean(near.group(1)), names)
        b = match_department(_clean(near.group(2)), names)
        if a and b and a != b:
            key = tuple(sorted((a, b)))
            existing = {tuple(sorted(p)) for p in parsed.keep_together}
            if key not in existing:
                parsed.keep_together.append((a, b))
            parsed.notes.append(f"{a} near {b}")
    if re.search(r"not share the same entrance|separate entrances?", text_n, flags=re.I):
        parsed.notes.append("related programs should not share the same entrance")

    # administration anywhere except quiet residential edge
    if re.search(
        r"administration.{0,80}?except.{0,40}?residential|"
        r"administration.{0,40}?almost anywhere",
        text_n,
        flags=re.I,
    ):
        admin = match_department("administration", names)
        if admin:
            parsed.notes.append(
                f"{admin} flexible placement except quiet residential edge"
            )

    # only use six if that gives more open space
    if re.search(
        r"only use (?:six|6).{0,40}?more open space|"
        r"six if.{0,40}?open space",
        text_n,
        flags=re.I,
    ):
        parsed.notes.append(
            "prefer fewer masses; use 6 only if it yields noticeably more open space"
        )

    # compact without dense-looking
    if re.search(r"compact without.{0,20}?dense", text_n, flags=re.I):
        parsed.notes.append("compact but not dense-looking")
        if parsed.preference == "balanced":
            parsed.preference = "compact"


def _extract_double_height_with_together(
    parsed: ParsedBrief, text: str, names: list[str]
) -> None:
    """Gym and dining together and double height(s) → both volumes are 2-storey."""
    for match in re.finditer(r"double[\s-]?heights?", text, flags=re.I):
        # Sentence-local only — a 100-char window before "double height" can
        # pull unrelated tokens from a prior length clause.
        clause = _sentence_at(text, match.start(), match.end())
        together = bool(
            re.search(r"together|same\s+(?:mass|building|wing)", clause, flags=re.I)
        )
        hits = _clause_departments(clause, names)
        if len(hits) < 2 and together and parsed.keep_together:
            for a, b in parsed.keep_together:
                hits = [a, b]
                break
        if not hits:
            continue
        for dept in hits:
            if dept not in parsed.double_height_departments:
                parsed.double_height_departments.append(dept)


def _extract_near_relations(parsed: ParsedBrief, text: str, names: list[str]) -> None:
    """Soft near/beside phrases already covered by pair extract; record unknowns."""
    for match in _SEPARATED.finditer(text):
        dept = match_department(match.group(1), names)
        if dept:
            # Needs a counterpart in context; note only.
            parsed.notes.append(f"prefer keeping {dept} separated from noisy uses")


def _note_unknown_programs(parsed: ParsedBrief, text: str, names: list[str]) -> None:
    """Programs the brief names that are not in this spreadsheet."""
    candidates = (
        "pool", "café", "cafe", "childcare", "daycare", "residential", "housing",
        "residence", "gallery", "retail", "maker space", "lounge", "commons",
        "auditorium", "rehearsal", "clinic", "convenience store", "event hall",
        "science labs", "pavilion", "student center", "student services",
        "wellness", "sports",
    )
    found: list[str] = []
    lower = text.lower()
    for c in candidates:
        if c in lower and match_department(c, names) is None:
            if c not in found:
                found.append(c)
    if found:
        parsed.unknown_programs = found
        parsed.notes.append("unknown programs (not in schedule): " + ", ".join(found))


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
    (("gymnasium", "gym", "fitness"), ("health", "physical", "gym")),
    (("art", "band", "music", "gallery"), ("art", "music")),
    (("dining", "cafeteria", "kitchen", "food"), ("dining", "food")),
    (("media", "library"), ("media",)),
    (("special education", "special ed"), ("special",)),
    (("academic", "classroom"), ("academic",)),
    (("student life",), ("administration", "guidance")),
    (("loading", "service", "custodial"), ("custodial", "maintenance")),
)


def _clause_departments(text: str, names: list[str]) -> list[str]:
    from .group import _norm

    found = _departments_mentioned(text, names)
    used = set(found)
    norm = f" {_norm(text)} "
    for triggers, keys in _HINTS:
        # Whole-token only — "art" must not fire inside "separate".
        if not any(re.search(rf"\b{re.escape(t)}\b", norm) for t in triggers):
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
        rf"length of (?:these|those) two.{{0,20}}?\bunder\s+{_NUM}{_FT}",
    ]
    return _first_number(patterns, text)


_RATIO_PAIR = r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)"
_MASS_REF = (
    r"(?:one|two|three|four|first|second|third|fourth|[1-4]|[a-dA-D])"
)
_EDGE_CAP = (
    r"(?:less\s+than|under|below|at\s+most|no\s+more\s+than|not\s+exceed(?:ing)?|"
    r"should\s+(?:be\s+)?(?:under|below)|must\s+(?:be\s+)?(?:under|below))"
)


def _extract_ratio_band(text: str) -> dict[str, Any] | None:
    """
    Ratio band for every mass length:width.
    e.g. "all masses should be between 2:5 and 3:4"
         "mass ratio prefer to be between 5:8 and 2:5"
    """
    patterns = [
        rf"(?:all\s+)?(?:masses|wings|buildings|bars).{{0,48}}?between\s+{_RATIO_PAIR}\s+and\s+{_RATIO_PAIR}",
        rf"(?:mass\s+)?(?:ratio|proportion|aspect(?:\s+ratio)?).{{0,48}}?between\s+{_RATIO_PAIR}\s+and\s+{_RATIO_PAIR}",
        rf"(?:mass\s+)?(?:ratio|proportion|aspect(?:\s+ratio)?).{{0,48}}?prefer.{{0,24}}?between\s+{_RATIO_PAIR}\s+and\s+{_RATIO_PAIR}",
        rf"prefer.{{0,36}}?(?:mass\s+)?(?:ratio|proportion|aspect).{{0,36}}?between\s+{_RATIO_PAIR}\s+and\s+{_RATIO_PAIR}",
        rf"between\s+{_RATIO_PAIR}\s+and\s+{_RATIO_PAIR}.{{0,36}}?(?:ratio|proportion|aspect|mass)",
        rf"(?:ratio|proportion|aspect).{{0,24}}?(?:from|of)\s+{_RATIO_PAIR}\s+(?:to|through|–|-)\s+{_RATIO_PAIR}",
        rf"from\s+{_RATIO_PAIR}\s+to\s+{_RATIO_PAIR}.{{0,36}}?(?:ratio|proportion|aspect|mass)",
        rf"(?:stay|keep|remain|sit)\s+between\s+{_RATIO_PAIR}\s+and\s+{_RATIO_PAIR}",
        rf"(?:each|every)\s+(?:mass|wing|building).{{0,36}}?between\s+{_RATIO_PAIR}\s+and\s+{_RATIO_PAIR}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.I | re.S)
        if not match:
            continue
        a, b, c, d = (float(match.group(i)) for i in range(1, 5))
        if b <= 0 or d <= 0:
            continue
        r1 = a / b
        r2 = c / d
        lo, hi = (r1, r2) if r1 <= r2 else (r2, r1)
        parts = [[a, b], [c, d]] if r1 <= r2 else [[c, d], [a, b]]
        sentence = _sentence_at(text or "", match.start(), match.end())
        role = _role_from_words(sentence, default="limitation")
        if re.search(r"\bprefer", sentence, flags=re.I):
            role = "preference"
        if role not in {"requirement", "limitation", "preference"}:
            role = "limitation"
        return {
            "lo": round(lo, 6),
            "hi": round(hi, 6),
            "parts": parts,
            "role": role,
            "start": match.start(),
            "end": match.end(),
        }
    return None


def _normalize_mass_ref(token: str) -> str:
    raw = str(token or "").strip().lower()
    mapping = {
        "one": "1",
        "first": "1",
        "a": "1",
        "two": "2",
        "second": "2",
        "b": "2",
        "three": "3",
        "third": "3",
        "c": "3",
        "four": "4",
        "fourth": "4",
        "d": "4",
    }
    return mapping.get(raw, raw)


def _extract_upper_floor_relations(
    parsed: ParsedBrief, text: str, names: list[str]
) -> None:
    """
    Prefer/require a program on the top floor and/or above another program.
    e.g. "media prefer on top floor above admin"
    """
    floor_pins = dict(parsed.constraints.get("floor_pins") or {})
    floor_kinds = dict(parsed.constraints.get("floor_pin_kinds") or {})
    stacks = list(parsed.constraints.get("stack_above") or [])
    TOP = -1  # resolve to uppermost occupied level at apply time

    def _role(clause: str, default: str = "preference") -> str:
        role = _role_from_words(clause, default=default)
        if re.search(r"\b(?:must|has\s+to|have\s+to|needs?\s+to|required)\b", clause, flags=re.I):
            role = "requirement"
        elif role == "limitation":
            role = "preference"
        if role not in {"requirement", "limitation", "preference"}:
            role = default
        return role

    def _pin_top(dept: str, clause: str) -> None:
        # Top / upper pins are not ground pins.
        if dept in parsed.pin_ground:
            parsed.pin_ground = [d for d in parsed.pin_ground if d != dept]
            kinds = dict(parsed.constraints.get("pin_ground_kind") or {})
            kinds.pop(dept, None)
            parsed.constraints["pin_ground_kind"] = kinds
        floor_pins[dept] = TOP
        floor_kinds[dept] = _role(clause)

    def _stack(above: str, below: str, clause: str) -> None:
        if above == below:
            return
        key = (above, below)
        if any((s.get("above"), s.get("below")) == key for s in stacks):
            return
        stacks.append({"above": above, "below": below, "kind": _role(clause)})

    # "media prefer on top floor above admin"
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,48}?)\s+"
        r"(?:prefer(?:s|ably|red)?|should|must|needs?\s+to|has\s+to|have\s+to)\s+"
        r"(?:to\s+be\s+|be\s+|sit\s+|go\s+|stay\s+)?"
        r"(?:on\s+)?(?:the\s+)?(?:top|upper(?:most)?|highest)\s+(?:floor|level|storey|story)"
        r"(?:\s+above\s+([A-Za-z][A-Za-z0-9&/']*(?:\s+[A-Za-z][A-Za-z0-9&/']*){0,4}))?",
        text,
        flags=re.I,
    ):
        clause = match.group(0)
        uppers = _clause_departments(match.group(1), names)
        lowers = _clause_departments(match.group(2) or "", names) if match.group(2) else []
        for dept in uppers:
            _pin_top(dept, clause)
            for below in lowers:
                _stack(dept, below, clause)

    # "prefer media on the top floor"
    for match in re.finditer(
        r"(?:prefer(?:s|ably|red)?|ideally)\s+"
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,48}?)\s+"
        r"(?:on\s+)?(?:the\s+)?(?:top|upper(?:most)?|highest)\s+(?:floor|level|storey|story)"
        r"(?:\s+above\s+([A-Za-z][A-Za-z0-9&/']*(?:\s+[A-Za-z][A-Za-z0-9&/']*){0,4}))?",
        text,
        flags=re.I,
    ):
        clause = match.group(0)
        uppers = _clause_departments(match.group(1), names)
        lowers = _clause_departments(match.group(2) or "", names) if match.group(2) else []
        for dept in uppers:
            _pin_top(dept, clause)
            for below in lowers:
                _stack(dept, below, clause)

    # "media should sit above admin" / "media above administration"
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,48}?)\s+"
        r"(?:prefer(?:s|ably|red)?\s+|should\s+|must\s+|needs?\s+to\s+|has\s+to\s+)?"
        r"(?:to\s+be\s+|be\s+|sit\s+|go\s+|stay\s+|stack(?:ed)?\s+)?"
        r"above\s+([A-Za-z][A-Za-z0-9&/']*(?:\s+[A-Za-z][A-Za-z0-9&/']*){0,4})"
        r"(?!\s+(?:grade|ground|level\s*1|the\s+second))",
        text,
        flags=re.I,
    ):
        clause = match.group(0)
        # Skip "not above grade/ground" handled elsewhere.
        if re.search(r"\b(?:not|never|cannot|can't)\b", clause, flags=re.I):
            continue
        uppers = _clause_departments(match.group(1), names)
        lowers = _clause_departments(match.group(2), names)
        if not uppers or not lowers:
            continue
        for above in uppers:
            for below in lowers:
                _stack(above, below, clause)

    if floor_pins:
        parsed.constraints["floor_pins"] = floor_pins
        parsed.constraints["floor_pin_kinds"] = floor_kinds
        parsed.notes.append(
            "upper-floor pins: "
            + ", ".join(
                f"{d}→{'top' if lvl < 0 else f'L{lvl}'}" for d, lvl in floor_pins.items()
            )
        )
    if stacks:
        parsed.constraints["stack_above"] = stacks
        parsed.notes.append(
            "stack above: "
            + ", ".join(f"{s['above']} over {s['below']}" for s in stacks)
        )


def _extract_edge_sum_limits(text: str) -> list[dict[str, Any]]:
    """
    Cross-mass edge caps, e.g.
    - long edge of mass A and short edge of mass B less than 200 ft
    - long edges of mass A and B should be less than 300 together
    """
    raw = text or ""
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def _add(parts: list[dict[str, str]], max_ft: float, note: str) -> None:
        key = (tuple((p["ref"], p["edge"]) for p in parts), round(max_ft, 3))
        if key in seen or max_ft <= 0:
            return
        seen.add(key)
        out.append(
            {
                "parts": parts,
                "max_ft": round(max_ft, 4),
                "note": note,
            }
        )

    # long of X + short of Y (either order of long/short)
    mixed = re.compile(
        rf"(?:the\s+)?(long|short)\s+edge\s+(?:from|of)\s+(?:mass|wing)\s+({_MASS_REF})"
        rf"\s+and\s+(?:the\s+)?(long|short)\s+edge\s+(?:from|of)\s+(?:mass|wing)\s+({_MASS_REF})"
        rf".{{0,48}}?{_EDGE_CAP}\s+{_NUM}{_LEN_UNIT}",
        flags=re.I | re.S,
    )
    for match in mixed.finditer(raw):
        e1, r1, e2, r2 = match.group(1), match.group(2), match.group(3), match.group(4)
        max_ft = _to_feet(_parse_num(match.group(5)), match.group(6) if match.lastindex and match.lastindex >= 6 else None)
        if max_ft is None:
            continue
        parts = [
            {"ref": _normalize_mass_ref(r1), "edge": e1.lower()},
            {"ref": _normalize_mass_ref(r2), "edge": e2.lower()},
        ]
        _add(
            parts,
            max_ft,
            f"{e1} edge mass {parts[0]['ref']} + {e2} edge mass {parts[1]['ref']} under {max_ft:g} ft",
        )

    # possessive: mass A's long edge and mass B's short edge
    poss = re.compile(
        rf"(?:mass|wing)\s+({_MASS_REF})(?:'s)?\s+(long|short)\s+edge\s+and\s+"
        rf"(?:mass|wing)\s+({_MASS_REF})(?:'s)?\s+(long|short)\s+edge"
        rf".{{0,48}}?{_EDGE_CAP}\s+{_NUM}{_LEN_UNIT}",
        flags=re.I | re.S,
    )
    for match in poss.finditer(raw):
        r1, e1, r2, e2 = match.group(1), match.group(2), match.group(3), match.group(4)
        max_ft = _to_feet(_parse_num(match.group(5)), match.group(6) if match.lastindex and match.lastindex >= 6 else None)
        if max_ft is None:
            continue
        parts = [
            {"ref": _normalize_mass_ref(r1), "edge": e1.lower()},
            {"ref": _normalize_mass_ref(r2), "edge": e2.lower()},
        ]
        _add(
            parts,
            max_ft,
            f"{e1} edge mass {parts[0]['ref']} + {e2} edge mass {parts[1]['ref']} under {max_ft:g} ft",
        )

    # same-kind edges together: long edges of A and B ... less than N
    same = re.compile(
        rf"(?:the\s+)?(long|short)\s+edges?\s+(?:of\s+)?"
        rf"(?:mass(?:es)?|wing(?:s)?)?\s*({_MASS_REF})\s+and\s+(?:(?:mass|wing)\s+)?({_MASS_REF})"
        rf".{{0,48}}?(?:together\s+)?(?:should\s+be\s+|must\s+be\s+|have\s+to\s+(?:be\s+)?)?"
        rf"{_EDGE_CAP}\s+{_NUM}{_LEN_UNIT}",
        flags=re.I | re.S,
    )
    for match in same.finditer(raw):
        edge, r1, r2 = match.group(1), match.group(2), match.group(3)
        max_ft = _to_feet(_parse_num(match.group(4)), match.group(5) if match.lastindex and match.lastindex >= 5 else None)
        if max_ft is None:
            continue
        parts = [
            {"ref": _normalize_mass_ref(r1), "edge": edge.lower()},
            {"ref": _normalize_mass_ref(r2), "edge": edge.lower()},
        ]
        _add(
            parts,
            max_ft,
            f"{edge} edges of mass {parts[0]['ref']} and {parts[1]['ref']} together under {max_ft:g} ft",
        )

    # "combined / together long edges of mass A and mass B under N"
    combined = re.compile(
        rf"(?:combined|together)\s+(long|short)\s+edges?\s+(?:of\s+)?"
        rf"(?:mass(?:es)?|wing(?:s)?)?\s*({_MASS_REF})\s+and\s+(?:(?:mass|wing)\s+)?({_MASS_REF})"
        rf".{{0,36}}?{_EDGE_CAP}\s+{_NUM}{_LEN_UNIT}",
        flags=re.I | re.S,
    )
    for match in combined.finditer(raw):
        edge, r1, r2 = match.group(1), match.group(2), match.group(3)
        max_ft = _to_feet(_parse_num(match.group(4)), match.group(5) if match.lastindex and match.lastindex >= 5 else None)
        if max_ft is None:
            continue
        parts = [
            {"ref": _normalize_mass_ref(r1), "edge": edge.lower()},
            {"ref": _normalize_mass_ref(r2), "edge": edge.lower()},
        ]
        _add(
            parts,
            max_ft,
            f"{edge} edges of mass {parts[0]['ref']} and {parts[1]['ref']} together under {max_ft:g} ft",
        )

    # "long edges of mass A and B should be less than N together"
    together_tail = re.compile(
        rf"(?:the\s+)?(long|short)\s+edges?\s+(?:of\s+)?"
        rf"(?:mass(?:es)?|wing(?:s)?)?\s*({_MASS_REF})\s+and\s+(?:(?:mass|wing)\s+)?({_MASS_REF})"
        rf".{{0,36}}?{_EDGE_CAP}\s+{_NUM}{_LEN_UNIT}\s+together",
        flags=re.I | re.S,
    )
    for match in together_tail.finditer(raw):
        edge, r1, r2 = match.group(1), match.group(2), match.group(3)
        max_ft = _to_feet(_parse_num(match.group(4)), match.group(5) if match.lastindex and match.lastindex >= 5 else None)
        if max_ft is None:
            continue
        parts = [
            {"ref": _normalize_mass_ref(r1), "edge": edge.lower()},
            {"ref": _normalize_mass_ref(r2), "edge": edge.lower()},
        ]
        _add(
            parts,
            max_ft,
            f"{edge} edges of mass {parts[0]['ref']} and {parts[1]['ref']} together under {max_ft:g} ft",
        )

    return out


def _apply_counted_masses(
    parsed: ParsedBrief, text: str, names: list[str]
) -> None:
    """
    "4 masses. Gym and dining in the same mass. Academic by itself.
    Art and music on the ground floor."
    """
    _extract_mass_count(parsed, text)

    same: list[list[str]] = []
    for match in re.finditer(
        r"in the same (?:mass|building|wing|volume|one|block|box|structure|form)",
        text,
        flags=re.I,
    ):
        window = text[max(0, match.start() - 80) : match.start()]
        clause = re.split(r"[.;:]|,\s*with\b|\bwith\b", window, flags=re.I)[-1]
        depts = _clause_departments(clause, names)
        if len(depts) >= 2 and depts not in same:
            same.append(depts)
            span = text[max(0, match.start() - 80) : match.end()]
            if re.search(r"double[\s-]?height", span, flags=re.I):
                for dept in depts:
                    if dept not in parsed.double_height_departments:
                        parsed.double_height_departments.append(dept)

    # "gym and dining should share a volume / the largest block"
    for match in re.finditer(
        r"([^.]+?)\s+should share\s+(?:a|the)\s+(?:largest\s+)?"
        r"(?:mass|building|wing|volume|block|box|structure|form)",
        text,
        flags=re.I,
    ):
        depts = _clause_departments(match.group(1), names)
        if len(depts) >= 2 and depts not in same:
            same.append(depts)

    solos: list[str] = []
    for match in re.finditer(
        r"([^.]+?)\s+by itself(?:\s+is one mass)?",
        text,
        flags=re.I,
    ):
        depts = _clause_departments(match.group(1), names)
        solos.extend(depts)
    # "Core Academic must be in its own mass"
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,48}?)\s+"
        r"(?:must\s+be|has\s+to\s+be|needs?\s+to\s+be|should\s+be)\s+"
        r"(?:in\s+)?(?:its|their)\s+own\s+(?:mass|building|volume|block|wing)",
        text,
        flags=re.I,
    ):
        depts = _clause_departments(match.group(1), names)
        for d in depts:
            if d not in solos:
                solos.append(d)

    pin_kinds: dict[str, str] = dict(parsed.constraints.get("pin_ground_kind") or {})

    def _pin(dept: str, clause: str) -> None:
        if dept not in parsed.pin_ground:
            parsed.pin_ground.append(dept)
        role = _role_from_words(clause, default="preference")
        if re.search(r"\b(?:must|has\s+to|have\s+to|needs?\s+to|required)\b", clause, flags=re.I):
            role = "requirement"
        elif role == "limitation":
            role = "preference"
        pin_kinds[dept] = role

    for match in re.finditer(
        r"(?:better\s+)?on\s+(?:the\s+)?ground\s+floor|"
        r"at grade|at ground level|ground-floor\s+frontage|"
        r"on the first floor|on floor\s*1|on level\s*1|along the first level|"
        r"prefer(?:ably)?\s+(?:on\s+)?(?:the\s+)?ground(?:\s+floor)?|"
        r"better\s+(?:on\s+)?(?:the\s+)?ground(?:\s+floor)?|"
        r"first\s+occupied\s+level|"
        r"occupy\s+(?:the\s+)?ground\s+floor|"
        r"occupies\s+(?:the\s+)?ground\s+floor|"
        r"directly\s+accessible\s+from\s+grade|"
        r"accessible\s+from\s+grade",
        text,
        flags=re.I,
    ):
        # Nearest noun phrase before the floor cue (ignore earlier "same building").
        before = text[max(0, match.start() - 64) : match.start()]
        tail = re.split(r"\b(?:and|with|while|but|,)\b", before, flags=re.I)[-1].strip()
        phrase_m = re.search(
            r"(?:(?:the|a|an)\s+)?([a-z][a-z0-9&/' \-]{1,40})\s*$",
            tail,
            flags=re.I,
        )
        clause = _sentence_at(text, match.start(), match.end())
        if phrase_m:
            phrase = phrase_m.group(1).strip(" ,;")
            if not re.search(
                r"\b(?:building|mass|wing|volume|site|project|scheme|same|box|block)\b",
                phrase,
                flags=re.I,
            ):
                local = f"{phrase} {match.group(0)}"
                for dept in _clause_departments(phrase, names):
                    _pin(dept, local)
        # Compound sentences like "Media prefer ground, admin must ground"
        # must score each comma segment on its own wording — do not let
        # "must" on admin overwrite media's prefer.
        for segment in re.split(r"\s*,\s*", clause):
            seg = segment.strip()
            if not seg:
                continue
            for dept in _clause_departments(seg, names):
                _pin(dept, seg)
    # "art should stay at grade" / "art should stay on the first floor"
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,40}?)\s+"
        r"(?:must|should|needs?\s+to|has\s+to|have\s+to)\s+"
        r"(?:stay|remain|be|occupy|occupies)?\s*"
        r"(?:at grade|on the (?:ground|first) floor|on floor\s*1|on level\s*1|"
        r"on the first occupied level|on the ground floor)",
        text,
        flags=re.I,
    ):
        for dept in _clause_departments(match.group(1), names):
            _pin(dept, match.group(0))
    # "dining hall should be on floor 1" / "has to be on level 1"
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,40}?)\s+"
        r"(?:should|must|has to|have to|needs? to)\s+be\s+on\s+(?:floor|level)\s*1\b",
        text,
        flags=re.I,
    ):
        for dept in _clause_departments(match.group(1), names):
            _pin(dept, match.group(0))
    # "keep the gallery at grade"
    for match in re.finditer(
        r"keep\s+(?:the\s+)?([A-Za-z][A-Za-z0-9&/' \-]{0,30}?)\s+at grade",
        text,
        flags=re.I,
    ):
        for dept in _clause_departments(match.group(1), names):
            _pin(dept, match.group(0))
    # "library and gallery need ground-floor frontage" / "direct ground-floor access"
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,40}?)\s+"
        r"(?:need|needs|must have|require)\s+"
        r"(?:direct\s+)?ground[- ]floor",
        text,
        flags=re.I,
    ):
        for dept in _clause_departments(match.group(1), names):
            _pin(dept, match.group(0))
    # "gallery and library need to remain at grade"
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,40}?)\s+"
        r"(?:need to remain|must remain|should remain)\s+at grade",
        text,
        flags=re.I,
    ):
        for dept in _clause_departments(match.group(1), names):
            _pin(dept, match.group(0))
    # "should not be placed above level 1" / "not located above the second level"
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,40}?)\s+"
        r"(?:should\s+not|must\s+not|cannot|can't)\s+be\s+"
        r"(?:placed|located)\s+above\s+(?:level\s*)?(?:1|one|the\s+second\s+level|grade)",
        text,
        flags=re.I,
    ):
        for dept in _clause_departments(match.group(1), names):
            _pin(dept, match.group(0))
    for match in re.finditer(
        r"(?:they|them)\s+should\s+not\s+be\s+(?:placed|located)\s+above\s+level\s*1",
        text,
        flags=re.I,
    ):
        before = text[max(0, match.start() - 100) : match.start()]
        for dept in _clause_departments(before, names):
            _pin(dept, match.group(0))
    
    for match in re.finditer(
        r"([A-Za-z][A-Za-z0-9&/' \-]{0,40}?)\s+"
        r"(?:must|should|needs?\s+to|has\s+to|have\s+to)\s+"
        r"occup(?:y|ies)\s+(?:the\s+)?ground\s+floor",
        text,
        flags=re.I,
    ):
        for dept in _clause_departments(match.group(1), names):
            _pin(dept, match.group(0))

    _extract_upper_floor_relations(parsed, text, names)

    if pin_kinds:
        parsed.constraints["pin_ground_kind"] = pin_kinds

    # Double-height role per department (prefer vs require)
    dh_kinds: dict[str, str] = dict(parsed.constraints.get("double_height_kind") or {})
    for match in re.finditer(r"double[\s-]?heights?", text, flags=re.I):
        clause = _sentence_at(text, match.start(), match.end())
        role = _role_from_words(clause, default="requirement")
        for dept in _clause_departments(clause, names):
            if dept in parsed.double_height_departments:
                dh_kinds[dept] = role
    if dh_kinds:
        parsed.constraints["double_height_kind"] = dh_kinds

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

    # "Gym and dining together" — glue pairs into existing wings, or open a
    # new wing only when neither department is already placed.
    for a, b in parsed.keep_together:
        ia = next((i for i, (_, ds) in enumerate(groups) if a in ds), None)
        ib = next((i for i, (_, ds) in enumerate(groups) if b in ds), None)
        if ia is not None and ib is not None and ia != ib:
            name_a, depts_a = groups[ia]
            _, depts_b = groups[ib]
            merged = list(depts_a)
            for d in depts_b:
                if d not in merged:
                    merged.append(d)
            groups[ia] = (name_a, merged)
            groups.pop(ib)
        elif ia is not None and ib is None:
            name_a, depts_a = groups[ia]
            if b not in depts_a:
                groups[ia] = (name_a, depts_a + [b])
        elif ib is not None and ia is None:
            name_b, depts_b = groups[ib]
            if a not in depts_b:
                groups[ib] = (name_b, [a] + depts_b)
        elif ia is None and ib is None:
            add(f"Mass {n + 1}", [a, b])

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


def _extract_mass_count(parsed: ParsedBrief, text: str) -> None:
    """Exact counts, word counts, and ranges like 4-6 / four or five."""
    # Ranges first so "between 4 and 6" is not eaten as a single 4.
    range_pat = re.search(
        rf"(?:between|from)\s+({_WORD})\s+and\s+({_WORD})\s+(?:{_MASS_NOUN}|feels\s+right)"
        rf"|({_WORD})\s*(?:to|–|-|—|/|or)\s*({_WORD})\s+(?:smaller\s+|small\s+|larger\s+)?"
        rf"(?:{_MASS_NOUN}|feels\s+right)"
        rf"|({_WORD})\s+or\s+({_WORD})\s+(?:larger\s+)?(?:{_MASS_NOUN})"
        rf"|somewhere\s+between\s+({_WORD})\s+and\s+({_WORD})\s+feels\s+right"
        rf"|({_WORD})\s+or\s+({_WORD})\s+massing\s+pieces"
        rf"|({_WORD})\s+to\s+({_WORD})\s+{_MASS_NOUN}",
        text,
        flags=re.I,
    )
    if range_pat:
        g = [x for x in range_pat.groups() if x]
        if len(g) >= 2:
            a, b = _word_or_digit(g[0]), _word_or_digit(g[1])
            if a and b:
                lo, hi = sorted((a, b))
                parsed.mass_count_min = lo
                parsed.mass_count_max = hi
                parsed.mass_count = hi
                parsed.constraints["mass_count_role"] = _role_from_words(
                    _sentence_at(text, range_pat.start(), range_pat.end()),
                    default="requirement",
                )
                parsed.notes.append(f"mass count range {lo}-{hi}")

    at_least = re.search(
        rf"(?:at\s+least|no\s+fewer\s+than)\s+({_WORD})\s+{_MASS_NOUN}",
        text,
        flags=re.I,
    )
    if at_least:
        n = _word_or_digit(at_least.group(1))
        clause = _sentence_at(text, at_least.start(), at_least.end()).lower()
        # "at least 2 masses should remain under 3 stories" is a story rule, not a count min.
        if n and not re.search(
            r"remain under|stay under|under\s+\d+\s*(?:stor|floor|level)", clause
        ):
            parsed.mass_count_min = n if parsed.mass_count_min is None else min(parsed.mass_count_min, n)
            if parsed.mass_count is None:
                parsed.mass_count = n
            parsed.constraints["mass_count_role"] = _role_from_words(
                clause, default="requirement"
            )
            parsed.notes.append(f"mass count min {n}")

    at_most = re.search(
        rf"(?:no\s+more\s+than|not\s+more\s+than|at\s+most)\s+({_WORD})\s+{_MASS_NOUN}"
        rf"|(?:count|masses|mass\s+count)\s+should\s+not\s+exceed\s+({_WORD})"
        rf"|(?:should\s+not|must\s+not|cannot|can't)\s+exceed\s+({_WORD})\s+{_MASS_NOUN}",
        text,
        flags=re.I,
    )
    if at_most:
        token = next((g for g in at_most.groups() if g), None)
        n = _word_or_digit(str(token)) if token else None
        if n:
            clause = _sentence_at(text, at_most.start(), at_most.end()).lower()
            if not re.search(r"exceed\s+\d+\s+(?:stor|floor|level)", clause):
                parsed.mass_count_max = n if parsed.mass_count_max is None else max(parsed.mass_count_max, n)
                if parsed.mass_count is None:
                    parsed.mass_count = n
                parsed.notes.append(f"mass count max {n}")

    pref = re.search(
        rf"({_WORD})\s+{_MASS_NOUN}\s+"
        rf"(?:would\s+be\s+)?(?:my\s+)?(?:preferable|preferred(?:\s+option)?|ideal)"
        rf"(?:\s+if\s+the\s+site\s+allows(?:\s+it)?)?"
        rf"|prefer(?:ably|red)?\s+({_WORD})\s+{_MASS_NOUN}"
        rf"|(?:ideally|prefer)\s+({_WORD})\s+{_MASS_NOUN}",
        text,
        flags=re.I,
    )
    if pref:
        token = next((g for g in pref.groups() if g), None)
        n = _word_or_digit(str(token)) if token else None
        if n:
            parsed.constraints["preferred_mass_count"] = n
            parsed.notes.append(f"preferred mass count {n}")
            if parsed.mass_count is None and parsed.mass_count_min is None:
                parsed.mass_count = n
                parsed.constraints["mass_count_role"] = "preference"

    if parsed.mass_count_min is not None and parsed.mass_count_max is not None:
        parsed.mass_count = parsed.mass_count_max
        parsed.constraints.setdefault("mass_count_role", "requirement")
        return

    plus = re.search(
        rf"({_WORD})\s+main\s+(?:masses|volumes|blocks|buildings).{{0,80}}?\bplus\s+({_WORD})\s+"
        rf"(?:smaller\s+)?(?:pavilion|volume|building|mass|box)",
        text,
        flags=re.I,
    )
    if plus:
        a, b = _word_or_digit(plus.group(1)), _word_or_digit(plus.group(2))
        if a and b:
            parsed.mass_count = a + b
            parsed.constraints["mass_count_role"] = _role_from_words(
                _sentence_at(text, plus.start(), plus.end()), default="requirement"
            )
            parsed.notes.append(f"mass count {a}+{b} pavilion = {a + b}")
            return

    and_pav = re.search(
        rf"({_WORD})\s+main\s+(?:masses|volumes|blocks|buildings)\s+and\s+({_WORD})\s+"
        rf"(?:smaller\s+)?(?:standalone\s+)?(?:pavilion|box|volume|form)",
        text,
        flags=re.I,
    )
    if and_pav:
        a, b = _word_or_digit(and_pav.group(1)), _word_or_digit(and_pav.group(2))
        if a and b:
            parsed.mass_count = a + b
            parsed.constraints["mass_count_role"] = _role_from_words(
                _sentence_at(text, and_pav.start(), and_pav.end()), default="requirement"
            )
            parsed.notes.append(f"mass count {a}+{b} pavilion = {a + b}")
            return

    patterns = [
        rf"(?:exactly|scheme needs exactly)\s+(\d+)\s+{_MASS_NOUN}",
        rf"\b({_WORD})\s+(?:separate\s+|main\s+|primary\s+|small\s+|larger\s+|large\s+|chunky\s+|"
        rf"built\s+|standalone\s+|low-rise\s+)?"
        rf"(?:pavilion-like\s+)?{_MASS_NOUN}\b",
        rf"(?:use|make|want|need|have|into|organize.*?into|arrange|contain|include|require|requires)\s+"
        rf"(?:exactly\s+)?({_WORD})\s+"
        rf"(?:main\s+|primary\s+|small\s+|large\s+|chunky\s+|built\s+|standalone\s+|low-rise\s+)?"
        rf"(?:separate\s+)?{_MASS_NOUN}",
        rf"(?:broken into|organize the project into|scheme needs exactly|"
        rf"break the project into|think of the project as)\s+"
        rf"({_WORD})\s+(?:small\s+|large\s+|chunky\s+|built\s+)?"
        rf"(?:pavilion-like\s+)?{_MASS_NOUN}",
        rf"({_WORD})\s+(?:built\s+)?{_MASS_NOUN}\s+should be enough",
        rf"campus should have\s+({_WORD})\s+{_MASS_NOUN}",
        rf"project should have exactly\s+({_WORD})\s+{_MASS_NOUN}",
        rf"(?:there\s+)?(?:need|needs|must)\s+(?:to\s+be\s+)?({_WORD})\s+{_MASS_NOUN}",
        rf"I need\s+({_WORD})\s+(?:pieces\s+of\s+massing|volumes|{_MASS_NOUN})",
        rf"Make\s+({_WORD})\s+{_MASS_NOUN}",
        rf"design can have\s+({_WORD})\s+towers",
        rf"around\s+({_WORD})\s+(?:pavilions|blocks|masses|structures)",
        rf"(?:scheme|plan|project)\s+(?:must|needs?\s+to)\s+(?:contain|include|use)\s+"
        rf"({_WORD})\s+{_MASS_NOUN}",
        rf"({_WORD})\s+{_MASS_NOUN}\s+in\s+total",
    ]
    best_n: int | None = None
    best_pos = 10**9
    best_end = 0
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            n = _word_or_digit(match.group(1))
            if not n:
                continue
            prefix = text[max(0, match.start() - 18) : match.start()].lower()
            if re.search(r"\b(?:quieter|other|remaining|those|these|narrow|share\s+one|one)\s+$", prefix):
                continue
            # Skip "share one mass" / "in one mass" relational counts
            span = match.group(0).lower()
            if re.search(r"\b(?:share|same|own|single)\b", text[max(0, match.start()-24):match.start()].lower()):
                continue
            sent = _sentence_at(text, match.start(), match.end()).lower()
            if parsed.mass_count is not None and re.search(
                r"prefer|ideal|preferable|if\s+the\s+site", sent
            ):
                continue
            if match.start() < best_pos:
                best_pos = match.start()
                best_end = match.end()
                best_n = n
    if best_n is not None and parsed.mass_count is None:
        parsed.mass_count = best_n
        parsed.constraints["mass_count_role"] = _role_from_words(
            _sentence_at(text, best_pos, best_end), default="requirement"
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
    if parsed.mass_count is None and parsed.open_slots is None:
        # The user did not ask for a mass count. Family grouping owns leftovers.
        return
    slots = parsed.open_slots if parsed.open_slots is not None else 1
    proposal = _proposal_for_free(reading, free, slots, parsed.pin_ground)
    if proposal is None:
        proposal = _pack_free(free, max(1, slots))
        # A keep-together pair still in `free` must not end in different packs.
        pending = [
            (a, b)
            for a, b in parsed.keep_together
            if a in free and b in free
        ]
        if pending:
            from .group import _UnionFind

            uf = _UnionFind([d for group in proposal for d in group])
            for a, b in pending:
                uf.union(a, b)
            merged: dict[str, list[str]] = {}
            for group in proposal:
                root = uf.find(group[0])
                merged.setdefault(root, [])
                for d in group:
                    if d not in merged[root]:
                        merged[root].append(d)
            proposal = list(merged.values())
            while len(proposal) > max(1, slots):
                proposal.sort(key=len)
                proposal.append(proposal.pop(0) + proposal.pop(0))
    groups = proposal
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
    if (
        parsed.mass_count_min is not None
        and parsed.mass_count_max is not None
        and parsed.mass_count_min != parsed.mass_count_max
    ):
        requirements.append(
            {
                "kind": "requirement",
                "lever": "mass_count",
                "text": "range",
                "value": [parsed.mass_count_min, parsed.mass_count_max],
            }
        )
        limitations.append(
            {
                "kind": "limitation",
                "lever": "mass_count",
                "text": "max",
                "value": parsed.mass_count_max,
            }
        )
    elif parsed.mass_count_min is not None and parsed.mass_count_max is None:
        requirements.append(
            {
                "kind": "requirement",
                "lever": "mass_count",
                "text": "min",
                "value": parsed.mass_count_min,
            }
        )
    elif parsed.mass_count_max is not None and parsed.mass_count_min is None:
        limitations.append(
            {
                "kind": "limitation",
                "lever": "mass_count",
                "text": "max",
                "value": parsed.mass_count_max,
            }
        )
    elif parsed.mass_count:
        mass_role = str(
            parsed.constraints.get("mass_count_role")
            or _role_from_words("masses", default="requirement")
        )
        entry = {
            "kind": mass_role
            if mass_role in {"requirement", "limitation", "preference"}
            else "requirement",
            "lever": "mass_count",
            "text": "",
            "value": parsed.mass_count,
        }
        {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[entry["kind"]].append(entry)
    for a, b in parsed.keep_together:
        requirements.append(
            {"kind": "requirement", "lever": "keep_together", "text": "", "departments": [a, b]}
        )
    for a, b in parsed.keep_apart:
        requirements.append(
            {"kind": "requirement", "lever": "keep_apart", "text": "", "departments": [a, b]}
        )
    dh_kinds = parsed.constraints.get("double_height_kind") or {}
    for dept in parsed.double_height_departments:
        dh_role = str(dh_kinds.get(dept) or "requirement")
        entry = {
            "kind": dh_role if dh_role in {"requirement", "limitation", "preference"} else "requirement",
            "lever": "double_height",
            "text": "",
            "departments": [dept],
        }
        {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[entry["kind"]].append(entry)
    if parsed.constraints.get("preferred_mass_count"):
        preferences.append(
            {
                "kind": "preference",
                "lever": "mass_count",
                "text": "preferred",
                "value": parsed.constraints["preferred_mass_count"],
            }
        )
    if parsed.constraints.get("max_edge_ft"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "max_edge",
                "text": "every mass edge",
                "value": parsed.constraints["max_edge_ft"],
                "unit": "ft",
            }
        )
    elif parsed.constraints.get("length_limit_is_cap") and parsed.constraints.get("max_building_length_ft"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "max_length",
                "text": "",
                "value": parsed.constraints["max_building_length_ft"],
                "unit": "ft",
            }
        )
    if parsed.constraints.get("min_edge_ft"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "min_edge",
                "text": "every mass edge",
                "value": parsed.constraints["min_edge_ft"],
                "unit": "ft",
            }
        )
    if parsed.constraints.get("max_total_length_ft"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "site_length",
                "text": "",
                "value": parsed.constraints["max_total_length_ft"],
                "unit": "ft",
            }
        )
    if parsed.constraints.get("max_building_width_ft"):
        edge = parsed.constraints.get("max_edge_ft")
        width_cap = float(parsed.constraints["max_building_width_ft"])
        if edge is None or abs(width_cap - float(edge)) > 0.05:
            limitations.append(
                {
                    "kind": "limitation",
                    "lever": "max_width",
                    "text": "",
                    "value": width_cap,
                    "unit": "ft",
                }
            )
    if parsed.constraints.get("max_height_ft"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "max_height",
                "text": "",
                "value": parsed.constraints["max_height_ft"],
                "unit": "ft",
            }
        )
    if parsed.constraints.get("hard_max_stories"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "hard_max_stories",
                "text": "",
                "value": parsed.constraints["hard_max_stories"],
            }
        )
    elif parsed.max_stories:
        story_role = str(parsed.constraints.get("story_role") or "limitation")
        if story_role == "preference":
            preferences.append(
                {
                    "kind": "preference",
                    "lever": "story_range",
                    "text": "",
                    "value": [
                        parsed.constraints.get("stories_min"),
                        parsed.max_stories,
                    ],
                }
            )
        elif story_role == "requirement":
            requirements.append(
                {
                    "kind": "requirement",
                    "lever": "max_stories",
                    "text": "",
                    "value": parsed.max_stories,
                }
            )
        else:
            limitations.append(
                {"kind": "limitation", "lever": "max_stories", "text": "", "value": parsed.max_stories}
            )
    if parsed.constraints.get("max_gfa_sf") or parsed.constraints.get("target_gfa_sf"):
        gfa = parsed.constraints.get("max_gfa_sf") or parsed.constraints.get("target_gfa_sf")
        limitations.append(
            {
                "kind": "limitation",
                "lever": "gfa",
                "text": "max" if parsed.constraints.get("max_gfa_sf") else "target",
                "value": gfa,
                "unit": "sf",
            }
        )
    if parsed.constraints.get("max_footprint_sf"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "max_footprint",
                "text": "",
                "value": parsed.constraints["max_footprint_sf"],
                "unit": "sf",
            }
        )
    if parsed.constraints.get("preferred_footprint_sf"):
        preferences.append(
            {
                "kind": "preference",
                "lever": "preferred_footprint",
                "text": "",
                "value": parsed.constraints["preferred_footprint_sf"],
                "unit": "sf",
            }
        )
    elif parsed.constraints.get("prefer_larger_footprint"):
        preferences.append(
            {
                "kind": "preference",
                "lever": "preferred_footprint",
                "text": "larger",
            }
        )
    if parsed.constraints.get("preferred_open_space_pct"):
        preferences.append(
            {
                "kind": "preference",
                "lever": "open_space",
                "text": "preferred",
                "value": parsed.constraints["preferred_open_space_pct"],
                "unit": "%",
            }
        )
    for dept in parsed.pin_ground:
        pin_kinds = parsed.constraints.get("pin_ground_kind") or {}
        pin_role = str(pin_kinds.get(dept) or "preference")
        if pin_role not in {"requirement", "limitation", "preference"}:
            pin_role = "preference"
        entry = {
            "kind": pin_role,
            "lever": "pin_ground",
            "text": "",
            "departments": [dept],
        }
        {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[pin_role].append(entry)
    floor_pins = parsed.constraints.get("floor_pins") or {}
    floor_kinds = parsed.constraints.get("floor_pin_kinds") or {}
    for dept, level in floor_pins.items():
        pin_role = str(floor_kinds.get(dept) or "preference")
        if pin_role not in {"requirement", "limitation", "preference"}:
            pin_role = "preference"
        try:
            lvl = int(level)
        except (TypeError, ValueError):
            lvl = -1
        entry = {
            "kind": pin_role,
            "lever": "pin_floor",
            "text": "top floor" if lvl < 0 else f"level {lvl}",
            "value": lvl,
            "departments": [dept],
        }
        {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[pin_role].append(entry)
    for rel in parsed.constraints.get("stack_above") or []:
        if not isinstance(rel, dict):
            continue
        above = rel.get("above")
        below = rel.get("below")
        if not above or not below:
            continue
        role = str(rel.get("kind") or "preference")
        if role not in {"requirement", "limitation", "preference"}:
            role = "preference"
        entry = {
            "kind": role,
            "lever": "stack_above",
            "text": f"{above} above {below}",
            "departments": [above, below],
        }
        {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[role].append(entry)
    if parsed.preference and parsed.preference != "balanced":
        preferences.append(
            {"kind": "preference", "lever": "scheme_preference", "text": parsed.preference}
        )
    if parsed.constraints.get("preferred_stories"):
        preferences.append(
            {
                "kind": "preference",
                "lever": "preferred_stories",
                "text": "",
                "value": parsed.constraints["preferred_stories"],
            }
        )
    if parsed.constraints.get("stories_min") and not parsed.constraints.get("stories_max"):
        limitations.append(
            {
                "kind": "limitation",
                "lever": "min_stories",
                "text": "",
                "value": parsed.constraints["stories_min"],
            }
        )
    if parsed.constraints.get("stories_min") or parsed.constraints.get("stories_max"):
        if not any(
            c.get("lever") in {"story_range", "preferred_stories", "min_stories"}
            for c in preferences + limitations + requirements
        ):
            story_role = str(parsed.constraints.get("story_role") or "preference")
            entry = {
                "kind": story_role if story_role in {"requirement", "limitation", "preference"} else "preference",
                "lever": "story_range" if parsed.constraints.get("stories_max") else "min_stories",
                "text": "",
                "value": [
                    parsed.constraints.get("stories_min"),
                    parsed.constraints.get("stories_max"),
                ]
                if parsed.constraints.get("stories_max")
                else parsed.constraints.get("stories_min"),
            }
            {
                "requirement": requirements,
                "limitation": limitations,
                "preference": preferences,
            }[entry["kind"]].append(entry)
    if parsed.constraints.get("min_open_space_pct"):
        open_role = str(parsed.constraints.get("open_space_role") or "preference")
        if open_role not in {"requirement", "limitation", "preference"}:
            open_role = "preference"
        entry = {
            "kind": open_role,
            "lever": "open_space",
            "text": "",
            "value": parsed.constraints["min_open_space_pct"],
            "unit": "%",
        }
        {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[open_role].append(entry)
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
    if parsed.constraints.get("min_mass_area_sf") or parsed.constraints.get("preferred_min_area_sf"):
        area = parsed.constraints.get("preferred_min_area_sf") or parsed.constraints.get(
            "min_mass_area_sf"
        )
        area_role = (
            "preference"
            if parsed.constraints.get("preferred_min_area_sf")
            else "limitation"
        )
        entry = {
            "kind": area_role,
            "lever": "min_area",
            "text": "",
            "value": area,
            "unit": "sf",
        }
        {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[area_role].append(entry)
    if parsed.length_over_width:
        ratio_parts = parsed.constraints.get("preferred_ratio")
        ratio_role = str(parsed.constraints.get("ratio_role") or "preference")
        if ratio_role not in {"requirement", "limitation", "preference"}:
            ratio_role = "preference"
        # When a hard ratio band exists, the midpoint soft target stays off the briefing
        # as a separate preference — the band itself is the limitation.
        if not parsed.constraints.get("ratio_band"):
            entry = {
                "kind": ratio_role,
                "lever": "ratio",
                "text": (
                    f"{ratio_parts[0]:g}:{ratio_parts[1]:g} either way"
                    if isinstance(ratio_parts, (list, tuple)) and len(ratio_parts) >= 2
                    else ""
                ),
                "value": (
                    list(ratio_parts)
                    if isinstance(ratio_parts, (list, tuple))
                    else parsed.length_over_width
                ),
            }
            {
                "requirement": requirements,
                "limitation": limitations,
                "preference": preferences,
            }[ratio_role].append(entry)
        if parsed.constraints.get("max_ratio"):
            mr = parsed.constraints["max_ratio"]
            limitations.append(
                {
                    "kind": "limitation",
                    "lever": "ratio",
                    "text": f"{mr[0]:g}:{mr[1]:g}",
                    "value": list(mr),
                }
            )
    band = parsed.constraints.get("ratio_band")
    band_parts = parsed.constraints.get("ratio_band_parts")
    if isinstance(band, (list, tuple)) and len(band) >= 2:
        if isinstance(band_parts, (list, tuple)) and len(band_parts) >= 2:
            text = (
                f"{band_parts[0][0]:g}:{band_parts[0][1]:g}–"
                f"{band_parts[1][0]:g}:{band_parts[1][1]:g} either way"
            )
        else:
            text = f"{band[0]:g}–{band[1]:g} length/width either way"
        band_role = str(parsed.constraints.get("ratio_band_role") or "limitation")
        if band_role not in {"requirement", "limitation", "preference"}:
            band_role = "limitation"
        entry = {
            "kind": band_role,
            "lever": "ratio_band",
            "text": text,
            "value": [float(band[0]), float(band[1])],
            "unit": "ratio",
        }
        {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[band_role].append(entry)
    for rule in parsed.constraints.get("edge_sum_limits") or []:
        if not isinstance(rule, dict):
            continue
        parts = rule.get("parts") or []
        label = " + ".join(
            f"{p.get('edge')} edge mass {p.get('ref')}" for p in parts if isinstance(p, dict)
        )
        limitations.append(
            {
                "kind": "limitation",
                "lever": "edge_sum",
                "text": rule.get("note") or label,
                "value": rule.get("max_ft"),
                "unit": "ft",
                "parts": list(parts),
            }
        )
    for clause in parsed.dimensions:
        entry = {
            "kind": clause.get("kind")
            or _role_from_words(
                str(clause.get("text") or ""),
                default={
                    "exact": "requirement",
                    "max": "limitation",
                    "min": "limitation",
                    "preferred": "preference",
                }.get(str(clause.get("mode") or "exact"), "requirement"),
            ),
            "lever": (
                f"{clause.get('mode')}_{clause.get('lever')}"
                if clause.get("mode") and clause.get("mode") != "exact"
                else f"exact_{clause.get('lever')}"
                if clause.get("mode") == "exact"
                else clause.get("lever")
            ),
            "text": clause.get("text") or "",
            "value": clause.get("value"),
            "unit": clause.get("unit") or "ft",
            "departments": list(clause.get("departments") or []),
            "mode": clause.get("mode"),
            "scope": clause.get("scope"),
        }
        # Prefer stable lever names for UI / LLM: exact_width, max_width, preferred_width, …
        lever = str(clause.get("lever") or "width")
        mode = str(clause.get("mode") or "exact")
        if mode == "exact":
            entry["lever"] = f"exact_{lever}"
        elif mode == "max":
            entry["lever"] = f"max_{lever}"
        elif mode == "min":
            entry["lever"] = f"min_{lever}"
        else:
            entry["lever"] = f"preferred_{lever}"
        if lever == "length" and mode == "max":
            entry["lever"] = "max_edge"
            entry["text"] = (
                "every edge of this mass"
                if entry.get("departments")
                else "every mass edge"
            )
        # Global max_width already emitted from constraints — skip duplicate.
        if (
            entry["lever"] == "max_width"
            and not entry["departments"]
            and parsed.constraints.get("max_building_width_ft") is not None
            and abs(float(entry["value"]) - float(parsed.constraints["max_building_width_ft"])) < 0.01
        ):
            continue
        if (
            entry["lever"] == "max_edge"
            and not entry["departments"]
            and parsed.constraints.get("max_edge_ft") is not None
            and abs(float(entry["value"] or 0) - float(parsed.constraints["max_edge_ft"])) < 0.05
        ):
            continue
        bucket = {
            "requirement": requirements,
            "limitation": limitations,
            "preference": preferences,
        }[entry["kind"]]
        bucket.append(entry)
    return {
        "requirements": requirements,
        "limitations": limitations,
        "preferences": preferences,
    }


def _apply_dimension_clauses(session: StudySession, parsed: ParsedBrief) -> None:
    """Map scoped width/depth/length clauses onto mass constraints after grouping."""
    from .tools import set_constraint

    dept_widths: dict[str, float] = {}
    raw = parsed.constraints.get("department_widths")
    if isinstance(raw, dict):
        dept_widths.update({str(k): float(v) for k, v in raw.items()})
    dept_edges: dict[str, float] = {}
    raw_edges = parsed.constraints.get("department_max_edge_ft")
    if isinstance(raw_edges, dict):
        dept_edges.update({str(k): float(v) for k, v in raw_edges.items()})

    for clause in parsed.dimensions:
        lever = str(clause.get("lever") or "")
        mode = str(clause.get("mode") or "exact")
        depts = [str(d) for d in (clause.get("departments") or [])]
        if lever not in {"width", "length"} or not depts:
            continue
        try:
            value = float(clause["value"])
        except (TypeError, ValueError, KeyError):
            continue
        if lever == "length" and mode == "max":
            for dept in depts:
                dept_edges[dept] = value
            for mass in session.masses:
                if not any(d in mass.departments for d in depts):
                    continue
                key = f"{mass.id}_max_edge_ft"
                existing = session.constraints.get(key)
                cap = value if existing is None else min(float(existing), value)
                set_constraint(session, key, cap)
            continue
        if lever != "width":
            continue
        for dept in depts:
            dept_widths[dept] = value
        for mass in session.masses:
            if not any(d in mass.departments for d in depts):
                continue
            if mode in {"exact", "preferred"}:
                set_constraint(session, f"{mass.id}_width_ft", value)
                if "academic" in mass.id.lower() or any(
                    "academic" in d.lower() for d in mass.departments
                ):
                    session.constraints["academic_width_ft"] = value
            elif mode == "max":
                key = f"{mass.id}_width_ft"
                if key not in session.constraints:
                    set_constraint(session, key, value)

    if dept_widths:
        session.constraints["department_widths"] = dept_widths
    if dept_edges:
        session.constraints["department_max_edge_ft"] = dept_edges
    if (dept_widths or dept_edges) and hasattr(session, "save"):
        session.save()


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
                if isinstance(value, (list, tuple)):
                    nums = [int(v) for v in value if v is not None and v != ""]
                    if nums:
                        parsed.mass_count_min = min(nums)
                        parsed.mass_count_max = max(nums)
                        parsed.mass_count = max(nums)
                else:
                    parsed.mass_count = max(1, int(value))
            elif lever == "double_height":
                for dept in depts:
                    if dept not in parsed.double_height_departments:
                        parsed.double_height_departments.append(dept)
            elif lever == "exact_length" and value and "exact_building_length_ft" not in parsed.constraints:
                parsed.constraints["exact_building_length_ft"] = float(value)
            elif lever in {"exact_width", "preferred_width", "dept_width"} and value and depts:
                mode = "preferred" if "preferred" in lever else "exact"
                _append_dimension(
                    parsed,
                    lever="width",
                    mode=mode,
                    value_ft=float(value),
                    departments=depts,
                    scope="department",
                    text=str(clause.get("text") or ""),
                )
                widths = dict(parsed.constraints.get("department_widths") or {})
                for dept in depts:
                    widths[dept] = float(value)
                parsed.constraints["department_widths"] = widths
        elif kind == "limitation":
            if lever in {"max_length", "max_edge"} and value:
                if depts:
                    _append_dimension(
                        parsed,
                        lever="length",
                        mode="max",
                        value_ft=float(value),
                        departments=depts,
                        scope="department",
                        text=str(clause.get("text") or ""),
                    )
                    edges = dict(parsed.constraints.get("department_max_edge_ft") or {})
                    for dept in depts:
                        edges[dept] = float(value)
                    parsed.constraints["department_max_edge_ft"] = edges
                    parsed.pair_length_ft = None
                elif "max_building_length_ft" not in parsed.constraints:
                    _stamp_all_edge_cap(parsed, float(value), note=False)
                    parsed.pair_length_ft = None
            elif lever == "max_height" and value and parsed.max_stories is None:
                parsed.max_stories = max(1, int(value))
            elif lever in {"site_length", "max_total_length"} and value:
                if "max_building_length_ft" not in parsed.constraints:
                    parsed.constraints["max_total_length_ft"] = float(value)
            elif lever == "max_width" and value:
                if depts:
                    _append_dimension(
                        parsed,
                        lever="width",
                        mode="max",
                        value_ft=float(value),
                        departments=depts,
                        scope="department",
                        text=str(clause.get("text") or ""),
                    )
                elif "max_building_width_ft" not in parsed.constraints:
                    parsed.constraints["max_building_width_ft"] = float(value)
            elif lever == "max_stories" and value and parsed.max_stories is None:
                parsed.max_stories = max(1, int(value))
        elif kind == "preference":
            if lever == "pin_ground":
                for dept in depts:
                    if dept not in parsed.pin_ground:
                        parsed.pin_ground.append(dept)
            elif lever in {"preferred_width", "exact_width"} and value and depts:
                _append_dimension(
                    parsed,
                    lever="width",
                    mode="preferred" if lever.startswith("preferred") else "exact",
                    value_ft=float(value),
                    departments=depts,
                    scope="department",
                    text=str(clause.get("text") or ""),
                )
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
                session.constraints["topology_locked"] = True
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
                session.constraints["topology_locked"] = True
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
    client: Any = None,
    plan: Any = None,
) -> dict[str, Any]:
    """Write the parsed brief onto the session and optionally search for a scheme."""
    from .config import load_project_config
    from .tools import set_constraint, set_grouping

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
        if parsed.keep_apart:
            session.constraints["keep_apart"] = [list(p) for p in parsed.keep_apart]
        session.save()

    _apply_dimension_clauses(session, parsed)

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
    floor_pins = dict(parsed.constraints.get("floor_pins") or {})
    if floor_pins:
        from .tools import pin_department_to_floor

        pref_stories = parsed.constraints.get("preferred_stories")
        if pref_stories is not None:
            top_level = max(0, int(round(float(pref_stories))) - 1)
        elif parsed.max_stories is not None:
            top_level = max(0, int(parsed.max_stories) - 1)
        else:
            top_level = 2
        for dept, level in floor_pins.items():
            try:
                lvl = int(level)
            except (TypeError, ValueError):
                lvl = -1
            if lvl < 0:
                lvl = int(top_level)
            pin_department_to_floor(session, dept, lvl)
            reading_notes.append(f"pinned {dept} to level {lvl}")
    if parsed.constraints.get("stack_above"):
        session.constraints["stack_above"] = list(parsed.constraints["stack_above"])
    story_lock: dict[str, int] = {}
    dh = set(parsed.double_height_departments)
    preferred_stories = parsed.constraints.get("preferred_stories")
    for mass in session.masses:
        depts = set(mass.departments)
        if depts and depts <= dh:
            mass.story_count = 2
            story_lock[mass.id] = 2
        elif preferred_stories:
            target = max(1, int(preferred_stories))
            if parsed.max_stories:
                target = min(target, int(parsed.max_stories))
            mass.story_count = target
        elif parsed.max_stories:
            mass.story_count = min(mass.story_count or parsed.max_stories, parsed.max_stories)
    if preferred_stories is not None:
        set_constraint(session, "preferred_stories", float(preferred_stories))
    # Ground-floor pins place a program on L0. They do not force a one-story mass;
    # that would stretch a singleton wing past a site frontage for no good reason.
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
    explored = None
    if search and session.masses:
        from .explore.controller import run_search

        explored = run_search(session, mode="cover", client=client, plan=plan)
        solved = explored.get("solved")
        searched = {
            "ok": True,
            "found": len(session.last_search or []),
            "schemes": session.last_search or [],
            "notes": [explored.get("note") or ""],
            "archive": explored.get("archive"),
        }

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
        "explore": (session.constraints.get("explore") or None),
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
