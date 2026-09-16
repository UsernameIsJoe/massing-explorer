"""
Constraint satisfaction over program partitions.

Variables are family atoms (the masses already on the study). An assignment
is a restricted-growth string: atom i joins an existing block or opens a
new one. That enumerates distinct set partitions, not labeled permutations.

Hard constraints: keep-together (pre-glued), keep-apart (blocks cannot
share), coverage (every atom assigned). Site fit is not a CSP constraint;
the engine still filters.

The LLM does not invent P. mass_count, keep-together, and alone constrain
the partitions this module returns. They do not freeze P unless the session
sets partition_locked.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..group import _family_of, _title
from .strategy import (
    grouping_is_required,
    preferred_mass_count,
    required_alone,
    required_apart,
    required_mass_bounds,
    required_together,
)

PARTITION_CAP = 5  # back-compat alias for UI presentation
UI_PARTITION_CAP = 5  # human-facing CSP board / report shortlist
MAX_ENUM = 25000  # practical ceiling; truncated=True when hit


def describe_csp(session: Any, cap: int = UI_PARTITION_CAP) -> dict[str, Any]:
    """Report for the archive, planner, and the Phase 6 board (UI shortlist)."""
    locked = grouping_is_required(session)
    atoms = [] if locked else department_atoms(session)
    if not atoms:
        atoms = atoms_from_session(session)
    apart = list(required_apart(session))
    together = required_together(session)
    alone = required_alone(session)
    bounds = required_mass_bounds(session)
    preferred = preferred_mass_count(session)
    apart.extend(_alone_as_apart(atoms, alone))
    if not atoms:
        return {
            "ran": True,
            "locked": locked,
            "source": "csp",
            "atoms": [],
            "apart": [sorted(p) for p in apart],
            "together": [sorted(p) for p in together],
            "alone": list(alone),
            "mass_bounds": list(bounds) if bounds else None,
            "preferred_mass_count": preferred,
            "feasible_count": 0,
            "enumerated": 0,
            "truncated": False,
            "shown": 0,
            "chosen": [],
            "rejected": [],
            "note": "No program atoms, so the constraint solver has no partition.",
        }
    if locked:
        chosen = [_from_atoms(atoms, list(range(len(atoms))), "stated grouping")]
        return {
            "ran": True,
            "locked": True,
            "source": "csp",
            "atoms": [_atom_view(a) for a in atoms],
            "apart": [sorted(p) for p in apart],
            "together": [sorted(p) for p in together],
            "alone": list(alone),
            "mass_bounds": list(bounds) if bounds else None,
            "preferred_mass_count": preferred,
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

    solved = solve_partitions(
        atoms,
        apart=apart,
        together=together,
        cap=cap,
        mass_bounds=bounds,
        preferred_mass_count=preferred,
    )
    return {
        "ran": True,
        "locked": False,
        "source": "csp",
        "atoms": [_atom_view(a) for a in atoms],
        "apart": [sorted(p) for p in apart],
        "together": [sorted(p) for p in together],
        "alone": list(alone),
        "mass_bounds": list(bounds) if bounds else None,
        "preferred_mass_count": preferred,
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
    cap: int = UI_PARTITION_CAP,
    mass_bounds: tuple[int, int] | None = None,
    preferred_mass_count: int | None = None,
) -> dict[str, Any]:
    """Enumerate feasible partitions, then diversity-select up to `cap`."""
    glued = _glue(atoms, together or [])
    n = len(glued)
    apart_pairs = _atom_apart_pairs(glued, apart or [])
    if mass_bounds:
        k_min = max(1, min(int(mass_bounds[0]), n or 1))
        k_max = max(k_min, min(int(mass_bounds[1]), n or 1))
    else:
        k_min, k_max = 1, max(n, 1)
    preferred = None
    if preferred_mass_count is not None:
        try:
            pref = int(preferred_mass_count)
        except (TypeError, ValueError):
            pref = 0
        if pref > 0:
            preferred = max(k_min, min(k_max, pref))
    feasible: list[list[int]] = []
    rejected: list[dict[str, Any]] = []
    enumerated = 0
    truncated = False

    for assign in _restricted_growth_bounded(n, k_min, k_max):
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

    ranked = sorted(
        feasible,
        key=lambda a: _score(glued, a, preferred_mass_count=preferred),
        reverse=True,
    )
    chosen = _pick_shortlist(
        glued,
        ranked,
        cap=cap,
        k_min=k_min,
        k_max=k_max,
        preferred_mass_count=preferred,
    )

    extra = " Enumeration stopped early." if truncated else ""
    bound = f" with |P| in {k_min}–{k_max}" if mass_bounds else ""
    pref_note = (
        f" Preferred |P|={preferred} from the brief."
        if preferred is not None
        else " No preferred mass count, so higher |P| is not ranked above lower |P|."
    )
    note = (
        f"CSP: {len(feasible)} feasible partition(s) of {n} atom(s){bound}; "
        f"shortlist {len(chosen)} with fair shares across allowed |P| "
        f"(not proportional to how many partitions each count has). "
        f"Constraints filter P; they do not freeze it."
        f"{pref_note}{extra}"
    )
    return {
        "atoms": glued,
        "feasible_count": len(feasible),
        "enumerated": min(enumerated, MAX_ENUM),
        "truncated": truncated,
        "chosen": chosen,
        "rejected": rejected,
        "preferred_mass_count": preferred,
        "note": note,
    }


def _pick_shortlist(
    atoms: list[dict[str, Any]],
    ranked: list[list[int]],
    *,
    cap: int,
    k_min: int,
    k_max: int,
    preferred_mass_count: int | None = None,
) -> list[dict[str, Any]]:
    """
    Diversity-select up to `cap` from ranked feasible assignments.

    Fair across the allowed |P| range: each non-empty mass-count stratum
    gets a near-equal share of the budget, then organizational diversity
    runs *inside* that stratum. A denser stratum (more feasible partitions)
    cannot crowd out a thinner one. Preferred |P| may claim leftover slots
    only after every stratum has its equal floor.
    """
    if cap <= 0:
        return []

    buckets: dict[int, list[list[int]]] = {k: [] for k in range(k_min, k_max + 1)}
    for assign in ranked:
        n_blocks = len(set(assign))
        if n_blocks in buckets:
            buckets[n_blocks].append(assign)

    active = [k for k in range(k_min, k_max + 1) if buckets.get(k)]
    if not active:
        return []
    if preferred_mass_count is not None:
        active.sort(key=lambda k: (abs(k - int(preferred_mass_count)), k))

    available = {k: len(buckets[k]) for k in active}
    quotas = _stratum_quotas(
        active,
        cap,
        preferred=preferred_mass_count,
        available=available,
    )

    chosen: list[dict[str, Any]] = []
    seen: set[frozenset[frozenset[str]]] = set()
    chosen_by_k: dict[int, list[frozenset[frozenset[str]]]] = {k: [] for k in active}
    taken: dict[int, int] = {k: 0 for k in active}

    def push(assign: list[int]) -> bool:
        item = _from_atoms(atoms, assign, _reason(atoms, assign))
        key = _signature(item["groups"])
        if key in seen:
            return False
        k = len(set(assign))
        seen.add(key)
        chosen.append(item)
        if k in chosen_by_k:
            chosen_by_k[k].append(key)
            taken[k] = taken.get(k, 0) + 1
        return True

    # Keep the stated grouping when it is feasible — it spends its stratum's
    # quota but always leads the shortlist for the board / COVER origin.
    for k in list(active):
        for i, assign in enumerate(buckets[k]):
            if _reason(atoms, assign) != "stated grouping":
                continue
            buckets[k].pop(i)
            push(assign)
            break

    def farthest_in_bucket(k: int) -> list[int] | None:
        """Next pick inside stratum k: max distance to same-|P| chosen, then balance."""
        peers = chosen_by_k.get(k) or []
        best: tuple[float, float, int] | None = None
        best_assign: list[int] | None = None
        best_i = -1
        for i, assign in enumerate(buckets[k]):
            item = _from_atoms(atoms, assign, _reason(atoms, assign))
            key = _signature(item["groups"])
            if key in seen:
                continue
            if peers:
                novelty = min(_partition_distance(key, p) for p in peers)
            else:
                novelty = 1.0
            bal = _block_balance(assign)
            score = (novelty, bal, -i)  # earlier rank breaks residual ties
            if best is None or score > best:
                best = score
                best_assign = assign
                best_i = i
        if best_assign is None:
            return None
        buckets[k].pop(best_i)
        return best_assign

    # Fill each stratum up to its fair quota.
    # First half: keep semantic rank (school-shaped seeds for COVER).
    # Second half: organizational diversity so the map still spreads.
    for k in active:
        quota = quotas.get(k, 0)
        if quota <= 0:
            continue
        quality_slots = max(1, (quota + 1) // 2)
        while taken[k] < quota and buckets[k]:
            if taken[k] < quality_slots or not chosen_by_k[k]:
                assign = buckets[k].pop(0)
                if not push(assign):
                    continue
            else:
                assign = farthest_in_bucket(k)
                if assign is None:
                    break
                push(assign)

    # Spillover if some strata ran dry: farthest across leftovers, preferring
    # strata that have fewer picks so far.
    while len(chosen) < cap:
        best_k: int | None = None
        best_i = -1
        best_score: tuple[float, int, float, int] | None = None
        for k in active:
            peers = chosen_by_k.get(k) or []
            for i, cand in enumerate(buckets[k]):
                item = _from_atoms(atoms, cand, _reason(atoms, cand))
                key = _signature(item["groups"])
                if key in seen:
                    continue
                novelty = (
                    min(_partition_distance(key, p) for p in peers) if peers else 1.0
                )
                score = (novelty, -taken.get(k, 0), _block_balance(cand), -i)
                if best_score is None or score > best_score:
                    best_score = score
                    best_k = k
                    best_i = i
        if best_k is None or best_i < 0:
            break
        push(buckets[best_k].pop(best_i))

    # Stated grouping leads; then stable by |P| so the board reads as a range.
    chosen.sort(
        key=lambda item: (
            0 if (item.get("reason") or "") == "stated grouping" else 1,
            len(item.get("groups") or []),
        )
    )
    return chosen


def _stratum_quotas(
    strata: list[int],
    cap: int,
    *,
    preferred: int | None = None,
    available: dict[int, int] | None = None,
) -> dict[int, int]:
    """
    Near-equal slot shares across |P| strata.

    Not proportional to feasible-set size — a larger Bell slice must not
    dominate the shortlist. Leftover slots go to `preferred` first when set,
    otherwise round-robin. Quotas never exceed what each stratum still has.
    """
    strata = [k for k in strata if (available or {k: 1}).get(k, 0) > 0]
    if not strata or cap <= 0:
        return {}
    n = len(strata)
    if cap < n:
        # Still cover as many strata as possible (range first).
        order = list(strata)
        if preferred is not None and preferred in order:
            order = [preferred] + [k for k in order if k != preferred]
        return {k: (1 if i < cap else 0) for i, k in enumerate(order)}

    base = cap // n
    rem = cap % n
    quotas = {k: base for k in strata}
    order = list(strata)
    if preferred is not None and preferred in quotas:
        order = [preferred] + [k for k in order if k != preferred]
        quotas[preferred] += rem
    else:
        for i in range(rem):
            quotas[order[i % len(order)]] += 1

    if available:
        spill = 0
        for k in strata:
            room = int(available.get(k, 0))
            if quotas[k] > room:
                spill += quotas[k] - room
                quotas[k] = room
        if spill:
            for k in order:
                room = int(available.get(k, 0)) - quotas[k]
                if room <= 0:
                    continue
                take = min(room, spill)
                quotas[k] += take
                spill -= take
                if spill <= 0:
                    break
    return quotas


def _block_balance(assign: list[int]) -> float:
    """Higher when block sizes are more even (tie-break against mega+alone)."""
    if not assign:
        return 0.0
    counts: dict[int, int] = defaultdict(int)
    for b in assign:
        counts[b] += 1
    vals = list(counts.values())
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    var = sum((s - mean) ** 2 for s in vals) / len(vals)
    return -var


def _coexist_pairs(sig: frozenset[frozenset[str]]) -> frozenset[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for block in sig:
        depts = sorted(str(d) for d in block)
        for i, a in enumerate(depts):
            for b in depts[i + 1 :]:
                pairs.add(frozenset({a, b}))
    return frozenset(pairs)


def _partition_distance(
    a: frozenset[frozenset[str]], b: frozenset[frozenset[str]]
) -> float:
    """Jaccard distance on which department pairs share a mass."""
    left, right = _coexist_pairs(a), _coexist_pairs(b)
    if not left and not right:
        return 0.0 if a == b else 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left ^ right) / len(union)


def department_atoms(session: Any) -> list[dict[str, Any]]:
    """One atom per department so P can regroup inside the constraints."""
    names: list[str] = []
    if hasattr(session, "department_names"):
        try:
            names = [str(d) for d in (session.department_names() or [])]
        except Exception:
            names = []
    if not names:
        names = [str(d) for m in (session.masses or []) for d in (m.departments or [])]
    home = {str(d): m for m in (session.masses or []) for d in (m.departments or [])}
    atoms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dept in names:
        if not dept or dept in seen:
            continue
        seen.add(dept)
        mass = home.get(dept)
        atoms.append(
            {
                "id": _slug(dept),
                "name": dept,
                "departments": [dept],
                "story_count": int(getattr(mass, "story_count", 2) or 2),
            }
        )
    return atoms


def _slug(name: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(name)).strip("_")
    return token or "dept"


def _alone_as_apart(
    atoms: list[dict[str, Any]], alone: list[str]
) -> list[frozenset[str]]:
    """An alone department cannot share a mass with anyone."""
    names = [str(d) for atom in atoms for d in atom.get("departments") or []]
    pairs: list[frozenset[str]] = []
    for dept in alone:
        if dept not in names:
            continue
        for other in names:
            if other != dept:
                pairs.append(frozenset({dept, other}))
    return pairs


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


def _restricted_growth_bounded(n: int, k_min: int, k_max: int):
    """Canonical assignments whose block count is in [k_min, k_max]."""
    if n <= 0:
        return
    k_min = max(1, min(int(k_min), n))
    k_max = max(k_min, min(int(k_max), n))
    assign = [0] * n

    def rec(i: int, blocks: int):
        if blocks > k_max:
            return
        if blocks + (n - i) < k_min:
            return
        if i == n:
            if k_min <= blocks <= k_max:
                yield assign[:]
            return
        for b in range(blocks):
            assign[i] = b
            yield from rec(i + 1, blocks)
        if blocks < k_max:
            assign[i] = blocks
            yield from rec(i + 1, blocks + 1)

    yield from rec(1, 1)


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
    # Typical school three-bar: classroom wing | public/arts | athletics+dining.
    if len(blocks) >= 3 and _is_school_bars(atoms, assign):
        return "school bars: academic / public / athletics"
    return f"csp partition: {len(blocks)} masses"


def _is_school_bars(atoms: list[dict[str, Any]], assign: list[int]) -> bool:
    """Academic together, athletics/dining together, arts out of the classroom bar."""
    academic = [i for i, a in enumerate(atoms) if "academic" in _families(a)]
    arts = [i for i, a in enumerate(atoms) if "arts" in _families(a)]
    ath = [
        i
        for i, a in enumerate(atoms)
        if _families(a) & {"athletics", "dining"}
    ]
    if len(academic) < 1 or len(ath) < 1:
        return False
    if len({assign[i] for i in academic}) != 1:
        return False
    if len({assign[i] for i in ath}) != 1:
        return False
    if assign[academic[0]] == assign[ath[0]]:
        return False
    if arts and any(assign[i] == assign[academic[0]] for i in arts):
        return False
    return True


def _shape_bonus(atoms: list[dict[str, Any]], assign: list[int]) -> float:
    """
    Soft school-shaped tiebreak so COVER's 12–20 shortlist is not a random
    slice of hundreds of equally ranked partitions.

    Mass count is still not a quality signal — only organization shape.
    """
    if not atoms or not assign:
        return 0.0
    bonus = 0.0
    academic = [i for i, a in enumerate(atoms) if "academic" in _families(a)]
    arts = [i for i, a in enumerate(atoms) if "arts" in _families(a)]
    ath = [
        i
        for i, a in enumerate(atoms)
        if _families(a) & {"athletics", "dining"}
    ]
    if len(academic) >= 2 and len({assign[i] for i in academic}) == 1:
        bonus += 3.0
    elif len(academic) >= 2:
        bonus -= 1.5
    if ath and len({assign[i] for i in ath}) == 1:
        bonus += 2.0
    if academic and arts:
        acad_block = assign[academic[0]]
        if all(assign[i] != acad_block for i in arts):
            bonus += 2.0
        else:
            # Arts jammed into the classroom bar often fights width locks.
            bonus -= 1.0
    # Core academic + special ed share a wing when both are atoms.
    core_i = sped_i = None
    media_i = None
    for i, atom in enumerate(atoms):
        blob = " ".join(str(d).lower() for d in (atom.get("departments") or []))
        if "core academic" in blob:
            core_i = i
        if "special education" in blob:
            sped_i = i
        if "media" in blob:
            media_i = i
    if core_i is not None and sped_i is not None and assign[core_i] == assign[sped_i]:
        bonus += 2.0
    # Media often rides the tall academic bar (top floor) rather than admin.
    if media_i is not None and academic and assign[media_i] == assign[academic[0]]:
        bonus += 1.0
    # Clinic / admin belong with the public bar, not the gym wing.
    for i, atom in enumerate(atoms):
        blob = " ".join(str(d).lower() for d in (atom.get("departments") or []))
        if not any(tok in blob for tok in ("medical", "administration", "guidance")):
            continue
        if ath and assign[i] == assign[ath[0]]:
            bonus -= 0.75
        if arts and assign[i] == assign[arts[0]]:
            bonus += 0.75
    # Light balance only — a glued gym atom as its own mass is common and legal.
    bonus += 0.05 * _block_balance(assign)
    return bonus


def _score(
    atoms: list[dict[str, Any]],
    assign: list[int],
    *,
    preferred_mass_count: int | None = None,
) -> tuple:
    """
    Semantic preference first. Mass count is not a quality signal by itself.

    Any allowed |P| range is flexible: higher does not beat lower unless the
    brief states a preferred mass count, in which case closer |P| ranks above
    farther |P| after the semantic tier. Shape bonus is a soft tiebreak so
    school-like bars surface in the COVER shortlist.
    """
    reason = _reason(atoms, assign)
    rank = {
        "stated grouping": 1000,
        "colocate gym with dining": 900,
        "school bars: academic / public / athletics": 850,
        "colocate academic with arts": 800,
        "arts with gym and dining": 700,
        "academic apart from a combined public/support bar": 600,
    }.get(reason, 200)
    if reason.startswith("colocate ") and rank == 200:
        # Mild note only — not enough to starve other |P| values in the shortlist.
        rank = 250
    shape = _shape_bonus(atoms, assign)
    if preferred_mass_count is None:
        return (rank, 0, shape)
    n_blocks = len(set(assign))
    # Closer to the stated preference wins; direction can be high or low.
    proximity = -abs(n_blocks - int(preferred_mass_count))
    return (rank, proximity, shape)


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
