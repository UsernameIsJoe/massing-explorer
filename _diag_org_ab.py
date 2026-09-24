"""
Phase 0: classify each COVER-plan org as A or B.

A — not legal under controlled school story sweep (CSP selection / unrealizable)
B — legal under school sweep, but missing from start-40 COVER sample pool
    realizations (COVER scheduling)

Writes studies/_diag_org_ab.json
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from massing_explorer.brief import apply_brief
from massing_explorer.explore.cover import (
    COVER_START,
    apply_cover_sample,
    build_cover_plan,
)
from massing_explorer.explore.csp import _relationship_features
from massing_explorer.explore.partitions import apply_partition
from massing_explorer.explore.realize import realize
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession
from massing_explorer.explore import archive as archive_mod

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


def _flatten(item: dict) -> tuple[list[dict], list[int]]:
    flat: list[dict] = []
    assign: list[int] = []
    for bi, g in enumerate(item.get("groups") or []):
        for d in g.get("departments") or []:
            flat.append({"departments": [d]})
            assign.append(bi)
    return flat, assign


def _org_sig(groups) -> str:
    return " | ".join(
        sorted("+".join(sorted(str(d) for d in g["departments"])) for g in groups)
    )


def _school_patterns(n: int) -> list[tuple[int, ...]]:
    out = [tuple([2] * n), tuple([1] * n)]
    for i in range(n):
        out.append(tuple(3 if j == i else 1 for j in range(n)))
        out.append(tuple(3 if j == i else 2 for j in range(n)))
    return out


def _try_realize(session, groups, pattern) -> bool:
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
    return bool(perf.get("fits_limitations"))


def main() -> None:
    import massing_explorer.session as session_mod

    tmp = tempfile.TemporaryDirectory()
    session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
    program = load_program_file(GSF, config_path=CONFIG)
    session = StudySession(
        study_id="diag_org_ab", program=program, config_path=str(CONFIG)
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

    plan = build_cover_plan(session, pool_size=120)
    by_pi = Counter(s.partition_index for s in plan.samples)
    start_by_pi = Counter(s.partition_index for s in plan.samples[:COVER_START])
    origin = archive_mod.capture(session)

    rows = []
    for i, item in enumerate(plan.partitions):
        groups = item.get("groups") or []
        n = len(groups)
        school_legal = False
        school_pat = None
        for pat in _school_patterns(n):
            if _try_realize(session, groups, pat):
                school_legal = True
                school_pat = pat
                break

        # Replay every start-40 sample aimed at this P
        cover_legal = False
        cover_pat = None
        cover_tries = 0
        for sample in plan.samples[:COVER_START]:
            if sample.partition_index != i:
                continue
            cover_tries += 1
            apply_cover_sample(session, plan, sample, origin=origin)
            for mass in session.masses:
                session.constraints.pop(f"{mass.id}_width_ft", None)
            (session.constraints.get("explore") or {}).pop("realize_cache", None)
            _r, perf = realize(session)
            if perf.get("fits_limitations"):
                cover_legal = True
                cover_pat = tuple(int(m.story_count) for m in session.masses)
                break

        if school_legal and not cover_legal:
            klass = "B"
        elif not school_legal:
            klass = "A"
        else:
            klass = "ok"

        try:
            feats = _relationship_features(*_flatten(item))
            motif = (
                f"{feats.get('art')}/{feats.get('media')}/{feats.get('admin')}"
            )
        except Exception:
            motif = "?"

        rows.append(
            {
                "i": i,
                "class": klass,
                "n": n,
                "motif": motif,
                "sig": _org_sig(groups),
                "school_legal": school_legal,
                "school_pat": school_pat,
                "cover_start_legal": cover_legal,
                "cover_pat": cover_pat,
                "samples_all": by_pi.get(i, 0),
                "samples_start40": start_by_pi.get(i, 0),
                "cover_start_tries": cover_tries,
            }
        )

    summary = {
        "plan_partitions": len(plan.partitions),
        "A_count": sum(1 for r in rows if r["class"] == "A"),
        "B_count": sum(1 for r in rows if r["class"] == "B"),
        "ok_count": sum(1 for r in rows if r["class"] == "ok"),
        "A_indices": [r["i"] for r in rows if r["class"] == "A"],
        "B_indices": [r["i"] for r in rows if r["class"] == "B"],
        "ok_indices": [r["i"] for r in rows if r["class"] == "ok"],
        "rows": rows,
        "phase1_gate": (
            "run_phase1_cover"
            if sum(1 for r in rows if r["class"] == "B") >= 2
            else "skip_or_csp"
        ),
    }
    path = ROOT / "studies" / "_diag_org_ab.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: summary[k] for k in summary if k != "rows"},
            indent=2,
        )
    )
    for r in rows:
        print(
            f"P{r['i']:02d} {r['class']:2} n={r['n']} "
            f"start40={r['samples_start40']} all={r['samples_all']} "
            f"motif={r['motif']}"
        )
    tmp.cleanup()


if __name__ == "__main__":
    main()
