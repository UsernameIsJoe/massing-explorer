"""
Typed design actions. The LLM may propose them; the engine applies or rejects.

First wave maps onto tools that already exist. SPLIT_MASS is illegal when
mass count or named wings were required.
"""

from __future__ import annotations

from typing import Any

from ..group import match_department
from ..study_state import MassGrouping
from .strategy import grouping_is_required, required_mass_count, required_together

SUPPORTED = (
    "COLOCATE",
    "KEEP_APART",
    "SPLIT_MASS",
    "PIN_GROUND",
    "SET_STORIES",
    "SET_LOADING",
    "PAIR_MASSES",
    "CLEAR_PAIRINGS",
    "SET_WIDTH",
    "SET_ENVELOPE",
    "APPLY_PARTITION",
)

UNSUPPORTED = ("COURTYARD", "PODIUM", "PERPENDICULAR_WINGS", "SET_SHAPE")

CSP_OWNS_P = (
    "The constraint solver owns P when grouping is open. "
    "The planner does not invent a partition."
)


def apply_action(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    """Apply one action or explain why it was rejected. No invented feet."""
    name = str(action.get("op") or action.get("name") or "").upper()
    if name in UNSUPPORTED:
        return _reject(name, "The engine cannot realize this yet.")
    handler = {
        "COLOCATE": _colocate,
        "KEEP_APART": _keep_apart,
        "SPLIT_MASS": _split_mass,
        "PIN_GROUND": _pin_ground,
        "SET_STORIES": _set_stories,
        "SET_LOADING": _set_loading,
        "PAIR_MASSES": _pair_masses,
        "CLEAR_PAIRINGS": _clear_pairings,
        "SET_WIDTH": _set_width,
        "SET_ENVELOPE": _set_envelope,
        "APPLY_PARTITION": _apply_partition,
    }.get(name)
    if handler is None:
        return _reject(name or "UNKNOWN", "Not a known design action.")
    return handler(session, action)


def _reject(op: str, reason: str) -> dict[str, Any]:
    return {"ok": False, "op": op, "rejected": True, "reason": reason}


def _ok(op: str, note: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "op": op, "rejected": False, "reason": note, **extra}


def _resolve_depts(session: Any, names: list[Any]) -> tuple[list[str] | None, str]:
    known = session.department_names()
    out: list[str] = []
    for raw in names:
        hit = match_department(str(raw), known)
        if not hit:
            return None, f"Unknown department: {raw}"
        if hit not in out:
            out.append(hit)
    return out, ""


def _home(session: Any, department: str) -> Any:
    return next((m for m in session.masses if department in m.departments), None)


def _must_share(session: Any, a: str, b: str) -> bool:
    pair = frozenset({a, b})
    for group in required_together(session):
        if pair <= group or (a in group and b in group):
            return True
    return False


def _colocate(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    if not grouping_is_required(session):
        return _reject("COLOCATE", CSP_OWNS_P)
    depts, err = _resolve_depts(session, list(action.get("programs") or action.get("departments") or []))
    if err:
        return _reject("COLOCATE", err)
    if len(depts) < 2:
        return _reject("COLOCATE", "COLOCATE needs at least two programs.")
    host = _home(session, depts[0])
    if host is None:
        return _reject("COLOCATE", f"{depts[0]} is not in a mass.")
    for dept in depts[1:]:
        other = _home(session, dept)
        if other is None:
            return _reject("COLOCATE", f"{dept} is not in a mass.")
        if other.id == host.id:
            continue
        if grouping_is_required(session) and required_mass_count(session) == len(session.masses):
            return _reject(
                "COLOCATE",
                "Mass count is a requirement, so masses cannot be merged.",
            )
        other.departments = [d for d in other.departments if d != dept]
        if dept not in host.departments:
            host.departments.append(dept)
        if not other.departments:
            session.masses = [m for m in session.masses if m.id != other.id]
    if hasattr(session, "save"):
        session.save()
    return _ok("COLOCATE", f"{', '.join(depts)} share {host.name}.")


def _keep_apart(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    if not grouping_is_required(session):
        return _reject("KEEP_APART", CSP_OWNS_P)
    depts, err = _resolve_depts(session, list(action.get("programs") or action.get("departments") or []))
    if err:
        return _reject("KEEP_APART", err)
    if len(depts) < 2:
        return _reject("KEEP_APART", "KEEP_APART needs two programs.")
    a, b = depts[0], depts[1]
    if _must_share(session, a, b):
        return _reject("KEEP_APART", f"{a} and {b} are required to share a mass.")
    mass_a = _home(session, a)
    mass_b = _home(session, b)
    if mass_a is None or mass_b is None:
        return _reject("KEEP_APART", "Both programs must already belong to a mass.")
    if mass_a.id != mass_b.id:
        return _ok("KEEP_APART", "Already in different masses.")
    if grouping_is_required(session) and required_mass_count(session) == len(session.masses):
        return _reject(
            "KEEP_APART",
            "Mass count is a requirement, so a new mass cannot be opened.",
        )
    mover = b if len(mass_a.departments) > 1 else a
    mass_a.departments = [d for d in mass_a.departments if d != mover]
    new_id = _fresh_id(session, mover)
    session.masses.append(
        MassGrouping(id=new_id, name=new_id.replace("_", " ").title(), departments=[mover], story_count=mass_a.story_count)
    )
    if hasattr(session, "save"):
        session.save()
    return _ok("KEEP_APART", f"{mover} is now its own mass.")


def _split_mass(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    if not grouping_is_required(session):
        return _reject("SPLIT_MASS", CSP_OWNS_P)
    return _reject(
        "SPLIT_MASS",
        "The brief already required a grouping, so masses cannot be split.",
    )


def _pin_ground(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    from ..tools import pin_department_to_floor

    depts, err = _resolve_depts(session, list(action.get("programs") or action.get("departments") or []))
    if err:
        return _reject("PIN_GROUND", err)
    notes = []
    for dept in depts:
        out = pin_department_to_floor(session, dept, 0)
        if not out.get("ok"):
            return _reject("PIN_GROUND", str(out.get("error") or "pin failed"))
        notes.append(dept)
    return _ok("PIN_GROUND", f"Pinned to ground: {', '.join(notes)}.")


def _set_stories(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    from ..tools import set_story_count

    mass_id = str(action.get("mass") or action.get("mass_id") or "")
    count = int(action.get("stories") or action.get("story_count") or 0)
    lock = session.constraints.get("story_lock") or {}
    if mass_id in lock:
        return _reject("SET_STORIES", f"{mass_id} story count is locked by the brief.")
    cap = int(session.constraints.get("max_stories") or 4)
    if count < 1 or count > cap:
        return _reject("SET_STORIES", f"Stories must be between 1 and {cap}.")
    out = set_story_count(session, mass_id, count)
    if not out.get("ok"):
        return _reject("SET_STORIES", str(out.get("error") or "story change failed"))
    return _ok("SET_STORIES", f"{mass_id} is {count} stories.")


def _set_loading(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    mode = str(action.get("type") or action.get("loading") or "").lower()
    if mode not in {"single", "double"}:
        return _reject("SET_LOADING", "Loading must be single or double.")
    if session.constraints.get("loading_required") and session.constraints.get("loading") != mode:
        return _reject("SET_LOADING", "Loading was required by the brief.")
    session.constraints["loading"] = mode
    if hasattr(session, "save"):
        session.save()
    return _ok("SET_LOADING", f"Loading is {mode}.")


def _pair_masses(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    from ..tools import pair_masses
    from .topology import stated_frontage_ft, topology_is_required

    if topology_is_required(session):
        return _reject(
            "PAIR_MASSES",
            "Topology was required by the brief, so pairing cannot be changed.",
        )
    frontage = stated_frontage_ft(session)
    if frontage is None:
        return _reject(
            "PAIR_MASSES",
            "D was not stated. Pairing needs a stated site frontage, not an invented length.",
        )
    ids = [str(x) for x in (action.get("masses") or action.get("mass_ids") or [])]
    length = action.get("length_ft") or action.get("total_length_ft")
    if length is not None and abs(float(length) - frontage) > 0.51:
        return _reject(
            "PAIR_MASSES",
            "Invented a pairing length. A cap is a filter, not a length to draw.",
        )
    if len(ids) < 2:
        return _reject("PAIR_MASSES", "PAIR_MASSES needs two masses the engine can place.")
    out = pair_masses(session, ids, float(frontage), length_is_cap=True)
    if not out.get("ok"):
        return _reject("PAIR_MASSES", str(out.get("error") or "pair failed"))
    return _ok("PAIR_MASSES", f"Paired {', '.join(ids)} under {frontage:g} ft (cap).")


def _clear_pairings(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    from .topology import topology_is_required

    if topology_is_required(session):
        return _reject("CLEAR_PAIRINGS", "Topology was required by the brief.")
    if not session.pairings:
        return _ok("CLEAR_PAIRINGS", "Already independent bars.")
    session.pairings = []
    if hasattr(session, "save"):
        session.save()
    return _ok("CLEAR_PAIRINGS", "Masses size independently again.")


def _set_width(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    """Nearby width step from the current plate — or an explicit projected width."""
    mass_id = str(action.get("mass") or action.get("mass_id") or "")
    mass = next((m for m in (session.masses or []) if m.id == mass_id), None)
    if mass is None:
        return _reject("SET_WIDTH", f"Unknown mass: {mass_id}")
    from ..solver import required_width_ft

    if required_width_ft(session, mass) is not None:
        return _reject("SET_WIDTH", "Exact brief width is locked for this mass.")
    if any(mass_id in (p.mass_ids or []) for p in (session.pairings or [])):
        return _reject("SET_WIDTH", "Paired width is not independent.")
    current = session.constraints.get(f"{mass_id}_width_ft")
    if current is None:
        current = _width_from_last(session, mass_id) or 60.0
    try:
        current = float(current)
    except (TypeError, ValueError):
        current = 60.0
    target = action.get("width_ft")
    if target is not None:
        try:
            nxt = float(target)
        except (TypeError, ValueError):
            return _reject("SET_WIDTH", "Need a numeric width_ft.")
    else:
        try:
            delta = float(action.get("delta_ft") or 0)
        except (TypeError, ValueError):
            return _reject("SET_WIDTH", "Need a nearby width step (delta_ft).")
        if abs(delta) < 0.1:
            return _reject("SET_WIDTH", "Width step is empty.")
        nxt = current + delta
    min_w = 45.0
    min_edge = session.constraints.get("min_edge_ft")
    if min_edge is not None:
        min_w = max(min_w, float(min_edge))
    max_w = session.constraints.get("max_building_width_ft")
    if max_w is None:
        max_w = session.constraints.get("max_edge_ft")
    if nxt < min_w:
        return _reject("SET_WIDTH", f"Width would drop below {min_w:g} ft.")
    if max_w is not None and nxt > float(max_w) + 0.01:
        nxt = float(max_w)
        if abs(nxt - current) < 0.1:
            return _reject("SET_WIDTH", "Width would exceed the stated max.")
    session.constraints[f"{mass_id}_width_ft"] = round(nxt, 4)
    if hasattr(session, "save"):
        session.save()
    return _ok("SET_WIDTH", f"{mass_id} width {current:g} → {nxt:g} ft (nearby step).")


def _set_envelope(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    env = str(action.get("envelope") or action.get("type") or "").lower()
    if env not in {"balanced", "compact", "elongated"}:
        return _reject("SET_ENVELOPE", "Envelope must be balanced, compact, or elongated.")
    session.constraints["cover_envelope"] = env
    if hasattr(session, "save"):
        session.save()
    if getattr(session, "program", None) is not None:
        try:
            from .cover import _apply_envelope_geometry

            _apply_envelope_geometry(session, env)
        except Exception:
            pass
    return _ok("SET_ENVELOPE", f"Envelope family is {env}.")


def _apply_partition(session: Any, action: dict[str, Any]) -> dict[str, Any]:
    if grouping_is_required(session):
        return _reject("APPLY_PARTITION", "Program organization was required by the brief.")
    groups = list(action.get("groups") or [])
    if not groups:
        return _reject("APPLY_PARTITION", "APPLY_PARTITION needs CSP groups.")
    from .partitions import apply_partition

    apply_partition(session, groups)
    return _ok("APPLY_PARTITION", "Applied a CSP partition.")


def _width_from_last(session: Any, mass_id: str) -> float | None:
    last = getattr(session, "last_massing", None) or {}
    for mass in last.get("masses") or []:
        if str(mass.get("id") or "") != mass_id:
            continue
        floors = mass.get("floors") or []
        if floors:
            try:
                return float(floors[0].get("width_ft") or 0) or None
            except (TypeError, ValueError, AttributeError):
                return None
    return None


def _fresh_id(session: Any, seed: str) -> str:
    base = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(seed)).strip("_") or "mass"
    used = {m.id for m in session.masses}
    if base not in used:
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"
