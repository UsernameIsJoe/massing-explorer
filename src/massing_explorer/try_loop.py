"""
Three-stage search over the levers a brief left open.

Stage one is a designed sample of the open levers. It stops when the remaining
design cannot open a new legal cell, not after a lucky batch. Stage two keeps
several feasible basins. Stage three allocates local moves by particle weight,
with a floor so a light elite is not deleted. A length cap is only a filter.
"""

from __future__ import annotations

import itertools
from typing import Any

STAGE1_CAP = 28
STAGE3_CAP = 8
BUDGET = STAGE1_CAP + STAGE3_CAP


def improve_scheme(
    session: Any,
    client: Any = None,
    max_tries: int = BUDGET,
) -> dict[str, Any]:
    """Cover the open levers, name the feasible basins, then tune inside them."""
    from .solver import solve_massing_study
    from .tools import solve_dimensions

    del client  # The engine owns the neighbors. The model does not invent sizes.
    budget = max(1, int(max_tries))
    if session.constraints.get("length_over_width") and not session.constraints.get("stated_ratio"):
        session.constraints["stated_ratio"] = session.constraints["length_over_width"]

    prefs = list(session.constraints.get("mass_preferences") or [])
    held_widths = _hold_solver_widths(session)
    factors = _open_factors(session, prefs)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    def evaluate(candidate: dict[str, Any], stage: int) -> dict[str, Any] | None:
        if len(records) >= budget:
            return None
        _apply_candidate(session, candidate)
        _strip_retry_widths(session)
        key = _fingerprint(session)
        if key in seen:
            return None
        seen.add(key)
        result = solve_massing_study(session)
        scored = _score(session, result, prefs)
        from .traits import measure_traits

        traits = measure_traits(result, session)
        levels = _read_levels(session, factors)
        record = {
            "try": len(records) + 1,
            "stage": stage,
            "reason": candidate.get("reason") or "",
            "fingerprint": key,
            "fits_limitations": scored["limit_fails"] == 0,
            "limit_fails": scored["limit_fails"],
            "preference_misses": scored["pref_misses"],
            "mass_preference": scored["mass_pref"],
            "score": scored["rank"],
            "stage2": scored["stage2"],
            "lengths": scored["lengths"],
            "levels": levels,
            "basin": _basin_key(session),
            "typology": _typology_key(session),
            "traits": traits,
            "state": _snapshot(session),
        }
        records.append(record)
        return record

    from .sample_space import (
        cell_key,
        coverage_status,
        designed_points,
        next_design_point,
    )

    baseline = _baseline_point(session, prefs, factors)
    design = designed_points(factors, baseline)
    attempted: list[dict[str, Any]] = []
    legal_cells: set[str] = set()
    failed_cells: set[str] = set()
    legal_types: set[str] = set()
    stage1_limit = min(budget, STAGE1_CAP)
    stopped = None
    while len(records) < stage1_limit:
        point = next_design_point(factors, design, attempted, legal_cells, failed_cells)
        if point is None:
            stopped = "design_exhausted"
            break
        attempted.append(point)
        record = evaluate(_candidate_from_point(session, prefs, factors, point), 1)
        if record is None:
            continue
        cell = cell_key(point, factors)
        if record["fits_limitations"]:
            legal_cells.add(cell)
            legal_types.add(record["typology"])
            failed_cells.discard(cell)
        elif cell not in legal_cells:
            failed_cells.add(cell)
    at_cap = len(records) >= stage1_limit and stopped is None
    space = coverage_status(
        factors, design, attempted, legal_cells, failed_cells, at_cap
    )
    if space.get("map_complete"):
        stopped = space.get("stopped")
    elif stopped is None:
        stopped = space.get("stopped") or "safety_cap"
    coverage = _coverage_report(factors, records, legal_types, stopped)
    coverage["map_complete"] = bool(space["map_complete"])
    coverage["designed"] = space["designed"]
    coverage["design"] = space["design"]
    coverage["margins_missing"] = space["margins_missing"]
    coverage["note"] = space["note"]
    feasible = [r for r in records if r["fits_limitations"]]
    taste = (session.constraints.get("preference_learning") or {}).get("weights") or {}
    basins = select_elites(records, taste)
    stage3 = _stage_three(
        session, records, factors, basins, coverage, budget, evaluate, taste
    )

    chosen = _winner(records, basins, taste)
    _release_solver_widths(session, held_widths)
    if chosen is not None:
        _restore(session, chosen["state"])
        solved = solve_dimensions(session)
    else:
        solved = solve_dimensions(session)

    fits = bool(chosen and chosen["fits_limitations"])
    note = _note(records, coverage, basins, stage3, fits, budget)
    compact_basins = [
        {
            "basin": b["key"],
            "fits": b["best"]["fits_limitations"],
            "mass_preference": b["best"]["mass_preference"],
            "lengths": b["best"]["lengths"],
            "reason": b["best"]["reason"],
        }
        for b in basins
    ]
    return {
        "ok": True,
        "tries": len(records),
        "safety_cap": budget,
        "feasible": len(feasible),
        "fits_limitations": fits,
        "coverage": coverage,
        "schemes": _scheme_catalog(records),
        "basins": compact_basins,
        "stage3": stage3,
        "kept": _public_entry(chosen) if chosen else None,
        "log": [_public_entry(r) for r in records],
        "solved": solved,
        "note": note,
    }


def _strip_retry_widths(session: Any) -> None:
    for key in list(session.constraints):
        if key.endswith("_width_ft") and key not in {
            "max_building_width_ft",
            "max_total_width_ft",
            "fixed_width_ft",
            "preferred_width_ft",
            "academic_width_ft",
        }:
            session.constraints.pop(key, None)


def _hold_solver_widths(session: Any) -> dict[str, float]:
    """A layout retry writes a width onto the study. That must not become the next scheme."""
    held = {}
    for key in list(session.constraints):
        if key.endswith("_width_ft") and key not in {
            "max_building_width_ft",
            "max_total_width_ft",
            "fixed_width_ft",
            "preferred_width_ft",
            "academic_width_ft",
        }:
            held[key] = session.constraints.pop(key)
    return held


def _release_solver_widths(session: Any, held: dict[str, float]) -> None:
    for key in list(session.constraints):
        if key.endswith("_width_ft") and key not in held and key not in {
            "max_building_width_ft",
            "max_total_width_ft",
            "fixed_width_ft",
            "preferred_width_ft",
            "academic_width_ft",
        }:
            session.constraints.pop(key, None)
    session.constraints.update(held)


def _open_factors(session: Any, prefs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Levers this brief left free. A lock, a pin, or an unasked shape is not a factor."""
    by_mass = {p.get("mass_id"): p for p in prefs if p.get("mass_id")}
    factors: list[dict[str, Any]] = []
    for mass in session.masses:
        stories = _story_options(session, mass, by_mass.get(mass.id))
        if len(stories) > 1:
            factors.append(
                {
                    "id": f"stories:{mass.id}",
                    "kind": "stories",
                    "mass_id": mass.id,
                    "name": mass.name,
                    "levels": list(stories),
                }
            )
        pref = by_mass.get(mass.id) or {}
        shapes = _shape_levels(pref)
        if len(shapes) > 1:
            factors.append(
                {
                    "id": f"shape:{mass.id}",
                    "kind": "shape",
                    "mass_id": mass.id,
                    "name": mass.name,
                    "levels": shapes,
                }
            )
    if not session.constraints.get("loading_required"):
        factors.append({"id": "loading", "kind": "loading", "levels": ["double", "single"]})
    owns_shape = any(
        (by_mass.get(m.id) or {}).get("shape") or (by_mass.get(m.id) or {}).get("ratio")
        for m in session.masses
    )
    if session.constraints.get("stated_ratio") and not owns_shape:
        factors.append({"id": "ratio", "kind": "ratio", "levels": [True, False]})
    return factors


def _shape_levels(pref: dict[str, Any]) -> list[str]:
    shape = pref.get("shape")
    if shape == "thin":
        return ["thin", "bar", "box"]
    if shape == "box":
        return ["box", "cube", "bar"]
    if shape == "cube":
        return ["cube", "box", "bar"]
    if pref.get("ratio") and abs(float(pref["ratio"]) - 1.0) < 1e-6:
        return ["box", "cube", "bar"]
    return []


def _coverage_candidates(
    session: Any,
    prefs: list[dict[str, Any]],
    factors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Each open setting once, others held at the stated preference. Not a walk to the cap."""
    by_mass = {p.get("mass_id"): p for p in prefs if p.get("mass_id")}
    base_stories = _preferred_stories(session, by_mass)
    base_shapes = {
        m.id: _shape_token_to_value((by_mass.get(m.id) or {}).get("shape"))
        for m in session.masses
    }
    base_ratios = {m.id: (by_mass.get(m.id) or {}).get("ratio") for m in session.masses}
    loading = session.constraints.get("loading") or "double"
    use_ratio = bool(session.constraints.get("stated_ratio")) and not any(base_shapes.values())
    out: list[dict[str, Any]] = []

    def add(stories, shapes, reason, **extra):
        out.append(
            {
                "stories": dict(stories),
                "shapes": {k: _shape_token_to_value(v) for k, v in shapes.items()},
                "ratios": dict(base_ratios),
                "loading": extra.pop("loading", loading),
                "use_ratio": extra.pop("use_ratio", use_ratio),
                "reason": reason,
                **extra,
            }
        )

    add(base_stories, base_shapes, "stated preference")
    if any(base_shapes.values()):
        add(
            base_stories,
            {m.id: None for m in session.masses},
            "functional bars at the preferred story counts",
        )

    for factor in factors:
        if factor["kind"] == "stories":
            for count in factor["levels"]:
                if int(count) == int(base_stories.get(factor["mass_id"], count)):
                    continue
                stories = dict(base_stories)
                stories[factor["mass_id"]] = int(count)
                add(stories, base_shapes, f"cover stories on {factor['name']}: {count}")
        elif factor["kind"] == "shape":
            current = _value_to_shape_token(base_shapes.get(factor["mass_id"]))
            for level in factor["levels"]:
                if level == current:
                    continue
                shapes = dict(base_shapes)
                shapes[factor["mass_id"]] = _shape_token_to_value(level)
                add(dict(base_stories), shapes, f"cover shape of {factor['name']}: {level}")
        elif factor["kind"] == "loading":
            for mode in factor["levels"]:
                if mode == loading:
                    continue
                add(base_stories, base_shapes, f"cover loading: {mode}", loading=mode)
        elif factor["kind"] == "ratio":
            add(
                base_stories,
                base_shapes,
                "cover the shared ratio turned off",
                use_ratio=False,
            )

    story_factors = [f for f in factors if f["kind"] == "stories"]
    if story_factors:
        ids = [f["mass_id"] for f in story_factors]
        options = [f["levels"] for f in story_factors]
        product = 1
        for opts in options:
            product *= max(1, len(opts))
        if 1 < product <= 12:
            for combo in itertools.product(*options):
                stories = dict(base_stories)
                for mid, count in zip(ids, combo):
                    stories[mid] = int(count)
                add(stories, base_shapes, "story combination at the preferred shapes")
    return out


def _baseline_point(
    session: Any,
    prefs: list[dict[str, Any]],
    factors: list[dict[str, Any]],
) -> dict[str, Any]:
    """The stated-preference corner of the designed space."""
    by_mass = {p.get("mass_id"): p for p in prefs if p.get("mass_id")}
    stories = _preferred_stories(session, by_mass)
    point: dict[str, Any] = {}
    for factor in factors:
        if factor["kind"] == "stories":
            point[factor["id"]] = int(stories.get(factor["mass_id"], factor["levels"][0]))
        elif factor["kind"] == "shape":
            token = _value_to_shape_token(
                _shape_token_to_value((by_mass.get(factor["mass_id"]) or {}).get("shape"))
            )
            if token not in factor["levels"]:
                token = factor["levels"][0]
            point[factor["id"]] = token
        elif factor["kind"] == "loading":
            point[factor["id"]] = session.constraints.get("loading") or "double"
        elif factor["kind"] == "ratio":
            point[factor["id"]] = True
    return point


def _candidate_from_point(
    session: Any,
    prefs: list[dict[str, Any]],
    factors: list[dict[str, Any]],
    point: dict[str, Any],
) -> dict[str, Any]:
    """A design coordinate becomes a solver candidate. Feet are not a coordinate."""
    by_mass = {p.get("mass_id"): p for p in prefs if p.get("mass_id")}
    stories = _preferred_stories(session, by_mass)
    shapes = {
        m.id: _shape_token_to_value((by_mass.get(m.id) or {}).get("shape"))
        for m in session.masses
    }
    ratios = {m.id: (by_mass.get(m.id) or {}).get("ratio") for m in session.masses}
    loading = session.constraints.get("loading") or "double"
    use_ratio = bool(session.constraints.get("stated_ratio")) and not any(shapes.values())
    bits = ["designed point"]
    for factor in factors:
        level = point.get(factor["id"])
        if factor["kind"] == "stories":
            stories[factor["mass_id"]] = int(level)
            bits.append(f"{factor.get('name') or factor['mass_id']} {level} stories")
        elif factor["kind"] == "shape":
            shapes[factor["mass_id"]] = _shape_token_to_value(level)
            bits.append(f"{factor.get('name') or factor['mass_id']} {level}")
        elif factor["kind"] == "loading":
            loading = level
            bits.append(f"loading {level}")
        elif factor["kind"] == "ratio":
            use_ratio = bool(level)
            bits.append("shared ratio" if use_ratio else "shared ratio off")
    return {
        "stories": stories,
        "shapes": shapes,
        "ratios": ratios,
        "loading": loading,
        "use_ratio": use_ratio,
        "reason": ", ".join(bits),
    }


def _story_band(stories: int) -> str:
    if int(stories) <= 1:
        return "low"
    if int(stories) == 2:
        return "mid"
    return "high"


def _typology_key(session: Any) -> str:
    """A legal cell: organization, shape family, story band, loading."""
    loading = session.constraints.get("loading") or "double"
    parts = []
    for mass in session.masses:
        shape = session.constraints.get(f"{mass.id}_shape")
        family = "square" if shape in {"box", "cube"} else "bar"
        parts.append(
            f"{mass.id}:{family}:{_story_band(mass.story_count)}:{','.join(mass.departments)}"
        )
    return f"{loading}|{'|'.join(parts)}"


def _coverage_report(
    factors: list[dict[str, Any]],
    records: list[dict[str, Any]],
    legal_types: set[str] | None = None,
    stopped: str | None = None,
) -> dict[str, Any]:
    seen: dict[str, set[Any]] = {f["id"]: set() for f in factors}
    for record in records:
        if record.get("stage") != 1:
            continue
        levels = record.get("levels") or {}
        for factor in factors:
            if factor["id"] in levels:
                seen[factor["id"]].add(_norm_level(levels[factor["id"]]))
    detail = []
    missing_n = 0
    for factor in factors:
        got = seen[factor["id"]]
        missing = [level for level in factor["levels"] if _norm_level(level) not in got]
        missing_n += len(missing)
        detail.append(
            {
                "id": factor["id"],
                "kind": factor["kind"],
                "levels": list(factor["levels"]),
                "seen": sorted(got, key=lambda v: str(v)),
                "missing": missing,
            }
        )
    complete = missing_n == 0
    types = sorted(legal_types or [])
    map_complete = stopped != "safety_cap"
    if not factors:
        text = "No open levers. The required scheme is the sample."
    elif stopped == "safety_cap":
        text = (
            f"Safety cap hit while new legal typologies were still appearing. "
            f"The map is incomplete ({len(types)} legal typologies). "
            "Missing "
            + (
                ", ".join(f"{item['id']}={item['missing']}" for item in detail if item["missing"])
                or "no lever settings"
            )
        )
    elif stopped == "no_new_typology":
        text = (
            f"Stopped after a batch added no new legal typology "
            f"({len(types)} on the map). Illegal schemes were not counted."
        )
    elif complete:
        text = (
            f"Covered every legal setting of {len(factors)} open levers "
            f"and {len(types)} legal typologies."
        )
    else:
        text = (
            "Coverage incomplete before the cap. Missing "
            + ", ".join(
                f"{item['id']}={item['missing']}" for item in detail if item["missing"]
            )
        )
    return {
        "complete": complete,
        "map_complete": map_complete,
        "stopped": stopped,
        "typologies": types,
        "factors": detail,
        "note": text,
    }


def stated_weight(record: dict[str, Any]) -> float:
    """Higher is closer to the stated preferences. Used until a choice exists."""
    return 1.0 / (1.0 + float(record.get("mass_preference") or 0.0))


def taste_weight(record: dict[str, Any], weights: dict[str, float]) -> float:
    if weights and any(abs(float(v)) > 1e-9 for v in weights.values()):
        from .preference import utility

        return utility(weights, record.get("traits") or {})
    return stated_weight(record)


def effective_sample_size(weights: list[float]) -> float:
    total = sum(max(0.0, w) for w in weights)
    if total <= 0:
        return 0.0
    return (total * total) / sum(max(0.0, w) ** 2 for w in weights)


def select_elites(
    records: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Keep several feasible basins, including one lighter one, not a single winner."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("stage") == 3:
            continue
        groups.setdefault(record.get("basin") or "", []).append(record)

    def best_of(items: list[dict[str, Any]]) -> dict[str, Any]:
        legal = [r for r in items if r["fits_limitations"]]
        pool = legal or items
        return max(pool, key=lambda r: taste_weight(r, weights or {}))

    elites = []
    near: dict[str, Any] | None = None
    for key, items in groups.items():
        best = best_of(items)
        item = {
            "key": key,
            "best": best,
            "count": len(items),
            "weight": taste_weight(best, weights or {}),
        }
        if best["fits_limitations"]:
            elites.append(item)
        elif near is None or best["score"] < near["best"]["score"]:
            near = {**item, "near_miss": True}
    if not elites:
        return [near] if near else []
    elites.sort(key=lambda item: item["weight"], reverse=True)
    kept = elites[:2]
    light = min(elites, key=lambda item: item["weight"])
    if light["key"] not in {item["key"] for item in kept}:
        kept.append(light)
    return kept[:3]


def _select_basins(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A basin is an organization and a shape family, not a score."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("stage") == 3:
            continue
        groups.setdefault(record["basin"], []).append(record)

    def best_of(items: list[dict[str, Any]]) -> dict[str, Any]:
        legal = [r for r in items if r["fits_limitations"]]
        pool = legal or items
        return min(pool, key=lambda r: r["stage2"] if r["fits_limitations"] else r["score"])

    feasible_basins = []
    near: dict[str, Any] | None = None
    for key, items in groups.items():
        best = best_of(items)
        if best["fits_limitations"]:
            feasible_basins.append({"key": key, "best": best, "count": len(items)})
        elif near is None or best["score"] < near["best"]["score"]:
            near = {"key": key, "best": best, "count": len(items), "near_miss": True}
    feasible_basins.sort(key=lambda b: b["best"]["stage2"])
    chosen = feasible_basins[:2]
    if not chosen and near is not None:
        chosen = [near]
    return chosen


def _stage_three(
    session: Any,
    records: list[dict[str, Any]],
    factors: list[dict[str, Any]],
    basins: list[dict[str, Any]],
    coverage: dict[str, Any],
    budget: int,
    evaluate,
    taste: dict[str, float] | None = None,
) -> dict[str, Any]:
    taste = taste or {}
    if not factors:
        return {"ran": False, "improved": False, "reason": "No open lever to tune."}
    if coverage.get("map_complete") is False:
        return {
            "ran": False,
            "improved": False,
            "reason": "Stage one hit the cap before the legal map finished, so it was not tuned.",
        }
    if _fully_enumerated(factors, records):
        return {
            "ran": False,
            "improved": False,
            "reason": "The open levers were already fully enumerated.",
        }
    if not basins:
        return {"ran": False, "improved": False, "reason": "No basin to tune."}

    recovery = not any(b["best"]["fits_limitations"] for b in basins)
    improved = False
    tuned = 0
    before = {
        b["key"]: b["best"]["stage2"] if b["best"]["fits_limitations"] else b["best"]["score"]
        for b in basins
    }
    from .sample_space import allocate_local_tries

    remaining = min(STAGE3_CAP, max(0, budget - len(records)))
    particles = [
        {"id": b["key"], "fits": bool(b["best"]["fits_limitations"]), "weight": b.get("weight") or 0.0}
        for b in basins
    ]
    weights = [p["weight"] for p in particles if p["fits"]]
    collapsed = len([p for p in particles if p["fits"]]) > 1 and effective_sample_size(weights) < 1.25
    if recovery:
        allocation = {b["key"]: remaining for b in basins[:1]}
    elif collapsed:
        allocation = allocate_local_tries(
            [{**p, "weight": 1.0} for p in particles],
            min(remaining, len([p for p in particles if p["fits"]])),
            floor=1,
        )
    else:
        allocation = allocate_local_tries(particles, remaining, floor=1)

    for basin in basins:
        allowed = int(allocation.get(basin["key"]) or 0)
        if allowed <= 0:
            continue
        seed = basin["best"]
        if recovery:
            neighbors = _recovery_neighbors(session, seed, factors)
        else:
            neighbors = _ordered_neighbors(seed, _tune_neighbors(session, seed, factors), taste)
        taken = 0
        for candidate in neighbors:
            if len(records) >= budget or tuned >= STAGE3_CAP or taken >= allowed:
                break
            record = evaluate(candidate, 3)
            if record is None:
                continue
            tuned += 1
            taken += 1
            if record["basin"] != basin["key"]:
                continue
            if recovery:
                if record["score"] < seed["score"]:
                    seed = record
                    basin["best"] = record
                    improved = True
            elif record["fits_limitations"] and taste_weight(record, taste) >= taste_weight(basin["best"], taste):
                if record["stage2"] < before[basin["key"]] or taste_weight(record, taste) > taste_weight(basin["best"], taste):
                    before[basin["key"]] = record["stage2"]
                    basin["best"] = record
                    basin["weight"] = taste_weight(record, taste)
                    improved = True
    if recovery:
        reason = (
            "Tuned the nearest miss to see if one legal step clears the cap."
            if improved
            else "No one-step neighbor of the nearest miss cleared the cap."
        )
    elif improved:
        reason = "A one-step neighbor ranked better inside a kept elite basin."
    elif collapsed:
        reason = "The elite weights collapsed onto one basin, so local tuning stopped."
    else:
        reason = "No one-step neighbor beat the kept basins."
    return {
        "ran": True,
        "improved": improved,
        "tries": tuned,
        "elites": len(basins),
        "allocation": allocation,
        "effective_sample_size": round(effective_sample_size(weights), 3) if weights else 0.0,
        "reason": reason,
    }


def _tune_neighbors(session: Any, seed: dict[str, Any], factors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    state = seed["state"]
    stories = {k: int(v) for k, v in state["stories"].items()}
    shapes = dict(state.get("shapes") or {})
    cap = session.constraints.get("max_building_length_ft")
    long_bar = bool(cap and any(length > float(cap) * 0.8 for length in seed.get("lengths") or []))
    out: list[dict[str, Any]] = []
    for factor in factors:
        if factor["kind"] == "stories":
            mid = factor["mass_id"]
            cur = stories.get(mid)
            if cur is None:
                continue
            for step in (-1, 1):
                nxt = int(cur) + step
                if nxt not in factor["levels"]:
                    continue
                changed = dict(stories)
                changed[mid] = nxt
                out.append(_from_state(state, changed, shapes, f"tune {factor.get('name') or mid} to {nxt} stories"))
        elif factor["kind"] == "shape":
            mid = factor["mass_id"]
            current = _value_to_shape_token(shapes.get(mid))
            for nxt in _adjacent_shapes(current):
                if nxt not in factor["levels"] or nxt == current:
                    continue
                changed = dict(shapes)
                changed[mid] = _shape_token_to_value(nxt)
                out.append(_from_state(state, stories, changed, f"tune {factor.get('name') or mid} toward {nxt}"))
        elif factor["kind"] == "loading" and long_bar:
            mode = state.get("loading") or "double"
            other = "single" if mode == "double" else "double"
            out.append(_from_state(state, stories, shapes, f"tune loading to {other}", loading=other))
    return out


def _recovery_neighbors(session: Any, seed: dict[str, Any], factors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One legal step that might cross the cap. Not a preference polish of an illegal scheme."""
    state = seed["state"]
    stories = {k: int(v) for k, v in state["stories"].items()}
    shapes = dict(state.get("shapes") or {})
    cap = session.constraints.get("max_building_length_ft")
    out: list[dict[str, Any]] = []
    lengths = seed.get("lengths") or []
    for mid, length in zip(list(stories), lengths):
        factor = next((f for f in factors if f["kind"] == "stories" and f["mass_id"] == mid), None)
        if factor is None or not cap or length <= float(cap) + 1e-6:
            continue
        nxt = int(stories[mid]) + 1
        if nxt not in factor["levels"]:
            continue
        changed = dict(stories)
        changed[mid] = nxt
        out.append(_from_state(state, changed, shapes, f"raise {mid} one story to clear the length cap"))
    if any(f["kind"] == "ratio" for f in factors) and state.get("length_over_width"):
        out.append(_from_state(state, stories, shapes, "drop the shared ratio to clear the length cap", use_ratio=False))
    for factor in factors:
        if factor["kind"] != "shape":
            continue
        mid = factor["mass_id"]
        if "thin" in factor["levels"] and _value_to_shape_token(shapes.get(mid)) != "thin":
            changed = dict(shapes)
            changed[mid] = "thin"
            out.append(_from_state(state, stories, changed, f"narrow {mid} to clear the length cap"))
    return out


def _fully_enumerated(factors: list[dict[str, Any]], records: list[dict[str, Any]]) -> bool:
    if not factors:
        return True
    product = 1
    levels = []
    for factor in factors:
        opts = [_norm_level(level) for level in factor["levels"]]
        product *= max(1, len(opts))
        levels.append(opts)
    if product > 12:
        return False
    seen = {
        tuple(_norm_level((r.get("levels") or {}).get(f["id"])) for f in factors)
        for r in records
    }
    needed = set(itertools.product(*levels))
    return needed <= seen


def _ordered_neighbors(
    seed: dict[str, Any],
    neighbors: list[dict[str, Any]],
    taste: dict[str, float],
) -> list[dict[str, Any]]:
    """Spend allocated tries on the lever edit the weights already favor."""
    if not taste or not neighbors:
        return neighbors
    from .sample_space import local_move_priority

    state = seed.get("state") or {}
    stories = {k: int(v) for k, v in (state.get("stories") or {}).items()}
    shapes = dict(state.get("shapes") or {})
    ranked = sorted(
        enumerate(neighbors),
        key=lambda item: local_move_priority(stories, shapes, item[1], taste),
        reverse=True,
    )
    return [candidate for _, candidate in ranked]


def reweight_particles(session: Any, weights: dict[str, float]) -> dict[str, Any]:
    """
    After a comparison, reweight legal schemes already drawn.

    Does not open a new sample and does not invent a dimension. If the
    highest-weight scheme was stored, that drawing is restored.
    """
    loop = dict(session.constraints.get("try_loop") or {})
    schemes = [dict(item) for item in (loop.get("schemes") or []) if item.get("fits")]
    if len(schemes) < 1:
        return {"ok": False, "reason": "No legal scheme to reweight."}
    from .preference import utility

    learned = bool(weights and any(abs(float(v)) > 1e-9 for v in weights.values()))
    for scheme in schemes:
        traits = scheme.get("traits") or {}
        scheme["weight"] = round(utility(weights, traits), 4) if learned else stated_weight(
            {"mass_preference": 0.0}
        )
    schemes.sort(key=lambda item: item.get("weight") or 0.0, reverse=True)
    raw = [max(0.0, float(item.get("weight") or 0.0)) for item in schemes]
    top = schemes[0]
    restored = False
    if top.get("state") and getattr(session, "masses", None):
        _restore(session, top["state"])
        from .tools import solve_dimensions

        solve_dimensions(session)
        restored = True
        loop["kept"] = {
            "try": top.get("try"),
            "reason": top.get("reason"),
            "typology": top.get("typology"),
            "traits": top.get("traits"),
            "lengths": top.get("lengths"),
            "fits_limitations": True,
        }
    loop["schemes"] = schemes
    loop["particle_weights"] = [
        {"id": item.get("id"), "weight": item.get("weight"), "typology": item.get("typology")}
        for item in schemes
    ]
    loop["effective_sample_size"] = round(effective_sample_size(raw), 3)
    session.constraints["try_loop"] = loop
    return {
        "ok": True,
        "kept": top.get("id"),
        "restored": restored,
        "effective_sample_size": loop["effective_sample_size"],
        "weights": loop["particle_weights"],
    }


def _winner(
    records: list[dict[str, Any]],
    basins: list[dict[str, Any]],
    taste: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    taste = taste or {}
    learned = bool(taste and any(abs(float(v)) > 1e-9 for v in taste.values()))
    if basins:
        legal = [b["best"] for b in basins if b["best"]["fits_limitations"]]
        if legal:
            if learned:
                return max(legal, key=lambda r: (taste_weight(r, taste), -float(r.get("mass_preference") or 0.0)))
            return min(legal, key=lambda r: r["stage2"])
        return min((b["best"] for b in basins), key=lambda r: r["score"])
    if not records:
        return None
    legal = [r for r in records if r["fits_limitations"]]
    if legal:
        return min(legal, key=lambda r: r["stage2"])
    return min(records, key=lambda r: r["score"])


def _note(records, coverage, basins, stage3, fits, budget) -> str:
    if not records:
        return f"No scheme was solved (cap {budget}). The cap was not used as a length."
    if not fits:
        return (
            f"No scheme fit the limitations in {len(records)} tries (cap {budget}). "
            f"{coverage.get('note')} {stage3.get('reason')} "
            "Kept the closest. The cap was not used as a length."
        )
    tuned = " Stage three improved a kept basin." if stage3.get("improved") else f" {stage3.get('reason')}"
    return (
        f"The search tried {len(records)} schemes (cap {budget}). "
        f"{coverage.get('note')} "
        f"Stage two kept {len(basins)} basin(s) that fit the length cap.{tuned}"
    )


def _from_state(state, stories, shapes, reason, loading=None, use_ratio=None) -> dict[str, Any]:
    return {
        "stories": dict(stories),
        "shapes": dict(shapes),
        "ratios": dict(state.get("mass_ratios") or {}),
        "loading": loading if loading is not None else (state.get("loading") or "double"),
        "use_ratio": bool(state.get("length_over_width")) if use_ratio is None else bool(use_ratio),
        "reason": reason,
    }


def _adjacent_shapes(current: str) -> list[str]:
    return {
        "thin": ["bar", "box"],
        "bar": ["thin", "box"],
        "box": ["cube", "bar"],
        "cube": ["box", "bar"],
    }.get(current, ["bar"])


def _shape_token_to_value(token: str | None) -> str | None:
    if token in {None, "", "bar", "-"}:
        return None
    return str(token)


def _value_to_shape_token(value: str | None) -> str:
    return "bar" if value in {None, "", "bar", "-"} else str(value)


def _norm_level(value: Any) -> Any:
    if value in {None, "", "bar", "-"}:
        return "bar"
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    return value


def _read_levels(session: Any, factors: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for factor in factors:
        if factor["kind"] == "stories":
            mass = next(m for m in session.masses if m.id == factor["mass_id"])
            out[factor["id"]] = int(mass.story_count)
        elif factor["kind"] == "shape":
            out[factor["id"]] = _value_to_shape_token(
                session.constraints.get(f"{factor['mass_id']}_shape")
            )
        elif factor["kind"] == "loading":
            out[factor["id"]] = session.constraints.get("loading") or "double"
        elif factor["kind"] == "ratio":
            out[factor["id"]] = bool(session.constraints.get("length_over_width"))
    return out


def _basin_key(session: Any) -> str:
    loading = session.constraints.get("loading") or "double"
    parts = []
    for mass in session.masses:
        shape = session.constraints.get(f"{mass.id}_shape")
        family = "square" if shape in {"box", "cube"} else "bar"
        parts.append(f"{mass.id}:{family}:{','.join(mass.departments)}")
    return f"{loading}|{'|'.join(parts)}"


def _public_entry(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "try": record.get("try"),
        "stage": record.get("stage"),
        "reason": record.get("reason"),
        "fits_limitations": record.get("fits_limitations"),
        "limit_fails": record.get("limit_fails"),
        "preference_misses": record.get("preference_misses"),
        "mass_preference": record.get("mass_preference"),
        "lengths": record.get("lengths"),
        "levels": record.get("levels"),
        "basin": record.get("basin"),
        "typology": record.get("typology"),
        "traits": record.get("traits"),
    }


def _scheme_catalog(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feasible schemes only. Illegal drawings are not a preference sample."""
    catalog = []
    for record in records:
        if not record.get("fits_limitations"):
            continue
        catalog.append(
            {
                "id": f"try-{record['try']}",
                "try": record.get("try"),
                "fits": True,
                "typology": record.get("typology"),
                "traits": record.get("traits") or {},
                "lengths": record.get("lengths"),
                "reason": record.get("reason"),
                "state": record.get("state"),
            }
        )
    return catalog


def _fingerprint(session: Any) -> str:
    ratio = session.constraints.get("length_over_width")
    loading = session.constraints.get("loading") or ""
    parts = [
        f"{m.id}:{m.story_count}:{_shape_token(session, m.id)}:{','.join(m.departments)}"
        for m in session.masses
    ]
    return f"{loading}|{ratio}|{'|'.join(parts)}"


def _shape_token(session: Any, mass_id: str) -> str:
    shape = session.constraints.get(f"{mass_id}_shape") or "-"
    ratio = session.constraints.get(f"{mass_id}_length_over_width")
    return f"{shape}:{ratio if ratio else '-'}"


def _snapshot(session: Any) -> dict[str, Any]:
    return {
        "stories": {m.id: m.story_count for m in session.masses},
        "departments": {m.id: list(m.departments) for m in session.masses},
        "loading": session.constraints.get("loading"),
        "length_over_width": session.constraints.get("length_over_width"),
        "shapes": {
            m.id: session.constraints.get(f"{m.id}_shape") for m in session.masses
        },
        "mass_ratios": {
            m.id: session.constraints.get(f"{m.id}_length_over_width")
            for m in session.masses
        },
    }


def _restore(session: Any, state: dict[str, Any]) -> None:
    for mass in session.masses:
        if mass.id in state["stories"]:
            mass.story_count = int(state["stories"][mass.id])
        if mass.id in state["departments"]:
            mass.departments = list(state["departments"][mass.id])
    if state.get("loading"):
        session.constraints["loading"] = state["loading"]
    else:
        session.constraints.pop("loading", None)
    if state.get("length_over_width"):
        session.constraints["length_over_width"] = state["length_over_width"]
    else:
        session.constraints.pop("length_over_width", None)
    from .mass_prefs import clear_mass_shapes

    clear_mass_shapes(session)
    for mid, shape in (state.get("shapes") or {}).items():
        if shape:
            session.constraints[f"{mid}_shape"] = shape
    for mid, ratio in (state.get("mass_ratios") or {}).items():
        if ratio:
            session.constraints[f"{mid}_length_over_width"] = ratio


def _apply_candidate(session: Any, candidate: dict[str, Any]) -> None:
    from .mass_prefs import apply_mass_shape, clear_mass_shapes

    lock = _locked(session)
    cap = _max_stories(session)
    for mass in session.masses:
        if mass.id in lock:
            mass.story_count = lock[mass.id]
            continue
        count = (candidate.get("stories") or {}).get(mass.id, mass.story_count)
        mass.story_count = max(1, min(cap, int(count)))
    clear_mass_shapes(session)
    ratios = candidate.get("ratios") or {}
    for mid, shape in (candidate.get("shapes") or {}).items():
        apply_mass_shape(session, mid, shape, ratios.get(mid))
    if candidate.get("loading") in {"single", "double"}:
        session.constraints["loading"] = candidate["loading"]
    own_shape = any((candidate.get("shapes") or {}).values()) or any(
        (candidate.get("ratios") or {}).values()
    )
    if candidate.get("use_ratio") is False or own_shape:
        session.constraints.pop("length_over_width", None)
    elif session.constraints.get("stated_ratio"):
        session.constraints["length_over_width"] = session.constraints["stated_ratio"]


def _score(session: Any, result: Any, prefs: list[dict[str, Any]]) -> dict[str, Any]:
    from .mass_prefs import preference_distance

    limit_fails = 0
    pref_misses = 0
    lengths: list[float] = []
    for check in result.validation:
        if check.passed:
            continue
        if check.check.startswith("site_length") or check.check.startswith("site_width"):
            limit_fails += 1
        elif check.check.startswith("preferred_length"):
            pref_misses += 1
        elif "length" in check.check and "preferred" not in check.check:
            limit_fails += 1
    for mass in result.masses:
        if mass.floors:
            lengths.append(round(mass.floors[0].length_ft, 1))

    by_id = {m.id: m for m in result.masses}
    mass_pref = 0.0
    styled: set[str] = set()
    for pref in prefs:
        mid = pref.get("mass_id")
        mass = by_id.get(mid)
        if mass is None:
            mass_pref += 5.0
            continue
        styled.add(mid)
        mass_pref += preference_distance(pref, mass)
        if pref.get("stories") and len(mass.floors) != int(pref["stories"]):
            pref_misses += 1
        if pref.get("shape") == "thin" and mass.floors:
            aspect = mass.floors[0].length_ft / mass.floors[0].width_ft
            if aspect < 1.3:
                pref_misses += 1
        if pref.get("shape") in {"box", "cube"} and mass.floors:
            aspect = mass.floors[0].length_ft / mass.floors[0].width_ft
            if abs(aspect - float(pref.get("ratio") or 1.0)) > 0.15:
                pref_misses += 1

    ratio = session.constraints.get("stated_ratio")
    ratio_miss = 0.0
    if ratio:
        for mass in result.masses:
            own = next((p for p in prefs if p.get("mass_id") == mass.id), None)
            if own and (own.get("shape") or own.get("ratio")):
                continue
            if not mass.floors or mass.floors[0].width_ft <= 0:
                continue
            got = mass.floors[0].length_ft / mass.floors[0].width_ft
            ratio_miss += abs(got - float(ratio))
        if not session.constraints.get("length_over_width"):
            pref_misses += 1

    mass_pref = round(mass_pref, 3)
    rank = (limit_fails, mass_pref, pref_misses, round(ratio_miss, 3), sum(lengths))
    stage2 = (mass_pref, pref_misses, round(ratio_miss, 3), sum(lengths))
    return {
        "limit_fails": limit_fails,
        "pref_misses": pref_misses,
        "mass_pref": mass_pref,
        "lengths": lengths,
        "rank": rank,
        "stage2": stage2,
    }


def _locked(session: Any) -> dict[str, int]:
    raw = session.constraints.get("story_lock") or {}
    return {str(k): int(v) for k, v in raw.items()}


def _pinned_ids(session: Any) -> set[str]:
    pins = session.floor_pins or {}
    return {m.id for m in session.masses if any(d in pins for d in m.departments)}


def _max_stories(session: Any) -> int:
    return max(1, int(session.constraints.get("max_stories") or 4))


def _preferred_stories(session: Any, by_mass: dict[str, dict[str, Any]]) -> dict[str, int]:
    lock = _locked(session)
    pinned = _pinned_ids(session)
    cap = _max_stories(session)
    stories = {}
    for mass in session.masses:
        if mass.id in lock:
            stories[mass.id] = lock[mass.id]
        elif mass.id in pinned:
            stories[mass.id] = mass.story_count
        else:
            wanted = (by_mass.get(mass.id) or {}).get("stories")
            stories[mass.id] = max(1, min(cap, int(wanted or mass.story_count or 1)))
    return stories


def _story_options(session: Any, mass: Any, pref: dict[str, Any] | None) -> list[int]:
    lock = _locked(session)
    if mass.id in lock:
        return [lock[mass.id]]
    if mass.id in _pinned_ids(session):
        return [mass.story_count]
    cap = _max_stories(session)
    options = set(range(1, cap + 1))
    if pref and pref.get("stories"):
        options.add(max(1, min(cap, int(pref["stories"]))))
    return sorted(options)
