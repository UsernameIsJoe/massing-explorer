"""
Constraint satisfaction over program partitions.

Variables are family atoms (the masses already on the study). An assignment
is a restricted-growth string: atom i joins an existing block or opens a
new one. That enumerates distinct set partitions, not labeled permutations.

Hard constraints: keep-together (pre-glued), keep-apart (blocks cannot
share), coverage (every atom assigned). Site fit is not a CSP constraint;
the engine still filters.

The LLM does not invent P. This module is the generator when grouping was
not required.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..group import _family_of, _title
from .strategy import grouping_is_required, required_apart, required_together

PARTITION_CAP = 5
MAX_ENUM = 8000


def describe_csp(session: Any, cap: int = PARTITION_CAP) -> dict[str, Any]:
    """Report for the archive, planner, and the Phase 6 board."""
    atoms = atoms_from_session(session)
    apart = required_apart(session)
    together = required_together(session)
    if not atoms:
        return {
            "ran": True,
            "locked": grouping_is_required(session),
            "source": "csp",
            "atoms": [],
            "apart": [sorted(p) for p in apart],
            "together": [sorted(p) for p in together],
            "feasible_count": 0,
            "enumerated": 0,
            "truncated": False,
            "shown": 0,
            "chosen": [],
            "rejected": [],
            "note": "No program atoms, so the constraint solver has no partition.",
        }
    if grouping_is_required(session):
        chosen = [_from_atoms(atoms, list(range(len(atoms))), "stated grouping")]
        return {
            "ran": True,
            "locked": True,
            "source": "csp",
            "atoms": [_atom_view(a) for a in atoms],
            "apart": [sorted(p) for p in apart],
            "together": [sorted(p) for p in together],
            "feasible_count": 1,
            "enumerated": 1,
            "truncated": False,
            "shown": 1,
            "chosen": chosen,
            "rejected": [],
            "note": (
                "P is locked. The constraint solver kept the stated grouping "
                "and did not sample other partitions."
            ),
        }

    solved = solve_partitions(atoms, apart=apart, together=together, cap=cap)
    return {
        "ran": True,
        "locked": False,
        "source": "csp",
        "atoms": [_atom_view(a) for a in atoms],
        "apart": [sorted(p) for p in apart],
        "together": [sorted(p) for p in together],
        "feasible_count": solved["feasible_count"],
        "enumerated": solved["enumerated"],
        "truncated": solved["truncated"],
        "shown": len(solved["chosen"]),
        "chosen": solved["chosen"],
        "rejected": solved["rejected"],
        "note": solved["note"],
    }


def solve_partitions(
    atoms: list[dict[str, Any]],
    apart: list[frozenset[str]] | None = None,
    together: list[frozenset[str]] | None = None,
    cap: int = PARTITION_CAP,
) -> dict[str, Any]:
    """All distinct feasible partitions of atoms, then the cap to show."""
    glued = _glue(atoms, together or [])
    n = len(glued)
    apart_pairs = _atom_apart_pairs(glued, apart or [])
    feasible: list[list[int]] = []
    rejected: list[dict[str, Any]] = []
    enumerated = 0
    truncated = False

    for assign in _restricted_growth(n):
        enumerated += 1
        if enumerated > MAX_ENUM:
            truncated = True
            break
        if _respects_apart(assign, apart_pairs):
            feasible.append(assign)
        elif len(rejected) < 4:
            rejected.append(
                {
                    "label": _assignment_label(glued, assign),
                    "why": "broke keep-apart",
                }
            )

    ranked = sorted(feasible, key=lambda a: _score(glued, a), reverse=True)
    chosen = []
    seen: set[frozenset[frozenset[str]]] = set()
    for assign in ranked:
        item = _from_atoms(glued, assign, _reason(glued, assign))
        key = _signature(item["groups"])
        if key in seen:
            continue
        seen.add(key)
        chosen.append(item)
        if len(chosen) >= cap:
            break

    extra = " Enumeration stopped early." if truncated else ""
    note = (
        f"CSP: {len(feasible)} feasible partition(s) of {n} atom(s); "
        f"showing {len(chosen)}. The LLM does not invent P.{extra}"
    )
    return {
        "atoms": glued,
        "feasible_count": len(feasible),
        "enumerated": min(enumerated, MAX_ENUM),
        "truncated": truncated,
        "chosen": chosen,
        "rejected": rejected,
        "note": note,
    }


def atoms_from_session(session: Any) -> list[dict[str, Any]]:
    out = []
    for mass in session.masses or []:
        out.append(
            {
                "id": mass.id,
                "name": mass.name,
                "departments": list(mass.departments),
                "story_count": int(mass.story_count or 2),
            }
        )
    return out


def _glue(atoms: list[dict[str, Any]], together: list[frozenset[str]]) -> list[dict[str, Any]]:
    """Collapse atoms that a keep-together clause already bound."""
    n = len(atoms)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    home: dict[str, int] = {}
    for i, atom in enumerate(atoms):
        for dept in atom["departments"]:
            home[str(dept)] = i
    for pair in together:
        idxs = [home[d] for d in pair if d in home]
        for a, b in zip(idxs, idxs[1:]):
            union(a, b)

    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, atom in enumerate(atoms):
        buckets[find(i)].append(atom)
    glued = []
    for members in buckets.values():
        if len(members) == 1:
            glued.append(dict(members[0]))
            continue
        depts: list[str] = []
        stories = 2
        for atom in members:
            depts.extend(atom["departments"])
            stories = max(stories, int(atom.get("story_count") or 2))
        glued.append(_payload(depts, stories))
    return glued


def _atom_apart_pairs(atoms: list[dict[str, Any]], apart: list[frozenset[str]]) -> list[tuple[int, int]]:
    home: dict[str, int] = {}
    for i, atom in enumerate(atoms):
        for dept in atom["departments"]:
            home[str(dept)] = i
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pair in apart:
        idxs = [home.get(d) for d in pair]
        if None in idxs or len(idxs) < 2:
            continue
        a, b = int(idxs[0]), int(idxs[1])
        if a == b:
            continue
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def _restricted_growth(n: int):
    """Canonical assignments for the partitions of n labeled atoms."""
    if n <= 0:
        return
    assign = [0] * n

    def rec(i: int, blocks: int):
        if i == n:
            yield assign[:]
            return
        for b in range(blocks):
            assign[i] = b
            yield from rec(i + 1, blocks)
        assign[i] = blocks
        yield from rec(i + 1, blocks + 1)

    yield from rec(1, 1)


def _respects_apart(assign: list[int], pairs: list[tuple[int, int]]) -> bool:
    for i, j in pairs:
        if assign[i] == assign[j]:
            return False
    return True


def _from_atoms(atoms: list[dict[str, Any]], assign: list[int], reason: str) -> dict[str, Any]:
    blocks: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, atom in enumerate(atoms):
        blocks[assign[i]].append(atom)
    groups = []
    for members in blocks.values():
        if len(members) == 1:
            groups.append(
                {
                    "id": members[0]["id"],
                    "name": members[0]["name"],
                    "departments": list(members[0]["departments"]),
                    "story_count": int(members[0].get("story_count") or 2),
                }
            )
            continue
        depts: list[str] = []
        stories = 2
        for atom in members:
            depts.extend(atom["departments"])
            stories = max(stories, int(atom.get("story_count") or 2))
        groups.append(_payload(depts, stories))
    return {"groups": _uniquify(groups), "reason": reason, "source": "csp"}


def _payload(departments: list[str], stories: int) -> dict[str, Any]:
    families = sorted({_family_of(d) for d in departments})
    if len(families) == 1 and families[0] != "other":
        mass_id = families[0]
    else:
        mass_id = "_".join(families) if families else "mass"
    return {
        "id": mass_id,
        "name": _title(mass_id),
        "departments": list(departments),
        "story_count": max(1, int(stories or 2)),
    }


def _uniquify(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used: set[str] = set()
    out = []
    for group in groups:
        mass_id = str(group["id"])
        base = mass_id
        n = 2
        while mass_id in used:
            mass_id = f"{base}_{n}"
            n += 1
        used.add(mass_id)
        item = dict(group)
        item["id"] = mass_id
        out.append(item)
    return out


def _signature(groups: list[dict[str, Any]]) -> frozenset[frozenset[str]]:
    return frozenset(frozenset(str(d) for d in g["departments"]) for g in groups)


def _families(atom: dict[str, Any]) -> set[str]:
    return {_family_of(d) for d in atom["departments"]}


def _reason(atoms: list[dict[str, Any]], assign: list[int]) -> str:
    if assign == list(range(len(atoms))):
        return "stated grouping"
    blocks: dict[int, list[int]] = defaultdict(list)
    for i, b in enumerate(assign):
        blocks[b].append(i)
    merges = [idxs for idxs in blocks.values() if len(idxs) > 1]
    if len(merges) == 1:
        fams: set[str] = set()
        for i in merges[0]:
            fams |= _families(atoms[i])
        if fams == {"athletics", "dining"}:
            return "colocate gym with dining"
        if fams == {"academic", "arts"}:
            return "colocate academic with arts"
        if fams == {"athletics", "dining", "arts"}:
            return "arts with gym and dining"
        if len(merges[0]) == 2:
            a, b = atoms[merges[0][0]], atoms[merges[0][1]]
            return f"colocate {a['name']} with {b['name']}"
    if len(blocks) == 2:
        fam_sets = [_families(atoms[i]) for i in range(len(atoms))]
        academic_only = [
            i for i, fams in enumerate(fam_sets) if fams == {"academic"}
        ]
        if academic_only and len({assign[i] for i in academic_only}) == 1:
            rest = [i for i in range(len(atoms)) if i not in academic_only]
            if rest and len({assign[i] for i in rest}) == 1:
                return "academic apart from a combined public/support bar"
    return f"csp partition: {len(blocks)} masses"


def _score(atoms: list[dict[str, Any]], assign: list[int]) -> tuple:
    reason = _reason(atoms, assign)
    n_blocks = len(set(assign))
    rank = {
        "stated grouping": 1000,
        "colocate gym with dining": 900,
        "colocate academic with arts": 800,
        "arts with gym and dining": 700,
        "academic apart from a combined public/support bar": 600,
    }.get(reason, 200 + n_blocks)
    if reason.startswith("colocate ") and rank == 200 + n_blocks:
        rank = 500
    merges = n_blocks - len(atoms)  # more negative = more merging
    return (rank, merges, n_blocks)


def _assignment_label(atoms: list[dict[str, Any]], assign: list[int]) -> str:
    blocks: dict[int, list[str]] = defaultdict(list)
    for i, b in enumerate(assign):
        blocks[b].append(str(atoms[i]["id"]))
    return " / ".join("+".join(names) for names in blocks.values())


def _atom_view(atom: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": atom["id"],
        "name": atom["name"],
        "departments": list(atom["departments"]),
    }
