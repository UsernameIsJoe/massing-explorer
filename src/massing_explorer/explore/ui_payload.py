"""
Slim projection of explore / briefing state for the studio UI.

Keeps snapshots and heavy strategy blobs out of the browser payload.
"""

from __future__ import annotations

from typing import Any


def _clause_label(clause: dict[str, Any]) -> str:
    lever = str(clause.get("lever") or "clause")
    depts = clause.get("departments") or []
    value = clause.get("value")
    unit = clause.get("unit") or ""
    text = (clause.get("text") or "").strip()
    parts = [lever.replace("_", " ")]
    if depts:
        parts.append(", ".join(str(d) for d in depts))
    if value is not None and value != "":
        if lever == "ratio" and text:
            parts.append(text)
        elif isinstance(value, list):
            lo, hi = (value + [None, None])[:2]
            if lo is not None or hi is not None:
                parts.append(f"{lo or '?'}–{hi or '?'}")
        else:
            parts.append(f"{value:g}{(' ' + unit) if unit else ''}" if isinstance(value, float) else f"{value}{(' ' + unit) if unit else ''}")
    if text and text not in parts and lever != "ratio":
        parts.append(text)
    return " · ".join(parts)


def _slim_clause(clause: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": clause.get("kind") or "",
        "lever": clause.get("lever") or "",
        "label": _clause_label(clause),
        "departments": list(clause.get("departments") or []),
        "value": clause.get("value"),
        "unit": clause.get("unit"),
        "text": clause.get("text") or "",
    }


def _cell_label(entry: dict[str, Any]) -> str:
    strategy = entry.get("strategy") or {}
    topo = ((strategy.get("T") or {}).get("kind")) or "topo?"
    stories = entry.get("stories") or {}
    story_bits = ",".join(f"{v}" for v in stories.values()) if stories else ""
    partition = str(entry.get("partition") or "")
    short_p = partition if len(partition) <= 48 else partition[:45] + "…"
    bits = [topo]
    if story_bits:
        bits.append(f"{story_bits} fl")
    if short_p:
        bits.append(short_p)
    return " · ".join(bits)


def _slim_performance(perf: dict[str, Any] | None) -> dict[str, Any]:
    if not perf:
        return {}
    keep = (
        "feasible",
        "fits_limitations",
        "spread",
        "height_variance",
        "preference_distance",
        "novelty",
        "failed_kinds",
        "limit_fails",
    )
    return {k: perf.get(k) for k in keep if k in perf}


def _slim_scheme(
    scheme: dict[str, Any],
    rank: int,
    *,
    story_height_ft: float = 14.0,
    include_preview: bool = True,
) -> dict[str, Any]:
    masses = []
    for m in scheme.get("masses") or []:
        masses.append(
            {
                "mass_id": m.get("mass_id") or m.get("id"),
                "mass_name": m.get("mass_name") or m.get("name"),
                "stories": m.get("stories") or m.get("story_count"),
                "width_ft": m.get("width_ft"),
                "length_ft": m.get("length_ft"),
            }
        )
    out: dict[str, Any] = {
        "rank": rank,
        "total_length_ft": scheme.get("total_length_ft"),
        "score": scheme.get("score"),
        "verified": bool(scheme.get("verified")),
        "failed_checks": list(scheme.get("failed_checks") or [])[:6],
        "masses": masses,
    }
    if include_preview and masses:
        from massing_explorer.preview3d import scheme_envelope_mesh

        out["preview"] = scheme_envelope_mesh(
            {"masses": masses, "verified": out["verified"], "failed_checks": out["failed_checks"],
             "total_length_ft": out["total_length_ft"]},
            story_height_ft=story_height_ft,
            rank=rank,
        )
    return out


def _slim_candidate(entry: dict[str, Any], *, kept: bool = False) -> dict[str, Any]:
    return {
        "cell_id": entry.get("cell"),
        "label": _cell_label(entry),
        "partition": entry.get("partition"),
        "fits": bool(entry.get("fits_limitations")),
        "reason": entry.get("reason") or "",
        "stories": entry.get("stories") or {},
        "traits": _slim_performance(entry.get("performance")),
        "kept": kept,
    }


def _process_steps(store: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    archive = store.get("archive") or {}
    if archive:
        steps.append(
            {
                "phase": "COVER",
                "summary": (
                    f"{archive.get('legal', 0)} legal · "
                    f"{archive.get('attempts', 0)} attempts · "
                    f"{len(archive.get('cells') or {})} cells"
                ),
                "detail": archive.get("note") or store.get("note") or "",
            }
        )
    csp = store.get("csp") or {}
    if csp:
        steps.append(
            {
                "phase": "CSP",
                "summary": csp.get("note") or ("locked" if csp.get("locked") else "open"),
                "detail": "",
            }
        )
    topo = store.get("topology") or {}
    if topo:
        steps.append(
            {
                "phase": "TOPOLOGY",
                "summary": str(topo.get("current") or topo.get("note") or "topology"),
                "detail": topo.get("note") or "",
            }
        )
    planner = store.get("planner") or {}
    if planner:
        steps.append(
            {
                "phase": "PLANNER",
                "summary": planner.get("note") or f"{planner.get('applied', 0)} applied",
                "detail": "",
            }
        )
    mcts = store.get("mcts") or {}
    if mcts:
        steps.append(
            {
                "phase": "MCTS",
                "summary": mcts.get("note") or f"{mcts.get('applied', 0)} applied",
                "detail": "",
            }
        )
    bayes = store.get("bayes") or {}
    if bayes.get("ran") or bayes.get("picked") or bayes.get("candidates"):
        picked = bayes.get("picked") or []
        steps.append(
            {
                "phase": "BO",
                "summary": (
                    f"spent {bayes.get('spent', 0)}/{bayes.get('budget', 0)} · "
                    f"observed {bayes.get('observed', 0)}"
                ),
                "detail": bayes.get("note") or "",
                "ops": [
                    {
                        "op": p.get("op"),
                        "kind": p.get("kind"),
                        "ei": p.get("ei"),
                        "reward": p.get("reward"),
                    }
                    for p in picked[:12]
                ],
                "candidates": [
                    {"op": c.get("op"), "ei": c.get("ei"), "mean": c.get("mean"), "std": c.get("std")}
                    for c in (bayes.get("candidates") or [])[:8]
                ],
            }
        )
    refine = store.get("refine") or {}
    if refine.get("ran") or refine.get("tries"):
        elites = refine.get("elites")
        # Controller stores a count (int), not a list.
        elite_n = elites if isinstance(elites, int) else len(elites or [])
        steps.append(
            {
                "phase": "REFINE",
                "summary": (
                    f"{refine.get('tries', 0)} tries · "
                    f"{'improved' if refine.get('improved') else 'no improvement'} · "
                    f"{elite_n} elites"
                ),
                "detail": refine.get("reason") or "",
            }
        )
    learning = store.get("learning") or {}
    pair = learning.get("pending_pair")
    if pair:
        steps.append(
            {
                "phase": "LEARN",
                "summary": f"A/B pending ({pair.get('kind') or 'pair'})",
                "detail": "",
                "pending_pair": {
                    "a": pair.get("a"),
                    "b": pair.get("b"),
                    "kind": pair.get("kind"),
                },
            }
        )
    explain = store.get("explain") or {}
    items = explain.get("items") or []
    sentences = explain.get("sentences") or []
    if items or sentences:
        steps.append(
            {
                "phase": "EXPLAIN",
                "summary": explain.get("note") or f"{len(items) or len(sentences)} notes",
                "items": [
                    {"kind": it.get("kind"), "sentence": it.get("sentence")}
                    for it in items[:12]
                ]
                or [{"kind": "note", "sentence": s} for s in sentences[:12]],
            }
        )
    return steps


def transparency_payload(
    session: Any,
    *,
    parsed: Any | None = None,
    briefing: dict[str, Any] | None = None,
    full_explore: bool = False,
) -> dict[str, Any]:
    """Build the UI-facing transparency object from session state."""
    briefing = briefing or session.constraints.get("briefing") or {}
    if not briefing and parsed is not None:
        from massing_explorer.brief import briefing_from_parsed

        briefing = briefing_from_parsed(parsed)

    interpreted = {
        "source": "explore" if full_explore else "regex",
        "requirements": [_slim_clause(c) for c in briefing.get("requirements") or []],
        "limitations": [_slim_clause(c) for c in briefing.get("limitations") or []],
        "preferences": [_slim_clause(c) for c in briefing.get("preferences") or []],
        "notes": list(getattr(parsed, "notes", None) or [])[:24],
        "unknown_programs": list(getattr(parsed, "unknown_programs", None) or []),
        "unmatched": list(getattr(parsed, "unmatched", None) or []),
    }

    schemes = list(getattr(session, "last_search", None) or [])
    store = session.constraints.get("explore") or {}
    archive = store.get("archive") or {}

    # kept_cell is stored as a cell id string (sometimes a full entry dict).
    kept_raw = store.get("kept_cell")
    kept_entry: dict[str, Any] | None = None
    kept_id: str | None = None
    if isinstance(kept_raw, dict):
        kept_entry = kept_raw
        kept_id = kept_raw.get("cell")
    elif isinstance(kept_raw, str) and kept_raw:
        kept_id = kept_raw
        cells = archive.get("cells") or {}
        if kept_id in cells and isinstance(cells[kept_id], dict):
            kept_entry = cells[kept_id]

    story_h = 14.0
    try:
        from massing_explorer.rhino_export import story_height_from_config
        from massing_explorer.config import load_project_config

        cfg_path = getattr(session, "config_path", None) or None
        cfg = load_project_config(cfg_path) if cfg_path else {}
        story_h = float(story_height_from_config(cfg) or 14.0)
    except Exception:
        story_h = 14.0

    selected_rank = 0 if schemes else None
    sample_pool: dict[str, Any] = {
        "count": len(schemes),
        "selected_rank": selected_rank,
        "schemes": [
            _slim_scheme(s, i, story_height_ft=story_h) for i, s in enumerate(schemes[:12])
        ],
        "archive_preview": [],
    }
    if archive and not schemes:
        try:
            from massing_explorer.explore import archive as archive_mod

            legal = archive_mod.legal_cells(archive)
            sample_pool["count"] = len(legal)
            sample_pool["archive_preview"] = [
                _slim_candidate(e, kept=(e.get("cell") == kept_id)) for e in legal[:12]
            ]
        except Exception:
            sample_pool["archive_preview"] = []

    top_candidates: list[dict[str, Any]] = []
    if archive:
        try:
            from massing_explorer.explore.controller import select_elites
            from massing_explorer.explore.preference import taste_weight

            weights = ((store.get("learning") or {}).get("weights") or {})
            elites = select_elites(archive, weights)
            for entry in elites:
                top_candidates.append(
                    _slim_candidate(entry, kept=(entry.get("cell") == kept_id))
                )
            if kept_id and kept_id not in {c["cell_id"] for c in top_candidates}:
                if kept_entry and kept_entry.get("cell"):
                    top_candidates.insert(0, _slim_candidate(kept_entry, kept=True))
            # If elites empty, still show a few legal cells by taste
            if not top_candidates:
                from massing_explorer.explore import archive as archive_mod

                legal = archive_mod.legal_cells(archive)
                ranked = sorted(legal, key=lambda e: taste_weight(e, weights), reverse=True)
                top_candidates = [
                    _slim_candidate(e, kept=(e.get("cell") == kept_id)) for e in ranked[:5]
                ]
        except Exception:
            top_candidates = []

    process = {
        "mode": store.get("mode") or ("cover" if full_explore else "quick"),
        "full_explore": bool(full_explore and store),
        "steps": _process_steps(store) if store else (
            [
                {
                    "phase": "QUICK",
                    "summary": "Grouping + dimension fit (no COVER archive)",
                    "detail": "Toggle Full explore for COVER / BO / LEARN / REFINE.",
                }
            ]
            + (
                [
                    {
                        "phase": "SITE SEARCH",
                        "summary": f"{len(schemes)} verified scheme(s) in sample pool",
                        "detail": "",
                    }
                ]
                if schemes
                else []
            )
        ),
        "archive": {
            "attempts": archive.get("attempts", 0),
            "legal": archive.get("legal", 0),
            "cells": len(archive.get("cells") or {}),
            "unsupported": list(archive.get("unsupported") or [])[:12],
            "partitions": store.get("partitions") or archive.get("stated_partition"),
        }
        if archive
        else None,
        "note": store.get("note") or "",
    }

    return {
        "interpreted": interpreted,
        "sample_pool": sample_pool,
        "top_candidates": top_candidates,
        "process": process,
    }
