"""
Empty-cell sentences: why a region of the archive is empty or illegal.

Unsupported topologies are named, not 'we failed to sample'. A locked
grouping is why other partitions are missing. A cap break is why a cell
does not count as coverage.
"""

from __future__ import annotations

from typing import Any

from .strategy import grouping_is_required, required_mass_count, story_band
from .topology import stated_frontage_ft, topology_is_required

UNSUPPORTED_TOPOLOGIES = (
    ("courtyard", "Courtyard is empty because the engine cannot draw it yet. That is unsupported, not a coverage failure."),
    ("podium", "Podium is empty because the engine cannot draw it yet. That is unsupported, not a coverage failure."),
    ("perpendicular_wings", "Perpendicular wings are empty because the engine cannot draw them yet. That is unsupported, not a coverage failure."),
)


def empty_cells(session: Any, archive: dict[str, Any] | None = None) -> dict[str, Any]:
    """Structured empty-cell map plus readable sentences."""
    from . import archive as archive_mod

    archive = archive or archive_mod.load_archive(session)
    items: list[dict[str, Any]] = []
    for _name, sentence in UNSUPPORTED_TOPOLOGIES:
        items.append({"kind": "unsupported", "cell": _name, "sentence": sentence})

    locked = grouping_is_required(session)
    if locked:
        count = required_mass_count(session)
        extra = f" ({count} masses)" if count else ""
        items.append(
            {
                "kind": "locked",
                "cell": "P",
                "sentence": (
                    f"Program organization was required{extra}, so other partitions "
                    "were not sampled. A stated must is not a Monte Carlo coordinate."
                ),
            }
        )
    else:
        parts = {e.get("partition") for e in (archive.get("cells") or {}).values() if e.get("partition")}
        if len(parts) <= 1:
            items.append(
                {
                    "kind": "unsampled",
                    "cell": "P",
                    "sentence": (
                        "P was open, but COVER only recorded one organization. "
                        "Other legal CSP partitions may still be empty."
                    ),
                }
            )

    pairings = list(getattr(session, "pairings", None) or [])
    if topology_is_required(session):
        items.append(
            {
                "kind": "locked",
                "cell": "T",
                "sentence": (
                    "Topology was required, so other T were not sampled. "
                    "A stated pairing is not a Monte Carlo coordinate."
                ),
            }
        )
    elif stated_frontage_ft(session) is None:
        items.append(
            {
                "kind": "unsampled",
                "cell": "paired_bars",
                "sentence": (
                    "Paired bars were not drawable because the brief did not state a site frontage. "
                    "D is only the stated rectangle. That is missing site, not a coverage failure."
                ),
            }
        )
    elif not pairings:
        sampled = {
            ((e.get("strategy") or {}).get("T") or {}).get("kind")
            for e in (archive.get("cells") or {}).values()
        }
        if "paired_bars" not in sampled:
            items.append(
                {
                    "kind": "unsampled",
                    "cell": "paired_bars",
                    "sentence": (
                        "Paired-bar topology is empty. Independent bars were sampled; "
                        "paired bars are drawable when a frontage is stated."
                    ),
                }
            )

    for name in ("streets", "neighbors", "topography"):
        verb = "is" if name == "topography" else "are"
        items.append(
            {
                "kind": "unsupported",
                "cell": name,
                "sentence": (
                    f"{name.capitalize()} {verb} empty because D is only the stated rectangle. "
                    "That is unsupported, not a coverage failure."
                ),
            }
        )

    lock = session.constraints.get("story_lock") or {}
    for mass in session.masses:
        if mass.id not in lock:
            continue
        band = story_band(int(lock[mass.id]))
        items.append(
            {
                "kind": "locked",
                "cell": f"V:{mass.id}",
                "sentence": (
                    f"{mass.name} is story-locked at {lock[mass.id]} ({band}), "
                    "so other vertical bands on that mass were not opened."
                ),
            }
        )

    infeasible = [
        (key, entry)
        for key, entry in (archive.get("cells") or {}).items()
        if not entry.get("fits_limitations")
    ]
    if infeasible:
        site = 0
        for _key, entry in infeasible:
            kinds = (entry.get("performance") or {}).get("failed_kinds") or []
            if "site_length" in kinds or "site_width" in kinds:
                site += 1
        items.append(
            {
                "kind": "infeasible",
                "cell": "limitations",
                "sentence": (
                    f"{len(infeasible)} sampled cell(s) missed a limitation "
                    f"({site} site-cap). They are attempts, not coverage. "
                    "A cap is a filter, not a target."
                ),
            }
        )
        for key, entry in infeasible[:2]:
            kinds = (entry.get("performance") or {}).get("failed_kinds") or []
            items.append(
                {
                    "kind": "infeasible",
                    "cell": key,
                    "sentence": _cap_clause(list(kinds), entry),
                }
            )

    if not archive.get("attempts"):
        items.append(
            {
                "kind": "unsampled",
                "cell": "archive",
                "sentence": "No strategy has been evaluated yet, so every supported cell is still empty.",
            }
        )

    return {
        "count": len(items),
        "unsupported": sum(1 for i in items if i["kind"] == "unsupported"),
        "infeasible": sum(1 for i in items if i["kind"] == "infeasible"),
        "locked": sum(1 for i in items if i["kind"] == "locked"),
        "unsampled": sum(1 for i in items if i["kind"] == "unsampled"),
        "items": items,
        "sentences": [i["sentence"] for i in items],
        "note": (
            f"{len(items)} empty-cell sentence(s): "
            f"{sum(1 for i in items if i['kind'] == 'unsupported')} unsupported, "
            f"{sum(1 for i in items if i['kind'] == 'infeasible')} infeasible, "
            f"{sum(1 for i in items if i['kind'] == 'locked')} locked."
        ),
    }


def _cap_clause(kinds: list[str], entry: dict[str, Any]) -> str:
    reason = entry.get("reason") or "this attempt"
    if "site_length" in kinds or "site_width" in kinds:
        return (
            f"A sampled cell from {reason} missed a site cap. "
            "Infeasible cells do not count as coverage. A cap is a filter, not a target."
        )
    if "gsf_fit" in kinds:
        return f"A sampled cell from {reason} missed GSF fit, so it is an attempt, not coverage."
    if "anchor_fit" in kinds:
        return f"A sampled cell from {reason} failed an anchor-room fit. The strategy is recorded as infeasible."
    if "layout_dims" in kinds:
        return f"A sampled cell from {reason} could not host leftover program around a void at the drawn plate."
    return f"A sampled cell from {reason} failed a limitation, so it is not coverage."
