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
        "program_coherence",
        "preference_alignment",
        "performance_efficiency",
        "robustness",
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
        "index": int(scheme.get("_pool_index", rank) or rank),
        "source": "search",
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


def _slim_candidate(entry: dict[str, Any], *, kept: bool = False, story_height_ft: float = 14.0) -> dict[str, Any]:
    strategy = entry.get("strategy") or {}
    geom = strategy.get("G") or {}
    topo = strategy.get("T") or {}
    out: dict[str, Any] = {
        "cell_id": entry.get("cell"),
        "label": _cell_label(entry),
        "partition": entry.get("partition"),
        "fits": bool(entry.get("fits_limitations")),
        "reason": entry.get("reason") or "",
        "stories": entry.get("stories") or {},
        "traits": _slim_performance(entry.get("performance")),
        "kept": kept,
        "topology": topo.get("kind") or "",
        "envelope": geom.get("envelope") or "",
        "loading": geom.get("loading") or "",
    }
    masses = _masses_from_entry(entry)
    if masses:
        from massing_explorer.preview3d import scheme_envelope_mesh

        out["masses"] = masses
        out["preview"] = scheme_envelope_mesh(
            {"masses": masses, "verified": out["fits"]},
            story_height_ft=story_height_ft,
        )
        total = sum(float(m.get("length_ft") or 0) for m in masses)
        out["total_length_ft"] = round(total, 1)
    return out


def _masses_from_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    plates = list(entry.get("plates") or [])
    if plates:
        return [
            {
                "mass_id": p.get("mass_id"),
                "mass_name": p.get("mass_name") or p.get("mass_id"),
                "stories": p.get("stories"),
                "width_ft": p.get("width_ft"),
                "length_ft": p.get("length_ft"),
            }
            for p in plates
            if float(p.get("width_ft") or 0) > 0 and float(p.get("length_ft") or 0) > 0
        ]
    snap = entry.get("snapshot") or {}
    stories = entry.get("stories") or snap.get("stories") or {}
    widths = snap.get("widths") or {}
    lengths = list((entry.get("performance") or {}).get("lengths") or [])
    out: list[dict[str, Any]] = []
    for i, mass in enumerate(snap.get("masses") or []):
        if not isinstance(mass, dict):
            continue
        mid = str(mass.get("id") or "")
        try:
            width = float(widths.get(mid) or 0)
        except (TypeError, ValueError):
            width = 0.0
        length = float(lengths[i]) if i < len(lengths) else 0.0
        if width <= 0 or length <= 0:
            continue
        out.append(
            {
                "mass_id": mid,
                "mass_name": mass.get("name") or mid,
                "stories": int(stories.get(mid) or mass.get("story_count") or 2),
                "width_ft": width,
                "length_ft": length,
            }
        )
    return out


def _scheme_signature(scheme: dict[str, Any]) -> tuple:
    masses = scheme.get("masses") or []
    stories = tuple(int(m.get("stories") or m.get("story_count") or 0) for m in masses)
    widths = tuple(int(round(float(m.get("width_ft") or 0) / 10.0) * 10) for m in masses)
    return (stories, widths, bool(scheme.get("verified")))


def _dedupe_schemes(schemes: list[dict[str, Any]], cap: int | None = None) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for scheme in schemes:
        sig = _scheme_signature(scheme)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(scheme)
        if cap is not None and len(out) >= cap:
            break
    return out


def _all_archive_entries(archive: dict[str, Any]) -> list[dict[str, Any]]:
    cells = list((archive.get("cells") or {}).values())
    cells.sort(key=lambda e: (not bool(e.get("fits_limitations")), str(e.get("reason") or "")))
    return cells


def _process_steps(store: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    archive = store.get("archive") or {}
    if archive:
        cover = archive.get("cover") if isinstance(archive.get("cover"), dict) else {}
        plan = archive.get("cover_plan") if isinstance(archive.get("cover_plan"), dict) else {}
        cover_bits = []
        if cover:
            cover_bits.append(
                f"budget {cover.get('attempts', 0)}/{cover.get('max', 120)} "
                f"(start {cover.get('start', 40)})"
            )
            if cover.get("samples_planned") is not None:
                cover_bits.append(f"pool {cover.get('samples_planned')}")
            if cover.get("incomplete"):
                cover_bits.append("incomplete")
        elif plan.get("pool"):
            cover_bits.append(f"pool {plan.get('pool')}")
        steps.append(
            {
                "phase": "COVER",
                "summary": (
                    f"{archive.get('legal', 0)} legal · "
                    f"{archive.get('attempts', 0)} attempts · "
                    f"{len(archive.get('cells') or {})} cells"
                    + (f" · {'; '.join(cover_bits)}" if cover_bits else "")
                ),
                "detail": archive.get("note") or store.get("note") or "",
            }
        )
    diagnose = store.get("diagnose") or {}
    if diagnose.get("ran"):
        probes = diagnose.get("probes") or []
        conflict = diagnose.get("conflict") or {}
        steps.append(
            {
                "phase": "DIAGNOSE",
                "summary": (
                    f"{diagnose.get('class') or 'unknown'}"
                    + (
                        f" · {len(probes)} probe(s)"
                        if probes
                        else ""
                    )
                ),
                "detail": diagnose.get("note") or conflict.get("note") or "",
                "probes": [
                    {
                        "label": p.get("label"),
                        "kind": p.get("kind"),
                        "lever": p.get("lever"),
                        "unlocks": p.get("unlocks"),
                        "apply": False,
                    }
                    for p in probes[:8]
                ],
                "knowledge": list(diagnose.get("knowledge") or [])[:8],
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


def _drawing_signature(card: dict[str, Any]) -> tuple:
    """Rounded footprint fingerprint for UI dedupe."""
    masses = card.get("masses") or []
    if not masses:
        return ("empty", card.get("cell_id") or card.get("label") or "")
    return tuple(
        (
            str(m.get("mass_id") or m.get("id") or ""),
            int(m.get("stories") or 0),
            round(float(m.get("width_ft") or 0)),
            round(float(m.get("length_ft") or 0)),
        )
        for m in masses
    )


def _dedupe_drawings(cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep one card per drawn plate; prefer legal / kept."""
    ranked = sorted(
        cards,
        key=lambda c: (
            not bool(c.get("fits") or c.get("kept")),
            not bool(c.get("kept")),
            int(c.get("rank") or 0),
        ),
    )
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    collapsed = 0
    for card in ranked:
        sig = _drawing_signature(card)
        if sig in seen:
            collapsed += 1
            continue
        seen.add(sig)
        out.append(card)
    out.sort(key=lambda c: int(c.get("rank") or 0))
    for i, card in enumerate(out):
        card["rank"] = i
    return out, collapsed


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
    search_pool = _dedupe_schemes(
        [{**s, "_pool_index": i} if isinstance(s, dict) else s for i, s in enumerate(schemes)],
    )
    archive_cards: list[dict[str, Any]] = []
    if archive:
        for i, entry in enumerate(_all_archive_entries(archive)):
            try:
                card = _slim_candidate(entry, kept=(entry.get("cell") == kept_id), story_height_ft=story_h)
                card["rank"] = i
                card["source"] = "archive"
                card["verified"] = bool(card.get("fits"))
                archive_cards.append(card)
            except Exception:
                continue

    pool_schemes: list[dict[str, Any]] = []
    drawings_collapsed = 0
    if archive_cards:
        pool_schemes, drawings_collapsed = _dedupe_drawings(archive_cards)
        selected_rank = 0
    elif search_pool:
        pool_schemes = [
            _slim_scheme(s, i, story_height_ft=story_h) for i, s in enumerate(search_pool)
        ]
        selected_rank = 0

    cover = archive.get("cover") if isinstance(archive.get("cover"), dict) else {}
    sample_pool: dict[str, Any] = {
        "count": len(pool_schemes) or len(schemes),
        "selected_rank": selected_rank,
        "schemes": pool_schemes,
        "archive_preview": archive_cards,
        "typology_cells": len(archive_cards),
        "drawings_collapsed": drawings_collapsed,
        "cover_attempts": int(cover.get("attempts") or archive.get("attempts") or 0),
        "cover_start": int(cover.get("start") or 40),
        "cover_max": int(cover.get("max") or 120),
        "cover_pool": int(
            (cover.get("samples_planned") if cover else None)
            or ((archive.get("cover_plan") or {}).get("pool") if isinstance(archive.get("cover_plan"), dict) else 0)
            or 0
        ),
        "cover_incomplete": bool(cover.get("incomplete") or archive.get("cover_incomplete")),
    }

    top_candidates: list[dict[str, Any]] = []
    if archive:
        try:
            from massing_explorer.explore.controller import select_elites_explained
            from massing_explorer.explore.preference import taste_weight

            weights = ((store.get("learning") or {}).get("weights") or {})
            elites = select_elites_explained(archive, weights)
            for row in elites:
                card = _slim_candidate(
                    row["entry"], kept=(row["entry"].get("cell") == kept_id), story_height_ft=story_h
                )
                card["why"] = row.get("why") or ""
                top_candidates.append(card)
            if kept_id and kept_id not in {c["cell_id"] for c in top_candidates}:
                if kept_entry and kept_entry.get("cell"):
                    top_candidates.insert(0, _slim_candidate(kept_entry, kept=True, story_height_ft=story_h))
            # If elites empty, still show a few legal cells by taste
            if not top_candidates:
                from massing_explorer.explore import archive as archive_mod

                legal = archive_mod.legal_cells(archive)
                ranked = sorted(legal, key=lambda e: taste_weight(e, weights), reverse=True)
                top_candidates = [
                    _slim_candidate(e, kept=(e.get("cell") == kept_id), story_height_ft=story_h) for e in ranked[:5]
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
