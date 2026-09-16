"""
Program-perturbation robustness on one strategy.

Grow or shrink a department, re-solve the same P/T/V/G, record survive or
collapse. Dimensions may adapt via the normal solve path; the strategy
(grouping / stories) stays fixed. Untested candidates must be labeled untested.
"""

from __future__ import annotations

from typing import Any

from ..solver import solve_massing_study
from .performance import measure
from .strategy import partition_id

GROW = 1.15
SHRINK = 0.85
HARD_KINDS = {"site_length", "site_width", "site_total_length", "site_total_width", "gsf_fit", "anchor_fit", "layout_dims"}
DEFAULT_FACTORS = (SHRINK, GROW)


def shared_probe_departments(session: Any, cap: int = 3) -> list[str]:
    """Same department list for every shortlist probe in a run."""
    return _pick_departments(session, cap=cap)


def probe_strategy(
    session: Any,
    factors: tuple[float, float] = DEFAULT_FACTORS,
    departments: list[str] | None = None,
) -> dict[str, Any]:
    """Perturb program area on the current grouping. Restores GSF after each try."""
    origin_pid = partition_id(session)
    origin_masses = [(m.id, list(m.departments), int(m.story_count)) for m in session.masses]
    stored = _capture_gsf(session)
    baseline = solve_massing_study(session)
    base_perf = measure(baseline, session)
    base_hard = _hard_kinds(baseline)
    names = list(departments) if departments is not None else _pick_departments(session)
    probes: list[dict[str, Any]] = []
    survived = 0
    collapsed = 0
    try:
        for name in names:
            if name not in stored:
                continue
            for factor in factors:
                _scale_one(session, name, stored[name], factor)
                result = solve_massing_study(session)
                perf = measure(result, session)
                after_hard = _hard_kinds(result)
                new_hard = after_hard - base_hard
                same_p = partition_id(session) == origin_pid
                same_wings = [
                    (m.id, list(m.departments), int(m.story_count)) for m in session.masses
                ] == origin_masses
                still_fits = bool(perf.get("fits_limitations")) and "gsf_fit" not in after_hard
                collapse = (not same_p) or (not same_wings) or (not still_fits)
                if collapse:
                    collapsed += 1
                    outcome = "collapse"
                    why = _collapse_why(after_hard if not still_fits else new_hard, same_p, same_wings)
                    if not still_fits and not new_hard:
                        why = "The same strategy still misses a limitation after the area change."
                else:
                    survived += 1
                    outcome = "survive"
                    why = "Same strategy still fits after the area change."
                probes.append(
                    {
                        "department": name,
                        "factor": factor,
                        "delta_pct": round((factor - 1.0) * 100),
                        "outcome": outcome,
                        "why": why,
                        "fits_limitations": bool(perf.get("fits_limitations")),
                        "failed_kinds": list(perf.get("failed_kinds") or []),
                        "same_partition": same_p,
                    }
                )
                _restore_gsf(session, stored)
    finally:
        _restore_gsf(session, stored)

    total = survived + collapsed
    # Untested (no probes) is unknown — not proven robust.
    tested = total > 0
    score = (survived / total) if tested else None
    note = (
        f"Robustness: {survived} survive, {collapsed} collapse on the same strategy. "
        "Program area moved; grouping/stories fixed; dimensions may adapt via solve."
        if tested
        else "Robustness: untested (no probe departments)."
    )
    return {
        "ran": True,
        "tested": tested,
        "status": "probed" if tested else "untested",
        "baseline_fits": bool(base_perf.get("fits_limitations")),
        "survived": survived,
        "collapsed": collapsed,
        "score": round(score, 4) if score is not None else None,
        "probes": probes,
        "departments": names,
        "factors": list(factors),
        "dimensions_may_adapt": True,
        "strategy_fixed": True,
        "note": note,
    }


def attach_probe_to_entry(entry: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    """Write probe results onto the exact archive entry (not a sticky session field)."""
    from .performance import eval_composites

    perf = dict(entry.get("performance") or {})
    if probe.get("tested") and probe.get("score") is not None:
        perf["_robustness_probe"] = float(probe["score"])
        perf["robustness_status"] = "probed"
    else:
        perf.pop("_robustness_probe", None)
        perf["robustness_status"] = "untested"
    perf["robustness_scenarios"] = {
        "departments": list(probe.get("departments") or []),
        "factors": list(probe.get("factors") or []),
        "dimensions_may_adapt": bool(probe.get("dimensions_may_adapt", True)),
        "strategy_fixed": bool(probe.get("strategy_fixed", True)),
    }
    perf.update(eval_composites(perf))
    entry["performance"] = perf
    entry["robustness"] = {
        "status": perf["robustness_status"],
        "score": probe.get("score"),
        "survived": probe.get("survived"),
        "collapsed": probe.get("collapsed"),
        "departments": list(probe.get("departments") or []),
    }
    return entry


def probe_shortlist(
    session: Any,
    entries: list[dict[str, Any]],
    *,
    departments: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Run the same program-change scenarios on each entry.

    Restores each entry before probing so results attach to that candidate only.
    """
    from . import archive as archive_mod

    if not entries:
        return []
    held = archive_mod.capture(session)
    names = list(departments) if departments is not None else shared_probe_departments(session)
    out: list[dict[str, Any]] = []
    try:
        for entry in entries:
            if not entry.get("snapshot"):
                continue
            archive_mod.restore_entry(session, entry)
            probe = probe_strategy(session, departments=names)
            attach_probe_to_entry(entry, probe)
            out.append(entry)
    finally:
        archive_mod.restore_snapshot(session, held)
    return out



def _pick_departments(session: Any, cap: int = 3) -> list[str]:
    depts = list(getattr(getattr(session, "program", None), "departments", None) or [])
    depts = sorted(depts, key=lambda d: float(d.target_gsf or 0), reverse=True)
    names: list[str] = []
    if depts:
        names.append(depts[0].name)
    for dept in depts:
        if dept.name in names:
            continue
        low = dept.name.lower()
        if any(tok in low for tok in ("health", "physical", "dining", "art")):
            names.append(dept.name)
            break
    if len(names) < 2 and len(depts) > 1:
        names.append(depts[1].name)
    if len(names) < 3 and len(depts) > 2:
        names.append(depts[2].name)
    return names[:cap]


def _capture_gsf(session: Any) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for dept in getattr(getattr(session, "program", None), "departments", None) or []:
        out[dept.name] = (float(dept.nfa_sf or 0), float(dept.target_gsf or 0))
    return out


def _restore_gsf(session: Any, stored: dict[str, tuple[float, float]]) -> None:
    for dept in getattr(getattr(session, "program", None), "departments", None) or []:
        if dept.name not in stored:
            continue
        nfa, gsf = stored[dept.name]
        dept.nfa_sf = nfa
        dept.target_gsf = gsf


def _scale_one(session: Any, name: str, origin: tuple[float, float], factor: float) -> None:
    for dept in session.program.departments:
        if dept.name != name:
            continue
        dept.nfa_sf = origin[0] * factor
        dept.target_gsf = origin[1] * factor
        return


def _hard_kinds(result: Any) -> set[str]:
    kinds: set[str] = set()
    for check in getattr(result, "validation", None) or []:
        if getattr(check, "passed", True):
            continue
        kind = str(getattr(check, "check", "")).split(":")[0]
        if kind in HARD_KINDS:
            kinds.add(kind)
    return kinds


def _collapse_why(new_hard: set[str], same_p: bool, same_wings: bool) -> str:
    if not same_p or not same_wings:
        return "Grouping changed. Robustness must not regroup a must; this probe is invalid."
    if "site_length" in new_hard or "site_width" in new_hard:
        return "The same strategy missed a site cap after the area change."
    if "gsf_fit" in new_hard:
        return "The same strategy missed GSF fit after the area change."
    if "anchor_fit" in new_hard:
        return "An anchor room no longer fits this plate after the area change."
    if "layout_dims" in new_hard:
        return "Leftover around a void no longer hosts the program after the area change."
    return "The same strategy picked up a new hard failure."
