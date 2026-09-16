"""
Fill exact feet for a frozen strategy.

Search may change P, T, loading, envelope, plate profile, and story counts.
This inner step tries a small adaptive set of widths / ratio projections, then
picks a legal drawing or the closest illegal one. It does not bump stories.

Stop when feasibility is structurally hopeless, repeated attempts show no
distance progress, or further legal quality gain is tiny. Exhausting the
budget is not proof of impossibility.
"""

from __future__ import annotations

from typing import Any

from ..config import load_project_config
from ..solver import (
    _mass_plate_area,
    functional_bar_width,
    required_width_ft,
    solve_massing_study,
)
from .feasibility import feasibility_distance_of
from .performance import measure
from .strategy import partition_id

SOLVE_CAP_MIN = 3
SOLVE_CAP_BASE = 6
SOLVE_CAP_NEAR = 10
SOLVE_CAP_MAX = 14
WIDTH_MARGIN_FT = 0.25
MIN_WIDTH_FT = 45.0
# Back-compat alias used by older call sites / tests.
SOLVE_CAP = SOLVE_CAP_MAX
_NO_PROGRESS_STREAK = 2
_LEGAL_IMPROVE_EPS = 1e-4
_CACHE_MAX = 64


def realize(
    session: Any,
    *,
    weights: dict[str, float] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """
    Mutate session widths, solve, return (result, performance).

    Stories, partition, and topology stay as they arrived. Among legal `d`,
    maximize architectural reward under optional LEARN weights (envelope is a
    preference among those). Otherwise minimize feasibility distance.
    """
    if not getattr(session, "masses", None):
        result = solve_massing_study(session)
        return result, measure(result, session)

    if weights is None:
        explore = (getattr(session, "constraints", None) or {}).get("explore") or {}
        learning = explore.get("learning") or {}
        raw = learning.get("weights") or {}
        if isinstance(raw, dict) and any(abs(float(v or 0)) > 1e-9 for v in raw.values()):
            weights = {str(k): float(v) for k, v in raw.items()}

    held_stories = {m.id: int(m.story_count) for m in session.masses}
    held_partition = partition_id(session)
    held_widths = {
        m.id: session.constraints.get(f"{m.id}_width_ft") for m in session.masses
    }
    config = _config_of(session)

    cache_key = _realize_cache_key(session, held_stories)
    cached = _cache_get(session, cache_key)
    if cached is not None:
        combo, perf_hint = cached
        _restore_widths(session, held_widths)
        _apply_widths(session, combo)
        _restore_stories(session, held_stories)
        result = solve_massing_study(session)
        _restore_stories(session, held_stories)
        return result, measure(result, session)

    combos = _width_combos(session, config)
    best_legal: tuple[float, float, dict[str, float | None], Any, dict[str, Any]] | None = None
    best_illegal: tuple[float, dict[str, float | None], Any, dict[str, Any]] | None = None

    cap = _adaptive_solve_cap(session, config, len(combos))
    idx = 0
    no_progress = 0
    last_best_dist: float | None = None
    last_best_reward: float | None = None

    while idx < len(combos) and idx < cap:
        combo = combos[idx]
        idx += 1
        _restore_widths(session, held_widths)
        _apply_widths(session, combo)
        _restore_stories(session, held_stories)
        result = solve_massing_study(session)
        _restore_stories(session, held_stories)
        if partition_id(session) != held_partition:
            continue
        perf = measure(result, session)
        extra = _followup_combos(session, config, result, perf, combo)
        for follow in extra:
            if follow not in combos and len(combos) < SOLVE_CAP_MAX:
                combos.append(follow)
        # Raise cap when distance is shrinking or a near-feasible / promising legal appears.
        if not perf.get("fits_limitations"):
            dist = _distance(perf)
            if best_illegal is None or dist < best_illegal[0] - 1e-9:
                best_illegal = (dist, dict(combo), result, perf)
                if last_best_dist is None or dist < last_best_dist - 1e-6:
                    no_progress = 0
                    last_best_dist = dist
                    if dist < 0.35 and cap < SOLVE_CAP_NEAR:
                        cap = min(SOLVE_CAP_NEAR, SOLVE_CAP_MAX, len(combos))
                    elif dist < 0.15 and cap < SOLVE_CAP_MAX:
                        cap = min(SOLVE_CAP_MAX, len(combos))
                else:
                    no_progress += 1
            else:
                no_progress += 1
            if _structurally_hopeless(perf) and idx >= SOLVE_CAP_MIN:
                break
            if no_progress >= _NO_PROGRESS_STREAK and idx >= SOLVE_CAP_MIN:
                break
        else:
            from .saturate import architectural_reward

            reward = float(architectural_reward(perf, weights))
            env = _envelope_fit(session, result)
            env_label = str((session.constraints or {}).get("cover_envelope") or "balanced")
            if env_label in {"compact", "elongated"}:
                key = (env, reward)
            else:
                key = (reward, env)
            improved = best_legal is None or key > (best_legal[0], best_legal[1])
            if improved:
                best_legal = (key[0], key[1], dict(combo), result, perf)
                if last_best_reward is None or reward > last_best_reward + _LEGAL_IMPROVE_EPS:
                    no_progress = 0
                    last_best_reward = reward
                    if cap < SOLVE_CAP_NEAR:
                        cap = min(SOLVE_CAP_NEAR, len(combos))
                else:
                    no_progress += 1
            else:
                no_progress += 1
            if no_progress >= _NO_PROGRESS_STREAK and idx >= SOLVE_CAP_BASE:
                break

    picked = best_legal[2:] if best_legal is not None else (
        best_illegal[1:] if best_illegal is not None else None
    )
    if picked is None:
        _restore_widths(session, held_widths)
        result = solve_massing_study(session)
        _restore_stories(session, held_stories)
        return result, measure(result, session)

    combo, _result, _perf = picked
    _restore_widths(session, held_widths)
    _apply_widths(session, combo)
    _restore_stories(session, held_stories)
    result = solve_massing_study(session)
    _restore_stories(session, held_stories)
    perf = measure(result, session)
    _cache_put(session, cache_key, dict(combo), perf)
    return result, perf


def _adaptive_solve_cap(session: Any, config: dict[str, Any], n_combos: int) -> int:
    """Start small; callers may raise toward NEAR/MAX when progress appears."""
    n = max(1, int(n_combos))
    # Many free widths → start a bit higher; required-only → fewer needed.
    free = 0
    for mass in session.masses or []:
        if required_width_ft(session, mass) is None and mass.id not in _paired_ids(session):
            free += 1
    base = SOLVE_CAP_BASE if free >= 2 else SOLVE_CAP_MIN
    return max(SOLVE_CAP_MIN, min(base, n, SOLVE_CAP_MAX))


def _structurally_hopeless(perf: dict[str, Any]) -> bool:
    """True when violations look like hard structural conflict, not a width miss."""
    kinds = {str(k) for k in (perf.get("failed_kinds") or [])}
    if "program_split" in kinds and "volume_collision" in kinds:
        return True
    violations = perf.get("violations") or {}
    if not isinstance(violations, dict):
        return False
    try:
        split = float(violations.get("split") or 0)
        stories = float(violations.get("stories") or 0)
    except (TypeError, ValueError):
        return False
    return split >= 0.9 and stories >= 0.9


def _realize_cache_key(session: Any, stories: dict[str, int]) -> tuple:
    from .strategy import read_strategy

    strat = read_strategy(session)
    env = str((session.constraints or {}).get("cover_envelope") or "")
    loading = str((session.constraints or {}).get("loading") or "")
    topo = "paired" if session.pairings else "independent"
    plate = str((session.constraints or {}).get("cover_plate_profile") or "uniform")
    story_t = tuple(sorted((str(k), int(v)) for k, v in stories.items()))
    part = str(partition_id(session))
    return (part, story_t, topo, loading, env, plate)


def _cache_get(
    session: Any, key: tuple
) -> tuple[dict[str, float | None], dict[str, Any]] | None:
    explore = (session.constraints or {}).setdefault("explore", {})
    cache = explore.get("realize_cache")
    if not isinstance(cache, dict):
        return None
    hit = cache.get(repr(key))
    if not isinstance(hit, dict):
        return None
    combo = hit.get("combo")
    if not isinstance(combo, dict):
        return None
    return combo, hit.get("perf") or {}


def _cache_put(
    session: Any,
    key: tuple,
    combo: dict[str, float | None],
    perf: dict[str, Any],
) -> None:
    explore = (session.constraints or {}).setdefault("explore", {})
    cache = explore.setdefault("realize_cache", {})
    if not isinstance(cache, dict):
        explore["realize_cache"] = {}
        cache = explore["realize_cache"]
    cache[repr(key)] = {
        "combo": {str(k): (None if v is None else float(v)) for k, v in combo.items()},
        "perf": {
            "fits_limitations": bool(perf.get("fits_limitations")),
            "feasibility_distance": perf.get("feasibility_distance"),
        },
    }
    # Bound memory
    while len(cache) > _CACHE_MAX:
        cache.pop(next(iter(cache)))


def _config_of(session: Any) -> dict[str, Any]:
    path = getattr(session, "config_path", None)
    try:
        return load_project_config(path)
    except Exception:
        return {}


def _restore_stories(session: Any, held: dict[str, int]) -> None:
    for mass in session.masses or []:
        if mass.id in held:
            mass.story_count = held[mass.id]


def _restore_widths(session: Any, held: dict[str, Any]) -> None:
    for mass in session.masses or []:
        key = f"{mass.id}_width_ft"
        width = held.get(mass.id)
        if width is None:
            session.constraints.pop(key, None)
        else:
            session.constraints[key] = width


def _apply_widths(session: Any, combo: dict[str, float | None]) -> None:
    for mass in session.masses or []:
        key = f"{mass.id}_width_ft"
        if mass.id not in combo:
            continue
        width = combo[mass.id]
        if width is None:
            session.constraints.pop(key, None)
        else:
            session.constraints[key] = round(float(width), 4)


def _paired_ids(session: Any) -> set[str]:
    ids: set[str] = set()
    for pairing in session.pairings or []:
        ids.update(str(mid) for mid in (pairing.mass_ids or []))
    return ids


def _length_cap(session: Any, mass: Any) -> float | None:
    caps: list[float] = []
    c = session.constraints or {}
    for key in (
        f"{mass.id}_max_edge_ft",
        "max_edge_ft",
        "max_building_length_ft",
    ):
        raw = c.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
            caps.append(float(raw))
    dept_caps = c.get("department_max_edge_ft") or {}
    if isinstance(dept_caps, dict):
        for dept in mass.departments or []:
            raw = dept_caps.get(dept)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
                caps.append(float(raw))
    return min(caps) if caps else None


def _clamp_width(session: Any, mass: Any, width: float, *, plate: float = 0.0) -> float:
    min_w = float((session.constraints or {}).get("min_edge_ft") or MIN_WIDTH_FT)
    max_w = (session.constraints or {}).get("max_building_width_ft")
    cap = _length_cap(session, mass)
    w = max(min_w, float(width))
    if isinstance(max_w, (int, float)) and not isinstance(max_w, bool) and max_w > 0:
        w = min(w, float(max_w))
    if cap and plate > 0:
        # Every edge ≤ cap ⇒ width ≤ cap and width ≥ plate/cap.
        w = min(w, float(cap))
        need = float(plate) / float(cap) + WIDTH_MARGIN_FT
        if need <= float(cap):
            w = max(w, need)
    elif cap:
        w = min(w, float(cap))
    return round(w, 4)


def _plate_of(session: Any, mass: Any, config: dict[str, Any]) -> float:
    try:
        return float(_mass_plate_area(session, mass, config) or 0.0)
    except Exception:
        return 0.0


def _width_choices(session: Any, mass: Any, config: dict[str, Any]) -> list[float]:
    required = required_width_ft(session, mass)
    if required is not None:
        return [_clamp_width(session, mass, float(required))]
    if mass.id in _paired_ids(session):
        return []

    plate = _plate_of(session, mass, config)
    raw: list[float] = []
    raw.extend(_envelope_widths(session, plate))
    try:
        raw.append(float(functional_bar_width(session, mass, config)))
    except Exception:
        pass
    stated = (session.constraints or {}).get(f"{mass.id}_width_ft")
    if isinstance(stated, (int, float)) and not isinstance(stated, bool) and stated > 0:
        raw.append(float(stated))
    cap = _length_cap(session, mass)
    if plate > 0 and cap:
        raw.append(plate / float(cap) + WIDTH_MARGIN_FT)
    max_w = (session.constraints or {}).get("max_building_width_ft")
    if isinstance(max_w, (int, float)) and not isinstance(max_w, bool) and max_w > 0:
        raw.append(float(max_w))

    out: list[float] = []
    seen: set[float] = set()
    for width in raw:
        w = _clamp_width(session, mass, width, plate=plate)
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _envelope_widths(session: Any, plate: float) -> list[float]:
    """Generate widths for the requested envelope, not only a later tie-break."""
    if plate <= 0:
        return []
    env = str((session.constraints or {}).get("cover_envelope") or "balanced")
    side = plate ** 0.5
    min_w = float((session.constraints or {}).get("min_edge_ft") or MIN_WIDTH_FT)
    if env == "compact":
        return [side, side * 1.2, max(min_w, side * 0.85)]
    if env == "elongated":
        return [max(min_w, side * 0.42), max(min_w, side * 0.55), max(min_w, side * 0.7)]
    return [max(min_w, side * 0.65), side, side * 1.15]


def _total_length_combo(
    session: Any, config: dict[str, Any]
) -> dict[str, float | None] | None:
    """Widen free masses so sum(plate/width) sits under max_total_length_ft."""
    raw = (session.constraints or {}).get("max_total_length_ft")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw <= 0:
        return None
    cap = float(raw) * 0.98
    paired = _paired_ids(session)
    plates: dict[str, float] = {}
    locked: dict[str, float] = {}
    free: list[str] = []
    by_id = {m.id: m for m in session.masses or []}
    for mass in session.masses or []:
        plate = _plate_of(session, mass, config)
        plates[mass.id] = plate
        req = required_width_ft(session, mass)
        if req is not None:
            locked[mass.id] = float(req)
        elif mass.id in paired:
            stated = (session.constraints or {}).get(f"{mass.id}_width_ft")
            if isinstance(stated, (int, float)) and not isinstance(stated, bool) and stated > 0:
                locked[mass.id] = float(stated)
            else:
                try:
                    locked[mass.id] = float(functional_bar_width(session, mass, config))
                except Exception:
                    locked[mass.id] = MIN_WIDTH_FT
        else:
            free.append(mass.id)
    used = 0.0
    for mid, width in locked.items():
        plate = plates.get(mid) or 0.0
        if width > 0 and plate > 0:
            used += plate / width
    remain = cap - used
    if remain <= 0 or not free:
        return None
    free_plate = sum(plates.get(mid) or 0.0 for mid in free)
    if free_plate <= 0:
        return None
    combo: dict[str, float | None] = {mid: locked[mid] for mid in locked}
    for mid in free:
        plate = plates.get(mid) or 0.0
        share = remain * (plate / free_plate) if free_plate else remain / len(free)
        if share <= 0:
            continue
        width = plate / share + WIDTH_MARGIN_FT
        combo[mid] = _clamp_width(session, by_id[mid], width, plate=plate)
    return combo or None


def _width_combos(session: Any, config: dict[str, Any]) -> list[dict[str, float | None]]:
    per = {m.id: _width_choices(session, m, config) for m in session.masses}
    free = [m.id for m in session.masses if per.get(m.id)]
    plates = {m.id: _plate_of(session, m, config) for m in session.masses}
    combos: list[dict[str, float | None]] = []

    def push(combo: dict[str, float | None]) -> None:
        if combo not in combos:
            combos.append(combo)

    push({})
    total = _total_length_combo(session, config)
    if total:
        push(total)
    if not free:
        return combos
    push({mid: per[mid][0] for mid in free})
    push({mid: per[mid][-1] for mid in free})
    # Prefer widest available when a length budget is tight.
    push(
        {
            mid: _clamp_width(session, next(m for m in session.masses if m.id == mid), per[mid][-1], plate=plates.get(mid) or 0.0)
            for mid in free
        }
    )
    for mid in free:
        for width in per[mid][1:]:
            combo = {other: per[other][0] for other in free}
            combo[mid] = width
            push(combo)
            if len(combos) >= SOLVE_CAP:
                return combos
    return combos


def _followup_combos(
    session: Any,
    config: dict[str, Any],
    result: Any,
    perf: dict[str, Any],
    base: dict[str, float | None],
) -> list[dict[str, float | None]]:
    extra: list[dict[str, float | None]] = []
    paired = _paired_ids(session)
    for sug in getattr(result, "resize_suggestions", None) or []:
        mid = str(getattr(sug, "mass_id", "") or "")
        width = getattr(sug, "option_width_ft", None)
        if not mid or width is None or mid in paired:
            continue
        mass = next((m for m in session.masses if m.id == mid), None)
        if mass is None or required_width_ft(session, mass) is not None:
            continue
        plate = _plate_of(session, mass, config)
        combo = dict(base)
        combo[mid] = _clamp_width(session, mass, float(width) + WIDTH_MARGIN_FT, plate=plate)
        extra.append(combo)

    kinds = {str(k).split(":")[0] for k in (perf.get("failed_kinds") or [])}
    if "ratio_band" in kinds:
        ratio = _ratio_combo(session, result, base, paired)
        if ratio:
            extra.append(ratio)
    if kinds & {"site_total_length", "site_length", "site_width"}:
        total = _total_length_combo(session, config)
        if total:
            extra.append(total)
        # Nudge every free mass wider once.
        wider = dict(base)
        changed = False
        for mass in session.masses or []:
            if mass.id in paired or required_width_ft(session, mass) is not None:
                continue
            cur = wider.get(mass.id)
            if cur is None:
                cur = (session.constraints or {}).get(f"{mass.id}_width_ft")
            try:
                cur_f = float(cur) if cur is not None else MIN_WIDTH_FT
            except (TypeError, ValueError):
                cur_f = MIN_WIDTH_FT
            plate = _plate_of(session, mass, config)
            nxt = _clamp_width(session, mass, cur_f * 1.25, plate=plate)
            if abs(nxt - cur_f) > 0.5:
                wider[mass.id] = nxt
                changed = True
        if changed:
            extra.append(wider)
    return extra


def _ratio_combo(
    session: Any,
    result: Any,
    base: dict[str, float | None],
    paired: set[str],
) -> dict[str, float | None] | None:
    role = str((session.constraints or {}).get("ratio_band_role") or "limitation")
    if role == "preference":
        return None
    band = (session.constraints or {}).get("ratio_band")
    if not isinstance(band, (list, tuple)) or len(band) < 2:
        return None
    try:
        lo, hi = float(band[0]), float(band[1])
    except (TypeError, ValueError):
        return None
    solved = {m.id: m for m in (getattr(result, "masses", None) or [])}
    combo = dict(base)
    changed = False
    for mass in session.masses or []:
        if mass.id in paired or required_width_ft(session, mass) is not None:
            continue
        solved_mass = solved.get(mass.id)
        floors = list(getattr(solved_mass, "floors", None) or [])
        if not floors:
            continue
        width = float(floors[0].width_ft or 0)
        length = float(floors[0].length_ft or 0)
        target = _ratio_target_width(width, length, lo, hi)
        if target is None or abs(target - width) < 0.5:
            continue
        combo[mass.id] = _clamp_width(session, mass, target)
        changed = True
    return combo if changed else None


def _ratio_target_width(width: float, length: float, lo: float, hi: float) -> float | None:
    from ..aspect import aspect_in_band, normalize_band

    if width <= 0 or length <= 0:
        return None
    area = width * length
    lo, hi = normalize_band(lo, hi)
    tol = 0.03 * max(hi - lo, 0.15)
    best = None
    best_d = 1e9
    for asp in (lo, hi, 1.0 / lo if lo else 0.0, 1.0 / hi if hi else 0.0):
        if asp <= 0:
            continue
        w = (area / asp) ** 0.5
        new_aspect = (area / w) / w if w else 0.0
        if not aspect_in_band(new_aspect, lo, hi, tol=tol):
            continue
        d = abs(w - width)
        if d < best_d:
            best_d = d
            best = w
    return best


def _envelope_fit(session: Any, result: Any) -> float:
    env = str((session.constraints or {}).get("cover_envelope") or "balanced")
    aspects: list[float] = []
    for mass in getattr(result, "masses", None) or []:
        floors = list(getattr(mass, "floors", None) or [])
        if not floors:
            continue
        width = float(getattr(floors[0], "width_ft", 0) or 0)
        length = float(getattr(floors[0], "length_ft", 0) or 0)
        if width <= 0:
            continue
        asp = length / width
        aspects.append(max(asp, 1.0 / asp if asp else 1.0))
    if not aspects:
        return 0.5
    asp = sum(aspects) / len(aspects)
    if env == "compact":
        return max(0.0, min(1.0, 2.0 - asp))
    if env == "elongated":
        return max(0.0, min(1.0, (asp - 1.0) / 3.0))
    return 0.5


def _distance(perf: dict[str, Any]) -> float:
    raw = perf.get("feasibility_distance")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return feasibility_distance_of({"performance": perf, "fits_limitations": False})
