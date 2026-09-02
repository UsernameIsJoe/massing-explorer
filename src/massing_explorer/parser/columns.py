from __future__ import annotations

import re
from typing import Any


# Canonical field → header keywords (lowercase substrings)
COLUMN_KEYWORDS: dict[str, list[str]] = {
    "room_name": [
        "room type",
        "room name",
        "room_name",
        "space",
        "space name",
        "room",
        "description",
        "program element",
        "use",
    ],
    "area_each": [
        "room nfa",
        "nfa",
        "net area",
        "area each",
        "area/room",
        "unit area",
        "size",
        "area sf",
        "area_sf",
        "area (sf)",
        "nsf",
    ],
    "qty": [
        "# of rooms",
        "no. of rooms",
        "number of rooms",
        "qty",
        "quantity",
        "count",
        "# rooms",
        "rooms",
    ],
    "area_total": [
        "area totals",
        "area total",
        "total area",
        "total sf",
        "extended",
        "line total",
    ],
    "comments": ["comments", "comment", "notes", "remarks", "description"],
    "department": ["department", "dept", "program", "division", "category"],
}


FOOTER_KEYWORDS = [
    "total building",
    "gross floor area",
    "net floor area",
    "grossing factor",
    "student capacity",
    "enrollment",
    "proposed student",
    "design enrollment",
    "gfa / nfa",
    "gfa/nfa",
]

INSTRUCTION_PREFIXES = ("(", "[", "note:", "list rooms")


def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def detect_columns(header_row: list[Any]) -> dict[str, int]:
    """Map canonical field names to column indices from a header row."""
    mapping: dict[str, int] = {}
    normalized = [normalize_header(c) for c in header_row]

    for field, keywords in COLUMN_KEYWORDS.items():
        if field in mapping:
            continue
        for idx, header in enumerate(normalized):
            if not header:
                continue
            if any(kw in header for kw in keywords):
                mapping[field] = idx
                break

    if "room_name" not in mapping and header_row:
        mapping["room_name"] = 0

    return mapping


def is_footer_row(room_name: str) -> bool:
    lower = room_name.lower()
    return any(kw in lower for kw in FOOTER_KEYWORDS)


def is_instruction_row(room_name: str) -> bool:
    lower = room_name.strip().lower()
    return lower.startswith(INSTRUCTION_PREFIXES)


def to_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    num = to_number(value)
    if num is None:
        return None
    return int(round(num))
