"""
Modality classification beyond hard-coded cues.

Order:
  1. Built-in cue words (brief._role_from_words)
  2. Cross-project memory (phrases + learned cues)
  3. LLM guess with confidence
  4. If still unsure → ask the user (UI)
"""

from __future__ import annotations

import json
import re
from typing import Any

from .modality_memory import KINDS, normalize_phrase, remember, role_from_memory

_CONFIDENCE_ASK = 0.72

# Shared instruction: every LLM path must treat digit + spelled as values.
_NUMBER_RULE = (
    "NUMBERS (critical): Digits (40, 85.5) and spelled forms (four, forty, "
    "twenty-five, eighty-five) are VALUES only — never the modality role. "
    "You MUST account for EVERY number in the text — none may be ignored. "
    "Always put Arabic numerals in JSON numeric fields. "
    "Example: '3 stories and no longer than 210 ft' → both 3 and 210. "
    "Example: 'under forty meters' → value 40, unit m. Do not invent sizes. "
    "If you cannot place a number on a lever, list it in unmapped and lower confidence."
)

_CONTENT = re.compile(
    r"\b(?:mass|masses|building|wing|volume|block|site|length|width|depth|"
    r"height|stor(?:y|ies|eys)|floor|level|gsf|gfa|sq\.?\s*ft|open\s+space|"
    r"together|apart|double[\s-]?height|ground|plaza|ratio|proportion|"
    r"gym|dining|art|music|academic|admin|media|custodial|"
    r"under|below|over|exceed|max|minimum|at\s+most|no\s+more|prefer)\b",
    flags=re.I,
)

# Mass/wing labels like "mass 1" / "wing two" are not size values.
_LABEL_NUMBER = re.compile(
    r"\b(?:mass(?:es|ing)?|wing|building|volume|block|option|scheme|scheme\s*#?)\s+"
    r"(?:#\s*)?(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b",
    flags=re.I,
)

def split_brief_clauses(text: str) -> list[str]:
    """Split a brief into clause-sized chunks for modality review."""
    raw = (text or "").replace("\u2019", "'").replace("\u2018", "'")
    parts = re.split(r"[.;!?\n]+|\s+and\s+(?=[A-Z]|[a-z]+\s+(?:should|must|needs|has|prefer|would|could|may))", raw)
    out: list[str] = []
    for part in parts:
        clause = re.sub(r"\s+", " ", part).strip(" ,")
        if len(clause) < 8:
            continue
        out.append(clause)
    return out


def classify_local(text: str) -> dict[str, Any]:
    """Built-in cues + memory. Never invent a role without evidence."""
    from .brief import _role_from_words

    builtin = _role_from_words(
        text, default="unknown", allow_unknown=True, use_memory=False
    )
    if builtin in KINDS:
        return {
            "text": text,
            "kind": builtin,
            "source": "builtin",
            "confidence": 1.0,
            "cue": "",
            "needs_ask": False,
        }
    memorized = role_from_memory(text)
    if memorized in KINDS:
        return {
            "text": text,
            "kind": memorized,
            "source": "memory",
            "confidence": 1.0,
            "cue": "",
            "needs_ask": False,
        }
    return {
        "text": text,
        "kind": "unknown",
        "source": "none",
        "confidence": 0.0,
        "cue": "",
        "needs_ask": True,
    }


def _extract_json(content: str) -> dict[str, Any] | None:
    raw = (content or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def classify_with_llm(text: str, client: Any | None = None) -> dict[str, Any]:
    """Ask the model for a modality guess; low confidence → needs_ask."""
    local = classify_local(text)
    if not local["needs_ask"]:
        return local
    if client is None:
        try:
            from .ollama_client import OllamaClient

            client = OllamaClient()
            if not client.ping():
                return {**local, "source": "llm_unavailable"}
        except Exception:
            return {**local, "source": "llm_unavailable"}

    prompt = (
        "Classify ONE design-brief clause as requirement, limitation, or preference.\n"
        f"{_NUMBER_RULE}\n"
        "Use ONLY modality wording for the role.\n"
        "Canonical cues:\n"
        "- requirement: must, needs to, has to, requires, exactly\n"
        "- limitation: should be, cannot exceed, no more than, at least, should stay under\n"
        "- preference: prefer, ideally, would rather, better if, if possible, around, closer to\n"
        "If the clause uses novel wording, still guess from how hard/soft the ask is, "
        "and set confidence low when unsure.\n"
        "Reply JSON only: "
        '{"kind":"requirement|limitation|preference","confidence":0.0,'
        '"cue":"modality words you used","rationale":"short"}\n'
        f"Clause: {text}"
    )
    try:
        response = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Reply with JSON only. Role from modality wording, never from numbers. "
                        + _NUMBER_RULE
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )
        content = (response.get("message") or {}).get("content") or ""
        data = _extract_json(content) or {}
    except Exception:
        return {**local, "source": "llm_error"}

    kind = str(data.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return {**local, "source": "llm_bad", "rationale": data.get("rationale") or ""}
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    cue = str(data.get("cue") or "").strip()
    needs_ask = confidence < _CONFIDENCE_ASK
    return {
        "text": text,
        "kind": kind,
        "source": "llm",
        "confidence": confidence,
        "cue": cue,
        "rationale": str(data.get("rationale") or ""),
        "needs_ask": needs_ask,
    }


def find_modality_questions(
    text: str,
    *,
    client: Any | None = None,
    answers: list[dict[str, Any]] | None = None,
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    """
    Return clauses that still need a human modality pick.

    `answers` from a prior UI turn are applied into memory and skipped.
    """
    for ans in answers or []:
        clause = str(ans.get("text") or "")
        kind = str(ans.get("kind") or "")
        if clause and kind in KINDS:
            remember(clause, kind, cue=str(ans.get("cue") or "") or None)

    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for clause in split_brief_clauses(text):
        if not _CONTENT.search(clause):
            continue
        key = normalize_phrase(clause)
        if not key or key in seen:
            continue
        seen.add(key)
        result = classify_local(clause)
        if result["needs_ask"] and use_llm:
            result = classify_with_llm(clause, client=client)
            # High-confidence LLM guesses are auto-remembered as soft memory.
            if (
                not result.get("needs_ask")
                and result.get("kind") in KINDS
                and result.get("source") == "llm"
            ):
                remember(clause, str(result["kind"]), cue=str(result.get("cue") or "") or None)
        if result.get("needs_ask"):
            questions.append(
                {
                    "id": f"m{len(questions)}",
                    "text": clause,
                    "suggested": result.get("kind") if result.get("kind") in KINDS else None,
                    "confidence": result.get("confidence") or 0.0,
                    "source": result.get("source") or "none",
                    "cue": result.get("cue") or "",
                    "rationale": result.get("rationale") or "",
                    "reason": "modality",
                }
            )
    return questions


_VALUE_TOKEN = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?|"
    r"fourteen|fifteen|sixteen|eighteen|twenty(?:-five|-two)?|"
    r"thirty(?:-five)?|forty(?:-five)?|fifty(?:-five)?|sixty(?:-five)?|"
    r"seventy|eighty|ninety|hundred|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|twelve)"
    r"(?:\s*(?P<unit>ft|feet|foot|'|′|m|meters?|metres?|"
    r"stor(?:y|ies|eys)|floors?|levels?|"
    r"sq\.?\s*ft|sqft|sf|sq\.?\s*m|sqm))?",
    flags=re.I,
)

_LEVERS = {
    "max_length",
    "preferred_length",
    "exact_length",
    "max_width",
    "site_length",
    "max_height",
    "max_stories",
    "preferred_stories",
    "mass_count",
    "max_gfa",
    "ratio",
}


def _label_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _LABEL_NUMBER.finditer(text or "")]


def _inside_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def stated_numbers(text: str) -> list[dict[str, Any]]:
    """Every digit/spelled size in the text (skip mass/wing labels)."""
    from .brief import _parse_num, _to_feet

    raw = text or ""
    labels = _label_spans(raw)
    out: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for m in _VALUE_TOKEN.finditer(raw):
        if _inside_spans(m.start(), labels):
            continue
        # Percentages are not massing sizes here.
        after = raw[m.end() : m.end() + 8].lstrip().lower()
        if after.startswith("%") or after.startswith("percent"):
            continue
        try:
            num = _parse_num(m.group("num"))
        except (TypeError, ValueError):
            continue
        unit = (m.group("unit") or "").strip().lower() or None
        if unit and unit.startswith("stor"):
            kind = "stories"
            as_ft = float(num)
        elif unit and (unit.startswith("floor") or unit.startswith("level")):
            kind = "stories"
            as_ft = float(num)
        elif unit and ("sq" in unit or unit in {"sf", "sqft", "sqm"}):
            kind = "area"
            as_ft = float(num)  # compared loosely later
        else:
            kind = "length"
            as_ft = _to_feet(float(num), unit)
        key = (round(as_ft, 3), kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "raw": m.group(0),
                "value": float(num),
                "unit": unit,
                "kind": kind,
                "as_ft": as_ft,
                "start": m.start(),
                "end": m.end(),
            }
        )
    return out


_SIZE_CONSTRAINT_KEYS = {
    "max_building_length_ft",
    "preferred_length_ft",
    "exact_building_length_ft",
    "max_total_length_ft",
    "max_building_width_ft",
    "max_height_ft",
    "max_gfa_sf",
    "preferred_stories",
    "site_width_ft",
    "site_depth_ft",
    "academic_width_ft",
    "corridor_ft",
    "classroom_depth_ft",
}


def accounted_magnitudes(parsed: Any) -> list[float]:
    """All numeric magnitudes already stored on the parsed brief."""
    known: list[float] = []
    constraints = getattr(parsed, "constraints", {}) or {}
    for key, v in constraints.items():
        if key not in _SIZE_CONSTRAINT_KEYS and key not in {
            "department_widths",
        }:
            # Skip flags like length_limit_is_cap=1 — they are not sizes.
            if key != "department_widths":
                continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            known.append(float(v))
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, (int, float)) and not isinstance(vv, bool):
                    known.append(float(vv))
    for attr in (
        "max_stories",
        "mass_count",
        "mass_count_min",
        "mass_count_max",
        "pair_length_ft",
        "length_over_width",
    ):
        val = getattr(parsed, attr, None)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            known.append(float(val))
            if attr == "length_over_width" and val:
                for a in range(1, 13):
                    for b in range(1, 13):
                        if b and abs(a / b - float(val)) < 0.02:
                            known.extend([float(a), float(b)])
    for d in getattr(parsed, "dimensions", None) or []:
        if isinstance(d.get("value"), (int, float)):
            known.append(float(d["value"]))
    return known


def _near_any(
    value: float,
    known: list[float],
    *,
    tol: float = 0.75,
    allow_m_ft: bool = True,
) -> bool:
    for k in known:
        if abs(value - k) < tol:
            return True
        if allow_m_ft and (
            abs(value * 3.280839895 - k) < tol or abs(value - k * 3.280839895) < tol
        ):
            return True
    return False


def unaccounted_numbers(text: str, parsed: Any) -> list[dict[str, Any]]:
    """Stated numbers (digit/spelled) that did not land in any lever."""
    known = accounted_magnitudes(parsed)
    missed: list[dict[str, Any]] = []
    for item in stated_numbers(text):
        if item["kind"] == "stories":
            # Counts only — never treat story 3 as ~1 m in feet.
            ok = _near_any(item["value"], known, tol=0.51, allow_m_ft=False)
        elif item["kind"] == "area":
            ok = _near_any(item["value"], known, tol=1.0, allow_m_ft=False)
        else:
            ok = _near_any(item["as_ft"], known, tol=0.75, allow_m_ft=True) or _near_any(
                item["value"], known, tol=0.75, allow_m_ft=True
            )
        if not ok:
            missed.append(item)
    return missed


def _clause_values_ft(clause: str) -> list[float]:
    return [n["as_ft"] if n["kind"] == "length" else n["value"] for n in stated_numbers(clause)]


def clause_was_captured(clause: str, parsed: Any) -> bool:
    """True only when EVERY stated number in the clause is accounted for."""
    cl = (clause or "").lower()
    nums = stated_numbers(clause)
    if not nums:
        # No sizes → captured unless it looks like a keep-together we missed.
        if re.search(r"\b(?:attached|paired|together|joined|linked|connected|grouped)\b", cl):
            return bool(getattr(parsed, "keep_together", None))
        return True
    return not unaccounted_numbers(clause, parsed)


def interpret_clause_content(
    clause: str,
    *,
    client: Any | None = None,
    forced_kind: str | None = None,
) -> dict[str, Any]:
    """LLM: map every number in an uncaptured clause onto levers."""
    local_kind = forced_kind if forced_kind in KINDS else None
    if local_kind is None:
        role = classify_local(clause)
        local_kind = role["kind"] if role["kind"] in KINDS else None

    expected = stated_numbers(clause)
    if client is None:
        try:
            from .ollama_client import OllamaClient

            client = OllamaClient()
            if not client.ping():
                return {
                    "text": clause,
                    "needs_ask": True,
                    "source": "llm_unavailable",
                    "kind": local_kind,
                    "confidence": 0.0,
                    "items": [],
                }
        except Exception:
            return {
                "text": clause,
                "needs_ask": True,
                "source": "llm_unavailable",
                "kind": local_kind,
                "confidence": 0.0,
                "items": [],
            }

    expected_list = ", ".join(
        f"{e['value']:g}" + (f" {e['unit']}" if e["unit"] else "") for e in expected
    ) or "(none)"
    prompt = (
        "Extract ALL massing constraints from this design-brief clause.\n"
        f"{_NUMBER_RULE}\n"
        f"Numbers that MUST be placed (digit or spelled): {expected_list}\n"
        "Return one item per number. value must be an Arabic numeral; unit as written "
        "(ft, m, stories, count, sf). Leave unmapped ONLY if truly not a massing size.\n"
        "Levers: max_length, preferred_length, exact_length, max_width, site_length,\n"
        "max_height, max_stories, preferred_stories, mass_count, max_gfa, ratio.\n"
        "Kind: requirement | limitation | preference "
        "(from modality words: must/needs=requirement; should/under/cannot=limitation; "
        "prefer/around/ideally=preference).\n"
        "Reply JSON only: "
        '{"kind":"...","confidence":0.0,"items":[{"lever":"...","value":40,"unit":"m"}],'
        '"unmapped":[],"rationale":"short"}\n'
        f"Clause: {clause}"
    )
    try:
        response = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Reply with JSON only. Place EVERY stated number on a lever. "
                        + _NUMBER_RULE
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )
        content = (response.get("message") or {}).get("content") or ""
        data = _extract_json(content) or {}
    except Exception:
        return {
            "text": clause,
            "needs_ask": True,
            "source": "llm_error",
            "kind": local_kind,
            "confidence": 0.0,
            "items": [],
        }

    kind = str(data.get("kind") or local_kind or "").strip().lower()
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    items_raw = data.get("items")
    if not isinstance(items_raw, list):
        if data.get("lever"):
            items_raw = [
                {
                    "lever": data.get("lever"),
                    "value": data.get("value"),
                    "unit": data.get("unit"),
                }
            ]
        else:
            items_raw = []

    items: list[dict[str, Any]] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        lever = str(it.get("lever") or "").strip().lower()
        value = _coerce_llm_number(it.get("value"))
        unit = str(it.get("unit") or "").strip().lower() or None
        if lever in _LEVERS and value is not None:
            items.append({"lever": lever, "value": value, "unit": unit, "kind": kind})

    unmapped = data.get("unmapped") if isinstance(data.get("unmapped"), list) else []
    placed_vals = [float(i["value"]) for i in items]
    missing_stated = []
    for e in expected:
        if not any(
            abs(e["value"] - p) < 0.51
            or abs(e["as_ft"] - p) < 0.75
            or abs(e["value"] * 3.280839895 - p) < 0.75
            for p in placed_vals
        ):
            missing_stated.append(e["raw"])

    needs_ask = (
        kind not in KINDS
        or not items
        or confidence < _CONFIDENCE_ASK
        or bool(unmapped)
        or bool(missing_stated)
    )
    rationale = str(data.get("rationale") or "")
    if missing_stated:
        rationale = (rationale + " " if rationale else "") + (
            "Unplaced numbers: " + ", ".join(missing_stated)
        )
    return {
        "text": clause,
        "kind": kind if kind in KINDS else local_kind,
        "items": items,
        "lever": items[0]["lever"] if items else "",
        "value": items[0]["value"] if items else None,
        "unit": items[0]["unit"] if items else None,
        "confidence": confidence,
        "source": "llm",
        "rationale": rationale,
        "needs_ask": needs_ask,
        "unmapped": unmapped,
        "missing_stated": missing_stated,
    }


def _coerce_llm_number(value: Any) -> float | None:
    """Accept digit or spelled LLM output as a float."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    from .brief import _parse_num

    token = str(value).strip().lower().replace(",", "")
    token = re.split(r"\s+", token, maxsplit=1)[0]
    try:
        return _parse_num(token)
    except (TypeError, ValueError):
        return None


def apply_content_interpretation(parsed: Any, data: dict[str, Any]) -> bool:
    """Write LLM/content interpretation(s) onto ParsedBrief. True if any applied."""
    from .brief import _to_feet, _to_sf

    items = list(data.get("items") or [])
    if not items and data.get("lever"):
        items = [
            {
                "lever": data.get("lever"),
                "value": data.get("value"),
                "unit": data.get("unit"),
                "kind": data.get("kind"),
            }
        ]
    applied = False
    constraints = parsed.constraints
    for it in items:
        lever = str(it.get("lever") or "").strip().lower()
        if lever not in _LEVERS:
            continue
        value = _coerce_llm_number(it.get("value"))
        if value is None:
            continue
        unit = it.get("unit")
        kind = str(it.get("kind") or data.get("kind") or "limitation").lower()

        if lever == "max_length":
            constraints["max_building_length_ft"] = round(_to_feet(value, unit), 4)
            constraints["length_limit_is_cap"] = 1
            parsed.notes.append(
                f"max building length {constraints['max_building_length_ft']:g} ft (from clause)"
            )
            applied = True
        elif lever == "preferred_length":
            constraints["preferred_length_ft"] = round(_to_feet(value, unit), 4)
            applied = True
        elif lever == "exact_length":
            constraints["exact_building_length_ft"] = round(_to_feet(value, unit), 4)
            applied = True
        elif lever == "max_width":
            constraints["max_building_width_ft"] = round(_to_feet(value, unit), 4)
            applied = True
        elif lever == "site_length":
            constraints["max_total_length_ft"] = round(_to_feet(value, unit), 4)
            applied = True
        elif lever == "max_height":
            constraints["max_height_ft"] = round(_to_feet(value, unit), 4)
            applied = True
        elif lever == "max_stories":
            parsed.max_stories = max(1, int(round(value)))
            constraints["story_role"] = kind if kind in KINDS else "limitation"
            applied = True
        elif lever == "preferred_stories":
            constraints["preferred_stories"] = max(1, int(round(value)))
            applied = True
        elif lever == "mass_count":
            parsed.mass_count = max(1, int(round(value)))
            applied = True
        elif lever == "max_gfa":
            constraints["max_gfa_sf"] = round(_to_sf(value, unit), 4)
            applied = True
        elif lever == "ratio":
            parsed.length_over_width = float(value)
            applied = True
    return applied


def resolve_unresolved_clauses(
    text: str,
    parsed: Any,
    *,
    client: Any | None = None,
    answers: list[dict[str, Any]] | None = None,
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    """
    Every stated number must land on a lever: robust parse -> LLM -> ask.

    Answers may include a modality kind; we re-try interpretation with that kind.
    """
    answer_kinds = {
        normalize_phrase(str(a.get("text") or "")): str(a.get("kind") or "")
        for a in (answers or [])
        if str(a.get("text") or "") and str(a.get("kind") or "") in KINDS
    }

    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for clause in split_brief_clauses(text):
        has_nums = bool(stated_numbers(clause))
        if not has_nums and not _CONTENT.search(clause):
            continue
        key = normalize_phrase(clause)
        if not key or key in seen:
            continue
        seen.add(key)
        if clause_was_captured(clause, parsed):
            continue
        missed = unaccounted_numbers(clause, parsed)
        forced = answer_kinds.get(key)
        if use_llm:
            interp = interpret_clause_content(clause, client=client, forced_kind=forced)
            if not interp.get("needs_ask") and apply_content_interpretation(parsed, interp):
                if clause_was_captured(clause, parsed):
                    if interp.get("kind") in KINDS:
                        remember(
                            clause,
                            str(interp["kind"]),
                            cue=str(interp.get("lever") or "") or None,
                        )
                    continue
            suggested = interp.get("kind") if interp.get("kind") in KINDS else forced
            miss_txt = ", ".join(m["raw"] for m in missed) or ", ".join(
                interp.get("missing_stated") or []
            )
            questions.append(
                {
                    "id": f"u{len(questions)}",
                    "text": clause,
                    "suggested": suggested,
                    "confidence": interp.get("confidence") or 0.0,
                    "source": interp.get("source") or "none",
                    "cue": "",
                    "rationale": interp.get("rationale")
                    or (
                        f"Could not place number(s): {miss_txt}"
                        if miss_txt
                        else "Could not extract a size/lever from this clause."
                    ),
                    "reason": "unresolved",
                    "unplaced_numbers": miss_txt,
                }
            )
        else:
            miss_txt = ", ".join(m["raw"] for m in missed)
            questions.append(
                {
                    "id": f"u{len(questions)}",
                    "text": clause,
                    "suggested": forced if forced in KINDS else None,
                    "confidence": 0.0,
                    "source": "none",
                    "cue": "",
                    "rationale": (
                        f"Could not place number(s): {miss_txt}"
                        if miss_txt
                        else "Could not extract a size/lever from this clause."
                    ),
                    "reason": "unresolved",
                    "unplaced_numbers": miss_txt,
                }
            )
    return questions


def find_brief_questions(
    text: str,
    *,
    client: Any | None = None,
    answers: list[dict[str, Any]] | None = None,
    use_llm: bool = True,
    department_names: list[str] | None = None,
    parsed: Any | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """
    Guarantee every stated number gets an answer path:
      robust parse -> LLM interpretation (all numbers) -> ask user.

    Returns (questions, parsed). If questions is non-empty, pause for the UI.
    """
    from .brief import parse_brief

    modality_qs = find_modality_questions(
        text, client=client, answers=answers, use_llm=use_llm
    )
    if parsed is None:
        parsed = parse_brief(text, list(department_names or []))
    if modality_qs:
        # Still surface unplaced numbers on modality asks so nothing is silent.
        for q in modality_qs:
            missed = unaccounted_numbers(q.get("text") or "", parsed)
            if missed:
                q["unplaced_numbers"] = ", ".join(m["raw"] for m in missed)
                if not q.get("rationale"):
                    q["rationale"] = (
                        "Also need to place number(s): " + q["unplaced_numbers"]
                    )
        return modality_qs, parsed

    unresolved = resolve_unresolved_clauses(
        text,
        parsed,
        client=client,
        answers=answers,
        use_llm=use_llm,
    )
    return unresolved, parsed
