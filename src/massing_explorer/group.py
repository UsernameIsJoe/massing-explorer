"""
Deterministic department grouping.

Which departments share a mass is no longer an LLM invention. This module
clusters by operational family, then honours hard "stay together" / "keep
apart" constraints from the user brief. Every department is assigned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ProgramStudy
from .study_state import MassGrouping

# Keyword families. A department lands in the first family whose keyword
# appears in its name; leftovers become their own "other" mass.
DEFAULT_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("academic", ("academic", "classroom", "special education")),
    ("arts", ("art", "music")),
    ("athletics", ("health", "physical", "gym")),
    ("dining", ("dining", "food", "kitchen")),
    ("support", (
        "administration",
        "guidance",
        "medical",
        "media",
        "custodial",
        "maintenance",
    )),
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def match_department(query: str, names: list[str]) -> str | None:
    """
    Map a loose user phrase onto one department name.

    Exact / substring first, then common aliases (gym → PE), then token
    overlap, then a cheap edit-distance fallback so typos like "custodiala"
    still resolve.
    """
    q = _norm(query)
    if not q or not names:
        return None

    # Dimension / grammar words are never department names — "min" must not
    # substring-hit "administration".
    if q in {
        "min",
        "max",
        "minimum",
        "maximum",
        "length",
        "lengths",
        "width",
        "widths",
        "height",
        "heights",
        "depth",
        "depths",
        "meter",
        "meters",
        "metre",
        "metres",
        "foot",
        "feet",
        "ft",
        "story",
        "stories",
        "floor",
        "floors",
        "level",
        "levels",
        "mass",
        "masses",
        "wing",
        "wings",
        "ratio",
        "prefer",
        "preferred",
        "double",
        "single",
        "edge",
        "edges",
        "long",
        "wide",
        "tall",
        "deep",
    }:
        return None

    by_norm = {_norm(n): n for n in names}
    if q in by_norm:
        return by_norm[q]

    # Short architect words that never appear in the schedule name itself.
    aliases: dict[str, tuple[str, ...]] = {
        "gym": ("health", "physical", "gym", "athletics", "pe"),
        "gymnasium": ("health", "physical", "gym", "athletics"),
        "recreation": ("health", "physical", "gym", "athletics"),
        "cafeteria": ("dining", "food", "kitchen"),
        "kitchen": ("dining", "food", "kitchen"),
        "dining hall": ("dining", "food"),
        "dining": ("dining", "food"),
        "food": ("dining", "food"),
        "fitness": ("health", "physical", "gym", "athletics"),
        "library": ("media",),
        "classroom": ("academic",),
        "classrooms": ("academic",),
        "admin": ("administration", "guidance"),
        "administration": ("administration", "guidance"),
        "special ed": ("special",),
        "sped": ("special",),
        "special education": ("special",),
        "pe": ("health", "physical", "gym"),
        "athletics": ("health", "physical", "gym"),
        "art": ("art", "music"),
        "arts": ("art", "music"),
        "art spaces": ("art", "music"),
        "art studios": ("art", "music"),
        "gallery": ("art", "music"),
        "music": ("art", "music"),
        "student life": ("dining", "food", "administration"),
        "loading": ("custodial", "maintenance"),
        "service": ("custodial", "maintenance"),
        "service/loading": ("custodial", "maintenance"),
        "loading area": ("custodial", "maintenance"),
    }
    if q in aliases:
        keys = aliases[q]
        for name in names:
            n = _norm(name)
            if any(k in n for k in keys):
                return name

    # Typo against an alias key ("gyma" → gym, "arta" → art) before
    # comparing the whole schedule name (which is usually much longer).
    if len(q) >= 3:
        best_alias = None
        best_d = 99
        for key in aliases:
            if " " in key:
                continue
            d = _edits(q, key)
            limit = max(1, min(2, len(q) // 4))
            if d < best_d and d <= limit:
                best_d = d
                best_alias = key
        if best_alias is not None:
            keys = aliases[best_alias]
            for name in names:
                n = _norm(name)
                if any(k in n for k in keys):
                    return name

    # Single letters / glue words ("i", "and", "the") must not substring-hit
    # every schedule name that happens to contain that letter.
    if len(q) < 3:
        return None

    # Whole-token hit first. Short stems ("min" in "administration") only
    # count when the query is itself a schedule token, not a substring.
    token_hits = [n for n in names if q in _norm(n).split()]
    if len(token_hits) == 1:
        return token_hits[0]
    if len(token_hits) > 1:
        token_hits.sort(key=lambda n: abs(len(_norm(n)) - len(q)))
        return token_hits[0]

    contained = []
    if len(q) >= 4:
        contained = [n for n in names if q in _norm(n) or _norm(n) in q]
    if len(contained) == 1:
        return contained[0]
    if len(contained) > 1:
        contained.sort(key=lambda n: abs(len(_norm(n)) - len(q)))
        return contained[0]

    q_tokens = {t for t in q.split() if len(t) >= 3}
    if q_tokens:
        scored: list[tuple[float, str]] = []
        for name in names:
            tokens = set(_norm(name).split())
            if not tokens:
                continue
            overlap = len(q_tokens & tokens) / len(q_tokens | tokens)
            if overlap:
                scored.append((overlap, name))
        if scored:
            scored.sort(key=lambda t: -t[0])
            if scored[0][0] >= 0.4:
                return scored[0][1]

    # Typos need a real stem; "and"/"want" must not fuzzy-match "art".
    if len(q) < 4:
        return None

    best_name = None
    best_d = 99
    for name in names:
        n = _norm(name)
        # Compare against the distinctive token (usually the first word)
        candidates = [n, n.split()[0] if n.split() else n]
        for c in candidates:
            d = _edits(q, c)
            limit = max(1, min(2, len(q) // 4))
            if d < best_d and d <= limit:
                best_d = d
                best_name = name
    return best_name


def _edits(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                prev[j - 1] if ca == cb else 1 + min(prev[j], cur[j - 1], prev[j - 1])
            )
        prev = cur
    return prev[-1]

class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _family_of(name: str) -> str:
    n = name.lower()
    for family, keywords in DEFAULT_FAMILIES:
        if any(k in n for k in keywords):
            return family
    return "other"


def _config_pairs(names: list[str], config: dict) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for entry in (config or {}).get("adjacencies") or []:
        keys = [str(k) for k in entry.get("departments") or []]
        strength = str(entry.get("strength") or "preferred").lower()
        resolved = [match_department(k, names) for k in keys]
        resolved = [r for r in resolved if r]
        for i in range(len(resolved)):
            for j in range(i + 1, len(resolved)):
                pairs.append((resolved[i], resolved[j], strength))
    return pairs


def _title(mass_id: str) -> str:
    return mass_id.replace("_", " ").title()


@dataclass
class GroupingResult:
    masses: list[MassGrouping]
    keep_together: list[tuple[str, str]]
    unmatched: list[str]


def group_departments(
    program: ProgramStudy,
    keep_together: list[tuple[str, str]] | None = None,
    keep_apart: list[tuple[str, str]] | None = None,
    config: dict | None = None,
    default_stories: int = 2,
) -> GroupingResult:
    """
    Cluster every department into a mass.

    Order of operations:
      1. operational families (academic, dining, …)
      2. config adjacency hints
      3. user keep-together (hard merge)
      4. user keep-apart (split the smaller side out)
    """
    names = [d.name for d in program.departments]
    if not names:
        return GroupingResult([], [], [])

    uf = _UnionFind(names)
    gsf = {d.name: d.target_gsf for d in program.departments}

    by_family: dict[str, list[str]] = {}
    for name in names:
        by_family.setdefault(_family_of(name), []).append(name)
    for members in by_family.values():
        for other in members[1:]:
            uf.union(members[0], other)

    # Config adjacencies are preferred, not required. Only `required` strength
    # is a hard merge; user keep-together always is.
    for a, b, strength in _config_pairs(names, config or {}):
        if strength == "required":
            uf.union(a, b)

    applied: list[tuple[str, str]] = []
    for a, b in keep_together or []:
        if a in uf.parent and b in uf.parent:
            uf.union(a, b)
            applied.append((a, b))

    for a, b in keep_apart or []:
        if a not in uf.parent or b not in uf.parent:
            continue
        if uf.find(a) != uf.find(b):
            continue
        # Split the smaller department into its own component
        mover = a if gsf.get(a, 0) <= gsf.get(b, 0) else b
        uf.parent[mover] = mover

    clusters: dict[str, list[str]] = {}
    for name in names:
        clusters.setdefault(uf.find(name), []).append(name)

    masses: list[MassGrouping] = []
    used_ids: set[str] = set()
    for members in sorted(clusters.values(), key=lambda m: -sum(gsf.get(d, 0) for d in m)):
        families = sorted({_family_of(d) for d in members})
        if len(families) == 1 and families[0] != "other":
            mass_id = families[0]
        else:
            mass_id = "_".join(families) if families else "mass"
        base = mass_id
        n = 2
        while mass_id in used_ids:
            mass_id = f"{base}_{n}"
            n += 1
        used_ids.add(mass_id)
        note = ""
        hits = [f"{a} + {b}" for a, b in applied if a in members and b in members]
        if hits:
            note = "stay together: " + "; ".join(hits)
        masses.append(
            MassGrouping(
                id=mass_id,
                name=_title(mass_id),
                departments=sorted(members),
                story_count=max(1, default_stories),
                notes=note,
            )
        )

    return GroupingResult(masses=masses, keep_together=applied, unmatched=[])
