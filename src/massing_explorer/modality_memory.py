"""
Cross-project modality memory.

Learns which wording means requirement / limitation / preference so novel
phrases do not need to be hard-coded. Numbers are never part of the key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

KINDS = ("requirement", "limitation", "preference")

_SPELLED = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)"
    r"(?:-|\s+)?(?:one|two|three|four|five|six|seven|eight|nine)?\b",
    flags=re.I,
)
_STOP = {
    "a", "an", "the", "and", "or", "of", "to", "for", "on", "in", "at", "by",
    "with", "from", "as", "is", "are", "be", "been", "being", "it", "its",
    "that", "this", "these", "those", "than", "then", "them", "their", "they",
    "mass", "masses", "building", "buildings", "wing", "wings", "floor",
    "floors", "story", "stories", "storey", "storeys", "level", "levels",
    "ft", "feet", "meter", "meters", "metre", "metres", "sqft", "gsf", "gfa",
}


def default_memory_path() -> Path:
    return Path.home() / ".massing_explorer" / "modality_memory.json"


def normalize_phrase(text: str) -> str:
    """Strip values so memory keys are wording-only."""
    t = (text or "").replace("\u2019", "'").replace("\u2018", "'").lower()
    t = re.sub(r"\d[\d,]*(?:\.\d+)?", " ", t)
    t = _SPELLED.sub(" ", t)
    t = re.sub(r"[^\w\s'/:-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _empty() -> dict[str, Any]:
    return {
        "version": 1,
        "cues": {k: [] for k in KINDS},
        "phrases": [],  # [{normalized, kind, count, cue, examples}]
    }


def load_memory(path: Path | None = None) -> dict[str, Any]:
    path = path or default_memory_path()
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    out = _empty()
    cues = data.get("cues") or {}
    for kind in KINDS:
        seen: list[str] = []
        for cue in cues.get(kind) or []:
            c = str(cue).strip().lower()
            if c and c not in seen:
                seen.append(c)
        out["cues"][kind] = seen
    phrases: list[dict[str, Any]] = []
    for entry in data.get("phrases") or []:
        if not isinstance(entry, dict):
            continue
        norm = normalize_phrase(str(entry.get("normalized") or entry.get("text") or ""))
        kind = str(entry.get("kind") or "")
        if not norm or kind not in KINDS:
            continue
        phrases.append(
            {
                "normalized": norm,
                "kind": kind,
                "count": int(entry.get("count") or 1),
                "cue": str(entry.get("cue") or ""),
                "examples": list(entry.get("examples") or [])[:6],
            }
        )
    out["phrases"] = phrases
    return out


def save_memory(memory: dict[str, Any], path: Path | None = None) -> Path:
    path = path or default_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "cues": {k: list((memory.get("cues") or {}).get(k) or []) for k in KINDS},
        "phrases": list(memory.get("phrases") or []),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def lookup_phrase(text: str, memory: dict[str, Any] | None = None) -> str | None:
    """Return kind if a remembered phrase matches (exact or contained)."""
    memory = memory or load_memory()
    norm = normalize_phrase(text)
    if not norm:
        return None
    # Prefer longest matching remembered phrase.
    best: tuple[int, str] | None = None
    for entry in memory.get("phrases") or []:
        key = str(entry.get("normalized") or "")
        kind = str(entry.get("kind") or "")
        if not key or kind not in KINDS:
            continue
        if norm == key or key in norm or norm in key:
            score = len(key)
            if best is None or score > best[0]:
                best = (score, kind)
    return best[1] if best else None


def lookup_cue(text: str, memory: dict[str, Any] | None = None) -> str | None:
    """Match learned cue strings (multi-word allowed) inside the clause."""
    memory = memory or load_memory()
    t = normalize_phrase(text)
    if not t:
        return None
    hits: list[tuple[int, str]] = []
    for kind in KINDS:
        for cue in (memory.get("cues") or {}).get(kind) or []:
            c = str(cue).strip().lower()
            if not c:
                continue
            if re.search(rf"\b{re.escape(c)}\b", t):
                hits.append((len(c), kind))
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def remember(
    text: str,
    kind: str,
    *,
    cue: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Record a user/LLM classification into cross-project memory."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    memory = load_memory(path)
    norm = normalize_phrase(text)
    if not norm:
        return memory

    # Upsert phrase
    found = None
    for entry in memory["phrases"]:
        if entry["normalized"] == norm:
            found = entry
            break
    if found is None:
        found = {
            "normalized": norm,
            "kind": kind,
            "count": 0,
            "cue": "",
            "examples": [],
        }
        memory["phrases"].append(found)
    found["kind"] = kind
    found["count"] = int(found.get("count") or 0) + 1
    if cue:
        found["cue"] = str(cue).strip().lower()
    examples = list(found.get("examples") or [])
    raw = (text or "").strip()
    if raw and raw not in examples:
        examples = ([raw] + examples)[:6]
    found["examples"] = examples

    # Learn cue token(s)
    cue_text = (cue or "").strip().lower()
    if not cue_text:
        # Pull a short non-stopword span as a soft cue (wording only).
        words = [w for w in norm.split() if w not in _STOP and len(w) > 2]
        if words:
            cue_text = " ".join(words[:3])
    if cue_text and cue_text not in memory["cues"][kind]:
        memory["cues"][kind].append(cue_text)
        # Keep cues unique across kinds — latest kind wins.
        for other in KINDS:
            if other == kind:
                continue
            memory["cues"][other] = [c for c in memory["cues"][other] if c != cue_text]

    save_memory(memory, path)
    return memory


def role_from_memory(text: str, memory: dict[str, Any] | None = None) -> str | None:
    """Phrase match first, then learned cues."""
    memory = memory or load_memory()
    return lookup_phrase(text, memory) or lookup_cue(text, memory)
