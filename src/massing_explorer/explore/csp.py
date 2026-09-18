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
from collections.abc import Callable
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


def _csp_inputs(session: Any) -> dict[str, Any]:
    """Atoms and P constraints shared by the UI report and the ordered stream."""
    locked = grouping_is_required(session)
    atoms = [] if locked else department_atoms(session)
    if not atoms:
        atoms = atoms_from_session(session)
    apart = list(required_apart(session))
    alone = required_alone(session)
    apart.extend(_alone_as_apart(atoms, alone))
    return {
        "locked": locked,
        "atoms": atoms,
        "apart": apart,
        "together": required_together(session),
        "alone": alone,
        "bounds": required_mass_bounds(session),
        "preferred": preferred_mass_count(session),
    }


def describe_csp(session: Any, cap: int = UI_PARTITION_CAP) -> dict[str, Any]:
    """Report for the archive, planner, and the Phase 6 board (UI shortlist)."""
    inputs = _csp_inputs(session)
    locked = inputs["locked"]
    atoms = inputs["atoms"]
    apart = inputs["apart"]
    together = inputs["together"]
    alone = inputs["alone"]
    bounds = inputs["bounds"]
    preferred = inputs["preferred"]
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


def ordered_partition_candidates(
    session: Any,
    *,
    cap: int,
    exclude: Callable[[list[dict[str, Any]]], bool] | None = None,
) -> list[dict[str, Any]]:
    """
    The same coverage-ordered stream the CSP shortlist admits, for P-pool growth.

    `exclude` marks partitions the caller already holds: they never take a seat,
    but their relationship features count as covered, so expansion reaches for
    organizations the pool is still missing instead of rank-tail neighbours.
    """
    inputs = _csp_inputs(session)
    if inputs["locked"] or not inputs["atoms"] or cap <= 0:
        return []
    solved = solve_partitions(
        inputs["atoms"],
        apart=inputs["apart"],
        together=inputs["together"],
        cap=cap,
        mass_bounds=inputs["bounds"],
        preferred_mass_count=inputs["preferred"],
        exclude=exclude,
    )
    return list(solved["chosen"])


def solve_partitions(
    atoms: list[dict[str, Any]],
    apart: list[frozenset[str]] | None = None,
    together: list[frozenset[str]] | None = None,
    cap: int = UI_PARTITION_CAP,
    mass_bounds: tuple[int, int] | None = None,
    preferred_mass_count: int | None = None,
    exclude: Callable[[list[dict[str, Any]]], bool] | None = None,
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
        exclude=exclude,
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
        f"shortlist {len(chosen)} with |P| quotas then relationship-feature "
        f"coverage (art / media / admin placement, gym+dining isolation, "
        f"academic cohesion, and their pairs), quality refill after. "
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
    exclude: Callable[[list[dict[str, Any]]], bool] | None = None,
) -> list[dict[str, Any]]:
    """
    Diversity-select up to `cap` from ranked feasible assignments.

    Two-level quotas against selection compression:
      1) fair shares across allowed |P|
      2) within each |P|, greedy *relationship-feature coverage* — every
         art / media / admin placement, gym+dining isolation and academic
         cohesion value, plus the pairwise combinations of those values —
         then semantic-rank refill for quality depth

    School prior ranks *inside* a coverage cell and for post-coverage refill.
    It does not choose which features appear on the shortlist.
    """
    if cap <= 0:
        return []

    buckets: dict[int, list[list[int]]] = {k: [] for k in range(k_min, k_max + 1)}
    # Features the caller already holds still count as covered (pool growth).
    seeded: dict[int, set[tuple[str, ...]]] = {k: set() for k in range(k_min, k_max + 1)}
    for assign in ranked:
        n_blocks = len(set(assign))
        if n_blocks not in buckets:
            continue
        if exclude is not None and exclude(_from_atoms(atoms, assign, "")["groups"]):
            seeded[n_blocks].update(_coverage_keys(atoms, assign))
            continue
        buckets[n_blocks].append(assign)

    active = [k for k in range(k_min, k_max + 1) if buckets.get(k)]
    if not active:
        return []
    if preferred_mass_count is not None:
        active.sort(key=lambda k: (abs(k - int(preferred_mass_count)), k))

    available = {k: len(buckets[k]) for k in active}
    mass_quotas = _stratum_quotas(
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

    # Stated grouping leads its stratum when feasible.
    for k in list(active):
        for i, assign in enumerate(buckets[k]):
            if _reason(atoms, assign) != "stated grouping":
                continue
            buckets[k].pop(i)
            push(assign)
            break

    def farthest_in_pool(
        pool: list[list[int]], peers: list[frozenset[frozenset[str]]]
    ) -> tuple[int, list[int]] | None:
        best: tuple[float, float, int] | None = None
        best_i = -1
        best_assign: list[int] | None = None
        for i, assign in enumerate(pool):
            key = _assign_signature(atoms, assign)
            if key in seen:
                continue
            novelty = (
                min(_partition_distance(key, p) for p in peers) if peers else 1.0
            )
            bal = _block_balance(assign)
            score = (novelty, bal, -i)
            if best is None or score > best:
                best = score
                best_i = i
                best_assign = assign
        if best_assign is None:
            return None
        return best_i, best_assign

    for k in active:
        quota = int(mass_quotas.get(k, 0) or 0)
        if quota <= 0 or not buckets[k]:
            continue

        # Secondary strata: relationship-feature coverage (not coarse families,
        # not geometric distance). Coverage runs before any rank refill so a
        # school-bar basin cannot take every seat.
        for assign in _feature_coverage_order(
            atoms,
            buckets[k],
            limit=quota - taken[k],
            skip=seen,
            covered=seeded.get(k),
        ):
            if taken[k] >= quota:
                break
            push(assign)

        # Remaining seats: semantic-rank refill (quality depth), then distance.
        for assign in buckets[k]:
            if taken[k] >= quota:
                break
            push(assign)

        leftovers = [a for a in buckets[k] if _assign_signature(atoms, a) not in seen]
        while taken[k] < quota and leftovers:
            picked = farthest_in_pool(leftovers, chosen_by_k.get(k) or [])
            if picked is None:
                break
            idx, assign = picked
            leftovers.pop(idx)
            push(assign)

        buckets[k] = leftovers

    # Spillover across |P| if some strata ran dry.
    while len(chosen) < cap:
        best_k: int | None = None
        best_i = -1
        best_score: tuple[float, int, float, int] | None = None
        for k in active:
            peers = chosen_by_k.get(k) or []
            for i, cand in enumerate(buckets[k]):
                key = _assign_signature(atoms, cand)
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

    chosen.sort(
        key=lambda item: (
            0 if (item.get("reason") or "") == "stated grouping" else 1,
            len(item.get("groups") or []),
        )
    )
    return chosen


def _stratum_quotas(
    strata: list[Any],
    cap: int,
    *,
    preferred: Any | None = None,
    available: dict[Any, int] | None = None,
) -> dict[Any, int]:
    """
    Near-equal slot shares across strata (|P| or relationship archetype).

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


def _atom_blob(atom: dict[str, Any]) -> str:
    return " ".join(str(d).lower() for d in (atom.get("departments") or []))


def _academic_block_id(atoms: list[dict[str, Any]], assign: list[int]) -> int | None:
    """Block id that carries core/academic program, if any."""
    core = None
    academic = None
    for i, atom in enumerate(atoms):
        blob = _atom_blob(atom)
        if "core academic" in blob:
            core = assign[i]
            break
        if academic is None and "academic" in _families(atom):
            academic = assign[i]
    if core is not None:
        return core
    return academic


def _program_role(
    atoms: list[dict[str, Any]],
    assign: list[int],
    *,
    tokens: tuple[str, ...],
    academic_block: int | None,
) -> str:
    """
    Where one main program sits: with academic, alone, with athletics, or other.
    """
    idxs = [
        i
        for i, atom in enumerate(atoms)
        if any(tok in _atom_blob(atom) for tok in tokens)
    ]
    if not idxs:
        return "absent"
    block = assign[idxs[0]]
    if academic_block is not None and block == academic_block:
        return "with_academic"
    members = sum(1 for b in assign if b == block)
    if members <= 1:
        return "alone"
    ath_blocks = {
        assign[i]
        for i, atom in enumerate(atoms)
        if _families(atom) & {"athletics", "dining"}
    }
    if block in ath_blocks:
        return "with_athletics"
    return "other"


def _block_motif(atoms: list[dict[str, Any]], assign: list[int]) -> tuple[str, ...]:
    """
    Coarse role pattern for shortlist diversity.

    Tracks where art, media, and admin sit relative to the academic bar —
    enough to tell school-bar variants from art-on-academic / admin-alone
    basins without hardcoding full groupings.
    """
    if not atoms or not assign or len(atoms) != len(assign):
        return ("absent", "absent", "absent")
    academic_block = _academic_block_id(atoms, assign)
    return (
        _program_role(
            atoms, assign, tokens=("art", "music"), academic_block=academic_block
        ),
        _program_role(atoms, assign, tokens=("media",), academic_block=academic_block),
        _program_role(
            atoms,
            assign,
            tokens=("administration", "guidance"),
            academic_block=academic_block,
        ),
    )


def _gym_dining_role(atoms: list[dict[str, Any]], assign: list[int]) -> str:
    """Whether dining+athletics hold a mass of their own (the isolated bar)."""
    idxs = [i for i, a in enumerate(atoms) if _families(a) & {"athletics", "dining"}]
    if not idxs:
        return "absent"
    blocks = {assign[i] for i in idxs}
    if len(blocks) > 1:
        return "split"
    block = next(iter(blocks))
    riders = [i for i, b in enumerate(assign) if b == block and i not in set(idxs)]
    return "shared" if riders else "isolated"


def _academic_cohesion(atoms: list[dict[str, Any]], assign: list[int]) -> str:
    """Whether the academic family (core + special ed) keeps one wing."""
    idxs = [i for i, a in enumerate(atoms) if "academic" in _families(a)]
    if not idxs:
        return "absent"
    if len(idxs) < 2:
        return "single"
    return "together" if len({assign[i] for i in idxs}) == 1 else "split"


_RELATIONSHIP_FEATURES: tuple[str, ...] = (
    "art",
    "media",
    "admin",
    "gym_dining",
    "academic",
)

# Placement roles: their joint motif is the cell the four coarse families
# collapsed, so it earns a coverage pass of its own on top of singles/pairs.
_ROLE_FEATURES: tuple[str, ...] = ("art", "media", "admin")


def _relationship_features(
    atoms: list[dict[str, Any]], assign: list[int]
) -> dict[str, str]:
    """
    Generic relationship features the shortlist must cover.

    Where art, media, and admin sit; whether dining+athletics keep their own
    mass; whether the academic family stays cohesive. Token matching only —
    no hardcoded groupings, so the same features read any program.
    """
    if not atoms or not assign or len(atoms) != len(assign):
        return {}
    art, media, admin = _block_motif(atoms, assign)
    return {
        "art": art,
        "media": media,
        "admin": admin,
        "gym_dining": _gym_dining_role(atoms, assign),
        "academic": _academic_cohesion(atoms, assign),
    }


def _coverage_keys(
    atoms: list[dict[str, Any]], assign: list[int]
) -> tuple[tuple[str, ...], ...]:
    """
    Individual feature values, pairwise combinations, and the art/media/admin
    motif triple (the cell that used to collapse under four coarse families).
    """
    feats = _relationship_features(atoms, assign)
    names = [f for f in _RELATIONSHIP_FEATURES if feats.get(f)]
    keys: list[tuple[str, ...]] = [(f, feats[f]) for f in names]
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            keys.append((left, feats[left], right, feats[right]))
    if all(feats.get(f) for f in _ROLE_FEATURES):
        keys.append(("motif", feats["art"], feats["media"], feats["admin"]))
    return tuple(keys)


def _round_robin(
    bags: dict[Any, list[Any]], order: list[Any]
) -> list[Any]:
    """
    One entry per group per round, so no feature (or pair) hogs the seats.

    Rare values of a feature rank late; without this the walk would spend the
    quota on whichever feature happened to vary first.
    """
    keys = [k for k in order if bags.get(k)]
    out: list[Any] = []
    depth = 0
    while True:
        added = False
        for key in keys:
            bag = bags[key]
            if depth < len(bag):
                out.append(bag[depth])
                added = True
        if not added:
            return out
        depth += 1


def _feature_coverage_order(
    atoms: list[dict[str, Any]],
    pool: list[list[int]],
    *,
    limit: int,
    skip: set[frozenset[frozenset[str]]] | None = None,
    covered: set[tuple[str, ...]] | None = None,
) -> list[list[int]]:
    """
    Seat relationship-feature coverage before any school-prior refill.

    Critical art×media×admin motifs (art alone / art-on-academic) get seats
    first — two admin variants per (art, media) — so rare basins are not
    buried under school-bar singles. Structural features, remaining motifs,
    leftover placement values, and pairs follow.

    Each uncovered target takes the best-ranked candidate that carries it.
    Motif triples are not retired by collateral singles/pairs.
    """
    if limit <= 0 or not pool:
        return []
    taken = set(skip or ())
    cands: list[tuple[list[int], tuple[tuple[str, ...], ...]]] = []
    for assign in pool:
        if _assign_signature(atoms, assign) in taken:
            continue
        cands.append((assign, _coverage_keys(atoms, assign)))
    if not cands:
        return []

    by_feature: dict[str, list[tuple[str, ...]]] = {}
    by_pair: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    motifs: list[tuple[str, ...]] = []
    known: set[tuple[str, ...]] = set()
    for _assign, keys in cands:
        for key in keys:
            if key in known:
                continue
            known.add(key)
            if key and key[0] == "motif":
                motifs.append(key)
            elif len(key) == 2:
                by_feature.setdefault(key[0], []).append(key)
            else:
                by_pair.setdefault((key[0], key[2]), []).append(key)

    role_pairs = [
        (left, right)
        for i, left in enumerate(_ROLE_FEATURES)
        for right in _ROLE_FEATURES[i + 1 :]
    ]
    other_pairs = [
        (left, right)
        for i, left in enumerate(_RELATIONSHIP_FEATURES)
        for right in _RELATIONSHIP_FEATURES[i + 1 :]
        if (left, right) not in role_pairs
    ]

    # Motif cells bucketed by art placement, so a rare basin is not buried
    # under the cousins that share the most common placement.
    motifs_by_art: dict[str, list[tuple[str, ...]]] = {}
    for key in motifs:
        motifs_by_art.setdefault(key[1], []).append(key)

    # Every feature value first: a handful of picks covers all of them, so
    # gym+dining isolation and academic cohesion are never starved by the
    # twelve placement values. Motif cells next for combination depth, then
    # the pairs, which pool expansion keeps walking after the shortlist fills.
    targets = _round_robin(by_feature, list(_RELATIONSHIP_FEATURES))
    targets += _round_robin(motifs_by_art, list(motifs_by_art))
    targets += _round_robin(by_pair, role_pairs)
    targets += _round_robin(by_pair, other_pairs)

    done = set(covered or ())
    picked: list[list[int]] = []
    used: set[int] = set()
    for target in targets:
        if len(picked) >= limit:
            break
        if target in done:
            continue
        for i, (assign, keys) in enumerate(cands):
            if i in used or target not in keys:
                continue
            used.add(i)
            for key in keys:
                if key and key[0] == "motif" and key != target:
                    continue
                done.add(key)
            picked.append(assign)
            break
    return picked


def _relationship_family(atoms: list[dict[str, Any]], assign: list[int]) -> str:
    """
    Coarse organizational label for reports and diagnostics.

    Shortlist seats come from `_relationship_features` coverage, not from these
    four buckets — they collapse too many distinct organizations into one cell.
    Families (not partition distance, not school-prior score):
      - school_bars
      - arts_with_academic
      - arts_separate
      - other
    """
    if _is_school_bars(atoms, assign):
        return "school_bars"
    art = _block_motif(atoms, assign)[0]
    if art == "with_academic":
        return "arts_with_academic"
    if art == "alone":
        return "arts_separate"
    return "other"


def _relationship_archetype(atoms: list[dict[str, Any]], assign: list[int]) -> str:
    """Backward-compatible alias for relationship-family classification."""
    return _relationship_family(atoms, assign)


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


def _assign_signature(
    atoms: list[dict[str, Any]], assign: list[int]
) -> frozenset[frozenset[str]]:
    """Same signature as `_signature`, without building the group payloads."""
    blocks: dict[int, set[str]] = defaultdict(set)
    for i, atom in enumerate(atoms):
        blocks[assign[i]].update(str(d) for d in atom["departments"])
    return frozenset(frozenset(b) for b in blocks.values())


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
        # In the school-bars prior the athletics+dining bar *is* its own bar:
        # a double-height gym does not host classrooms or offices.
        if _gym_dining_role(atoms, assign) == "isolated":
            bonus += 1.5
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
