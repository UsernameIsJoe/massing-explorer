"""
Step 1: separate realization failure from search allocation failure.

1) Run the controlled 3-mass / 3-4-mass briefs (optional harvest).
2) For each legal archive cell (or a JSON dump of them):
   a) restore + solve with saved widths  → must stay legal
   b) strip per-mass *_width_ft (keep department_widths) + realize()
      → can current realize rediscover legal feet?

Writes studies/_diag_realize_step1.json
"""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from massing_explorer.brief import apply_brief
from massing_explorer.explore.archive import restore_entry
from massing_explorer.explore.realize import realize
from massing_explorer.explore.strategy import idea_key, partition_id
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession
from massing_explorer.solver import solve_massing_study

ROOT = Path(__file__).resolve().parent
GSF = ROOT / "examples" / "Underwood_Elementary_Space_Summary_GSF_Tweaked.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"

BRIEF_3 = (
    "3 masses, max 3 floors. length max 60 meters. gym and dining together and "
    "double height. art and music prefer on ground floor. media prefer on top "
    "floor above admin. admin have to be on ground floor. core academic and "
    "special ed width has to be 80 feet. mass ratio have to be between 2:5 and "
    "5:8. prefer 3 floors."
)
BRIEF_34 = (
    "3-4 masses, max 3 floors. length max 60 meters. gym and dining together and "
    "double height. art and music prefer on ground floor. media prefer on top "
    "floor above admin. admin have to be on ground floor. core academic and "
    "special ed width has to be 80 feet. mass ratio have to be between 2:5 and "
    "5:8. prefer 3 floors."
)

COVER_BUDGET = {
    "start": 40,
    "step_small": 10,
    "step_large": 20,
    "max": 100,
}
EXPLORE_BUDGET = {
    "mcts_sims": 64,
    "mcts_depth": 4,
    "mcts_roots": 4,
    "bo": 12,
    "refine": 14,
    "repair": 14,
}


def _mass_n(entry: dict[str, Any]) -> int:
    stories = entry.get("stories") or {}
    if stories:
        return len(stories)
    plates = entry.get("plates") or []
    if plates:
        return len(plates)
    snap = entry.get("snapshot") or {}
    return len(snap.get("masses") or [])


def _grouping_sig(entry: dict[str, Any]) -> str:
    snap = entry.get("snapshot") or {}
    masses = snap.get("masses") or []
    parts = []
    for m in masses:
        depts = tuple(sorted(str(d) for d in (m.get("departments") or [])))
        parts.append(depts)
    parts.sort()
    return " | ".join("+".join(p) for p in parts)


def _strip_realized_widths(session: StudySession) -> list[str]:
    cleared: list[str] = []
    for mass in session.masses:
        key = f"{mass.id}_width_ft"
        if key in session.constraints:
            session.constraints.pop(key, None)
            cleared.append(key)
    explore = session.constraints.setdefault("explore", {})
    explore.pop("realize_cache", None)
    return cleared


def _run_brief(label: str, brief: str, studies_dir: Path) -> dict[str, Any]:
    program = load_program_file(GSF, config_path=CONFIG)
    session = StudySession(
        study_id=f"diag_{label}",
        program=program,
        config_path=str(CONFIG),
    )
    session.constraints["cover_budget"] = dict(COVER_BUDGET)
    session.constraints["explore_budget"] = dict(EXPLORE_BUDGET)
    session.save()
    out = apply_brief(session, brief)
    explore = session.constraints.get("explore") or {}
    archive = explore.get("archive") or {}
    cells = list((archive.get("cells") or {}).values())
    legal = [c for c in cells if c.get("fits_limitations")]
    by_n = Counter(_mass_n(c) for c in legal)
    groups = Counter(_grouping_sig(c) for c in legal)
    pool = (archive.get("p_pool") or {}).get("entries") or {}
    # Persist archive for realize replay.
    dump_path = studies_dir / f"_diag_archive_{label}.json"
    dump_path.write_text(
        json.dumps(
            {
                "label": label,
                "brief": brief,
                "ok": out.get("ok"),
                "attempts": archive.get("attempts"),
                "cells": len(cells),
                "legal": len(legal),
                "legal_by_mass_n": dict(by_n),
                "legal_groupings": dict(groups),
                "p_pool_size": len(pool),
                "p_pool_status": dict(Counter(str(e.get("status")) for e in pool.values())),
                "archive_cells": {str(c.get("cell") or i): c for i, c in enumerate(legal)},
                "constraints": {
                    "mass_count": (session.constraints.get("p_constraints") or {}).get(
                        "mass_count"
                    ),
                    "max_edge_ft": session.constraints.get("max_edge_ft")
                    or session.constraints.get("max_building_length_ft"),
                    "department_widths": session.constraints.get("department_widths"),
                    "ratio_band": session.constraints.get("ratio_band"),
                },
                "base_constraints": {
                    k: v
                    for k, v in session.constraints.items()
                    if k
                    not in {
                        "explore",
                        "explore_budget",
                        "cover_budget",
                    }
                    and not (
                        str(k).endswith("_width_ft")
                        and k
                        not in {
                            "fixed_width_ft",
                            "max_building_width_ft",
                            "academic_width_ft",
                        }
                    )
                },
                "note": (explore.get("note") or "")[:500],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "label": label,
        "ok": out.get("ok"),
        "attempts": archive.get("attempts"),
        "cells": len(cells),
        "legal": len(legal),
        "legal_by_mass_n": dict(by_n),
        "legal_groupings": dict(groups.most_common(12)),
        "p_pool_size": len(pool),
        "p_pool_status": dict(Counter(str(e.get("status")) for e in pool.values())),
        "dump": str(dump_path),
        "session_id": session.study_id,
        "legal_entries": legal,
        "base_constraints": copy.deepcopy(
            {
                k: v
                for k, v in session.constraints.items()
                if k
                not in {
                    "explore",
                    "explore_budget",
                    "cover_budget",
                }
            }
        ),
        "program_path": str(GSF),
        "config_path": str(CONFIG),
    }


def _fresh_session_for_entry(
    entry: dict[str, Any],
    *,
    program_path: Path,
    config_path: Path,
    base_constraints: dict[str, Any] | None = None,
) -> StudySession:
    program = load_program_file(program_path, config_path=config_path)
    session = StudySession(
        study_id="realize_replay",
        program=program,
        config_path=str(config_path),
    )
    if base_constraints:
        # Start from brief-level constraints so department_widths / caps exist,
        # then restore_entry overwrites mass-scoped + snapshot constraints.
        for k, v in base_constraints.items():
            if k.endswith("_width_ft") and k not in {
                "fixed_width_ft",
                "max_building_width_ft",
                "academic_width_ft",
            }:
                continue
            session.constraints[k] = copy.deepcopy(v)
    restore_entry(session, entry)
    return session


def replay_realize(
    entries: list[dict[str, Any]],
    *,
    program_path: Path,
    config_path: Path,
    base_constraints: dict[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    kept_with_dims = 0
    rebuilt = 0
    failed_rebuild = 0
    failed_saved = 0
    by_group: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "saved_ok": 0, "rebuild_ok": 0})

    for i, entry in enumerate(entries):
        if limit is not None and i >= limit:
            break
        gsig = _grouping_sig(entry)
        by_group[gsig]["n"] += 1
        cell = str(entry.get("cell") or i)
        # (a) saved dimensions
        session = _fresh_session_for_entry(
            entry,
            program_path=program_path,
            config_path=config_path,
            base_constraints=base_constraints,
        )
        widths_before = {
            m.id: session.constraints.get(f"{m.id}_width_ft") for m in session.masses
        }
        result = solve_massing_study(session, config_path=str(config_path))
        from massing_explorer.explore.performance import measure

        perf_saved = measure(result, session)
        saved_ok = bool(perf_saved.get("fits_limitations"))
        if saved_ok:
            kept_with_dims += 1
            by_group[gsig]["saved_ok"] += 1
        else:
            failed_saved += 1

        # (b) strip + realize
        session2 = _fresh_session_for_entry(
            entry,
            program_path=program_path,
            config_path=config_path,
            base_constraints=base_constraints,
        )
        cleared = _strip_realized_widths(session2)
        result2, perf2 = realize(session2)
        rebuild_ok = bool(perf2.get("fits_limitations"))
        if rebuild_ok:
            rebuilt += 1
            by_group[gsig]["rebuild_ok"] += 1
        else:
            failed_rebuild += 1
        widths_after = {
            m.id: session2.constraints.get(f"{m.id}_width_ft") for m in session2.masses
        }
        def _failed(perf: dict[str, Any]) -> list[Any]:
            raw = perf.get("failed_checks")
            if isinstance(raw, (list, tuple)):
                return list(raw)[:6]
            if raw in (None, 0, ""):
                return []
            return [raw]

        rows.append(
            {
                "cell": cell,
                "mass_n": _mass_n(entry),
                "grouping": gsig,
                "idea": entry.get("cell") or idea_key(session) if session.masses else None,
                "partition": partition_id(session) if session.masses else None,
                "stories": entry.get("stories"),
                "reason": entry.get("reason"),
                "saved_ok": saved_ok,
                "rebuild_ok": rebuild_ok,
                "cleared_keys": cleared,
                "widths_saved": widths_before,
                "widths_rebuild": widths_after,
                "saved_failed": _failed(perf_saved),
                "rebuild_failed": _failed(perf2),
                "rebuild_distance": perf2.get("feasibility_distance"),
            }
        )

    return {
        "n": len(rows),
        "saved_ok": kept_with_dims,
        "rebuild_ok": rebuilt,
        "saved_fail": failed_saved,
        "rebuild_fail": failed_rebuild,
        "rebuild_rate": (rebuilt / len(rows)) if rows else None,
        "by_grouping": dict(by_group),
        "failures": [r for r in rows if r["saved_ok"] and not r["rebuild_ok"]][:20],
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--from-archive",
        type=Path,
        help="JSON dump from a prior run (archive_cells or list of legal entries)",
    )
    parser.add_argument("--skip-harvest", action="store_true")
    parser.add_argument("--only", choices=("3", "34", "both"), default="both")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    import massing_explorer.session as session_mod

    studies = ROOT / "studies"
    studies.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.TemporaryDirectory()
    orig = session_mod.STUDIES_DIR
    session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
    report: dict[str, Any] = {"tag": "realize_step1", "harvest": {}, "replay": {}}
    try:
        legal_pool: list[dict[str, Any]] = []
        base_constraints: dict[str, Any] | None = None

        if args.from_archive:
            raw = json.loads(args.from_archive.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                legal_pool = raw
            elif isinstance(raw, dict) and "archive_cells" in raw:
                legal_pool = list((raw.get("archive_cells") or {}).values())
                base_constraints = raw.get("base_constraints")
            elif isinstance(raw, dict) and "cells" in raw and isinstance(raw["cells"], dict):
                legal_pool = [c for c in raw["cells"].values() if c.get("fits_limitations")]
            else:
                raise SystemExit(f"Unrecognized archive JSON: {args.from_archive}")
            report["harvest"]["source"] = str(args.from_archive)
            report["harvest"]["legal"] = len(legal_pool)
            # Brief-level constraints (dept widths, caps) if dump omitted them.
            if not base_constraints:
                brief = str(raw.get("brief") or BRIEF_34) if isinstance(raw, dict) else BRIEF_34
                program = load_program_file(GSF, config_path=CONFIG)
                boot = StudySession(
                    study_id="boot_constraints",
                    program=program,
                    config_path=str(CONFIG),
                )
                # Parse-only: apply brief with tiny cover so constraints land.
                boot.constraints["cover_budget"] = {
                    "start": 0,
                    "step_small": 0,
                    "step_large": 0,
                    "max": 0,
                }
                boot.constraints["explore_budget"] = {
                    "mcts_sims": 0,
                    "mcts_depth": 0,
                    "mcts_roots": 0,
                    "bo": 0,
                    "refine": 0,
                    "repair": 0,
                }
                try:
                    apply_brief(boot, brief)
                except Exception:
                    pass
                base_constraints = copy.deepcopy(
                    {
                        k: v
                        for k, v in boot.constraints.items()
                        if k
                        not in {
                            "explore",
                            "explore_budget",
                            "cover_budget",
                        }
                        and not (
                            str(k).endswith("_width_ft")
                            and k
                            not in {
                                "fixed_width_ft",
                                "max_building_width_ft",
                                "academic_width_ft",
                            }
                        )
                    }
                )
        elif not args.skip_harvest:
            briefs = []
            if args.only in ("3", "both"):
                briefs.append(("3mass", BRIEF_3))
            if args.only in ("34", "both"):
                briefs.append(("34mass", BRIEF_34))
            for label, brief in briefs:
                print(f"=== harvest {label} ===", flush=True)
                h = _run_brief(label, brief, studies)
                report["harvest"][label] = {
                    k: v for k, v in h.items() if k not in {"legal_entries", "base_constraints"}
                }
                print(json.dumps(report["harvest"][label], indent=2), flush=True)
                legal_pool.extend(h["legal_entries"])
                base_constraints = h["base_constraints"]

        # Dedupe by cell id / grouping+stories+plates signature
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for e in legal_pool:
            key = str(e.get("cell") or "") + "|" + _grouping_sig(e) + "|" + json.dumps(
                e.get("stories") or {}, sort_keys=True
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(e)

        print(f"=== realize replay on {len(unique)} legal entries ===", flush=True)
        replay = replay_realize(
            unique,
            program_path=GSF,
            config_path=CONFIG,
            base_constraints=base_constraints,
            limit=args.limit,
        )
        # Keep report lighter on disk
        report["replay"] = {
            k: v for k, v in replay.items() if k != "rows"
        }
        report["replay"]["sample_rows"] = replay["rows"][:15]
        out_path = studies / "_diag_realize_step1.json"
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report["replay"], indent=2), flush=True)
        print("wrote", out_path, flush=True)
    finally:
        session_mod.STUDIES_DIR = orig
        tmp.cleanup()


if __name__ == "__main__":
    main()
