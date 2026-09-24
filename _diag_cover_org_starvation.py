"""
Cheap probe: why seated motif diversity != legal org harvest.

No full COVER harvest. Builds plan + shortlist legality under (a) COVER's
shared story library vs (b) per-|P| school sweep, then simulates allocation
shares after first feasible.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from massing_explorer.brief import apply_brief
from massing_explorer.explore.cover import (
    COVER_START,
    apply_cover_sample,
    build_cover_plan,
    story_pattern_library,
)
from massing_explorer.explore.csp import _relationship_features, describe_csp
from massing_explorer.explore.partitions import apply_partition, cover_partition_budget
from massing_explorer.explore.p_pool import (
    DEEPEN_FRAC_WHEN_UNSATURATED,
    PROBE_FLOOR,
    allocate_cover_step,
    deepen_vs_expand_counts,
    partition_key,
)
from massing_explorer.explore.realize import realize
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession

ROOT = Path(__file__).resolve().parent
GSF = ROOT / "examples" / "Underwood_Elementary_Space_Summary_GSF_Tweaked.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"
BRIEF_34 = (
    "3-4 masses, max 3 floors. length max 60 meters. gym and dining together and "
    "double height. art and music prefer on ground floor. media prefer on top "
    "floor above admin. admin have to be on ground floor. core academic and "
    "special ed width has to be 80 feet. mass ratio have to be between 2:5 and "
    "5:8. prefer 3 floors."
)


def _flatten(item):
    from massing_explorer.explore.csp import _flatten

    return _flatten(item)


def _org_sig(groups):
    return " | ".join(
        sorted("+".join(sorted(str(d) for d in g["departments"])) for g in groups)
    )


def _school_patterns(n: int):
    out = [tuple([2] * n), tuple([1] * n)]
    for i in range(n):
        out.append(tuple(3 if j == i else 1 for j in range(n)))
        out.append(tuple(3 if j == i else 2 for j in range(n)))
    return out


def _try_realize(session, groups, pattern):
    apply_partition(
        session,
        [
            {
                "id": f"m{i}",
                "name": f"M{i}",
                "departments": list(g["departments"]),
                "story_count": int(s),
            }
            for i, (g, s) in enumerate(zip(groups, pattern))
        ],
    )
    for mass in session.masses:
        session.constraints.pop(f"{mass.id}_width_ft", None)
    (session.constraints.get("explore") or {}).pop("realize_cache", None)
    _r, perf = realize(session)
    return bool(perf.get("fits_limitations")), list(perf.get("failed_kinds") or [])[:4]


def main() -> None:
    import massing_explorer.session as session_mod

    tmp = tempfile.TemporaryDirectory()
    session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
    program = load_program_file(GSF, config_path=CONFIG)
    session = StudySession(
        study_id="diag_cover_starve", program=program, config_path=str(CONFIG)
    )
    session.constraints["cover_budget"] = {
        "start": 0,
        "step_small": 0,
        "step_large": 0,
        "max": 0,
    }
    session.constraints["explore_budget"] = {
        "mcts_sims": 0,
        "mcts_depth": 0,
        "mcts_roots": 0,
        "bo": 0,
        "refine": 0,
        "repair": 0,
    }
    session.save()
    apply_brief(session, BRIEF_34)

    stated_n = len(session.masses)
    cap = int(session.constraints.get("max_stories") or 4)
    cover_lib = story_pattern_library(
        stated_n, cap, locks=dict(session.constraints.get("story_lock") or {}),
        mass_ids=[m.id for m in session.masses],
    )

    report = describe_csp(session, cap=cover_partition_budget(session))
    chosen = list(report.get("chosen") or [])
    plan = build_cover_plan(session, pool_size=120)

    # Sample counts by partition index / mass count / motif
    by_pi = Counter(s.partition_index for s in plan.samples)
    start_by_pi = Counter(s.partition_index for s in plan.samples[:COVER_START])

    parts_meta = []
    for i, item in enumerate(plan.partitions):
        groups = item.get("groups") or []
        n = len([g for g in groups if g.get("departments")])
        feats = None
        try:
            atoms, assign = _flatten(item)
            feats = _relationship_features(atoms, assign)
        except Exception:
            feats = {}
        parts_meta.append(
            {
                "i": i,
                "n": n,
                "reason": str(item.get("reason") or "")[:80],
                "sig": _org_sig(groups)[:120],
                "motif": (
                    f"art={feats.get('art')}|media={feats.get('media')}|"
                    f"admin={feats.get('admin')}"
                    if feats
                    else ""
                ),
                "samples_all": by_pi.get(i, 0),
                "samples_start40": start_by_pi.get(i, 0),
            }
        )

    # Per shortlist org: legal under school sweep vs under COVER shared library
    # (with pad/truncate as apply_cover_sample does).
    org_yield = []
    for item in chosen:
        groups = item["groups"]
        n = len(groups)
        school_hit = None
        cover_hit = None
        school_fail = None
        cover_fail = None
        for pat in _school_patterns(n):
            ok, fails = _try_realize(session, groups, pat)
            if ok:
                school_hit = pat
                break
            school_fail = fails
        # Adapt COVER library patterns to this n
        adapted = []
        for pat in cover_lib:
            pl = list(pat)
            if len(pl) < n:
                mid = pl[len(pl) // 2] if pl else 2
                pl = pl + [mid] * (n - len(pl))
            elif len(pl) > n:
                pl = pl[:n]
            adapted.append(tuple(pl))
        # Dedup
        seen = set()
        adapted_u = []
        for p in adapted:
            if p not in seen:
                seen.add(p)
                adapted_u.append(p)
        for pat in adapted_u:
            ok, fails = _try_realize(session, groups, pat)
            if ok:
                cover_hit = pat
                break
            cover_fail = fails
        try:
            atoms, assign = _flatten(item)
            feats = _relationship_features(atoms, assign)
            motif = f"{feats.get('art')}/{feats.get('media')}/{feats.get('admin')}"
        except Exception:
            motif = "?"
        org_yield.append(
            {
                "n": n,
                "motif": motif,
                "sig": _org_sig(groups)[:100],
                "legal_school_sweep": school_hit is not None,
                "school_pat": school_hit,
                "legal_cover_lib": cover_hit is not None,
                "cover_pat": cover_hit,
                "school_fail_ex": school_fail,
                "cover_fail_ex": cover_fail,
                "cover_lib_adapted_n": len(adapted_u),
            }
        )

    # Allocation math after first feasible (unsaturated)
    deepen10, expand10 = deepen_vs_expand_counts(10, {"p_pool": {"entries": {
        "x": {"status": "feasible", "depth_saturated": False}
    }}})
    # Both discovery owed and deepen
    d, z, e = allocate_cover_step(10, {"p_pool": {"entries": {
        "x": {"status": "feasible", "depth_saturated": False}
    }}}, discovery_needed=20, deepen_needed=2)
    d2, z2, e2 = allocate_cover_step(10, {"p_pool": {"entries": {
        "x": {"status": "feasible", "depth_saturated": False}
    }}}, discovery_needed=0, deepen_needed=2)
    d3, z3, e3 = allocate_cover_step(20, {"p_pool": {"entries": {
        "x": {"status": "feasible", "depth_saturated": False}
    }}}, discovery_needed=12, deepen_needed=1)

    # Story library sizes for n=3 vs n=4
    lib3 = story_pattern_library(3, 3)
    lib4 = story_pattern_library(4, 3)

    out = {
        "stated_n": stated_n,
        "max_stories": cap,
        "cover_lib_len": len(cover_lib),
        "lib3_len": len(lib3),
        "lib4_len": len(lib4),
        "plan_partitions": len(plan.partitions),
        "plan_samples": len(plan.samples),
        "csp_feasible": report.get("feasible_count"),
        "parts_meta": parts_meta,
        "sample_mass_hist_start40": dict(
            Counter(
                parts_meta[s.partition_index]["n"]
                for s in plan.samples[:COVER_START]
                if s.partition_index < len(parts_meta)
            )
        ),
        "org_yield": org_yield,
        "legal_school": sum(1 for o in org_yield if o["legal_school_sweep"]),
        "legal_cover_lib": sum(1 for o in org_yield if o["legal_cover_lib"]),
        "legal_school_not_cover_lib": [
            o for o in org_yield if o["legal_school_sweep"] and not o["legal_cover_lib"]
        ],
        "alloc": {
            "DEEPEN_FRAC_WHEN_UNSATURATED": DEEPEN_FRAC_WHEN_UNSATURATED,
            "PROBE_FLOOR": PROBE_FLOOR,
            "step10_unsaturated_deepen_expand": [deepen10, expand10],
            "step10_both_discovery_deepen": [d, z, e],
            "step10_deepen_only": [d2, z2, e2],
            "step20_both": [d3, z3, e3],
        },
        "start40_per_P_min_max": [
            min(start_by_pi.values()) if start_by_pi else 0,
            max(start_by_pi.values()) if start_by_pi else 0,
        ],
        "P0_share_start40": start_by_pi.get(0, 0) / max(1, COVER_START),
    }
    path = ROOT / "studies" / "_diag_cover_org_starvation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in out if k not in ("parts_meta", "org_yield")}, indent=2))
    print("--- legal_school_not_cover_lib ---")
    print(json.dumps(out["legal_school_not_cover_lib"], indent=2))
    print("--- org_yield summary ---")
    for o in org_yield:
        print(
            f"n={o['n']} motif={o['motif']} school={o['legal_school_sweep']} "
            f"cover_lib={o['legal_cover_lib']} pat_s={o['school_pat']} pat_c={o['cover_pat']}"
        )
    print("--- start40 by P ---")
    for p in parts_meta:
        print(
            f"P{p['i']} n={p['n']} start40={p['samples_start40']} all={p['samples_all']} "
            f"{p['motif']}"
        )
    tmp.cleanup()


if __name__ == "__main__":
    main()
