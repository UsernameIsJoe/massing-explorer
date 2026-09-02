from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .config import grossing_from_config, load_project_config
from .models import Department, GrossingConfig, ProgramStudy, VerificationMessage
from .parser.excel import load_rows_from_file
from .parser.tabular import parse_tabular_rows


def _aggregate_departments(rooms: list, grossing: GrossingConfig) -> list[Department]:
    by_dept: dict[str, list] = defaultdict(list)
    for room in rooms:
        by_dept[room.department].append(room)

    departments = []
    for name, dept_rooms in by_dept.items():
        nfa = sum(r.total_area_sf for r in dept_rooms)
        departments.append(
            Department(
                name=name,
                nfa_sf=nfa,
                target_gsf=nfa * grossing.combined_multiplier,
                room_count=len(dept_rooms),
                rooms=dept_rooms,
            )
        )
    departments.sort(key=lambda d: d.name)
    return departments


def _verify_study(
    study: ProgramStudy,
    declared_totals: dict,
    dept_declared: dict,
) -> list[VerificationMessage]:
    messages: list[VerificationMessage] = []
    tolerance_sf = 1.0  # rounding tolerance for area sums

    for dept in study.departments:
        declared = dept_declared.get(dept.name)
        if declared is None:
            continue
        delta = abs(dept.nfa_sf - declared)
        passed = delta <= tolerance_sf
        messages.append(
            VerificationMessage(
                passed=passed,
                severity="warning" if not passed else "info",
                message=(
                    f"Department '{dept.name}': computed NFA {dept.nfa_sf:,.0f} SF "
                    f"vs declared {declared:,.0f} SF (delta {dept.nfa_sf - declared:+,.0f})"
                ),
            )
        )

    computed_nfa = sum(d.nfa_sf for d in study.departments)
    declared_nfa = declared_totals.get("nfa_sf")
    if declared_nfa is not None:
        delta = abs(computed_nfa - declared_nfa)
        passed = delta <= tolerance_sf
        messages.append(
            VerificationMessage(
                passed=passed,
                severity="error" if not passed else "info",
                message=(
                    f"Building NFA: computed {computed_nfa:,.0f} SF "
                    f"vs declared {declared_nfa:,.0f} SF (delta {computed_nfa - declared_nfa:+,.0f})"
                ),
            )
        )

    declared_gfa = declared_totals.get("gfa_sf")
    if declared_gfa is not None:
        computed_gfa = computed_nfa * study.grossing.grossing_factor
        # File may use grossing only (no area adjustment)
        delta_simple = abs(computed_gfa - declared_gfa)
        computed_gfa_adj = computed_nfa * study.grossing.combined_multiplier
        delta_adj = abs(computed_gfa_adj - declared_gfa)
        best_delta = min(delta_simple, delta_adj)
        passed = best_delta <= max(tolerance_sf, declared_gfa * 0.01)
        messages.append(
            VerificationMessage(
                passed=passed,
                severity="warning" if not passed else "info",
                message=(
                    f"Building GFA: declared {declared_gfa:,.0f} SF; "
                    f"engine (NFA×grossing) {computed_gfa:,.0f} SF; "
                    f"engine (NFA×adj×grossing) {computed_gfa_adj:,.0f} SF"
                ),
            )
        )

    file_grossing = declared_totals.get("grossing_factor")
    if file_grossing is not None:
        passed = abs(file_grossing - study.grossing.grossing_factor) < 0.01
        messages.append(
            VerificationMessage(
                passed=passed,
                severity="info",
                message=(
                    f"Grossing factor: file {file_grossing:.2f}, "
                    f"config {study.grossing.grossing_factor:.2f}"
                ),
            )
        )

    if not study.rooms:
        messages.append(
            VerificationMessage(
                passed=False,
                severity="error",
                message="No rooms parsed from file.",
            )
        )

    return messages


def load_program_file(
    path: str | Path,
    config_path: str | Path | None = None,
) -> ProgramStudy:
    rows, file_meta = load_rows_from_file(path)
    rooms, declared_totals, parse_meta = parse_tabular_rows(rows, source_file=str(path))

    config = load_project_config(config_path)
    grossing = grossing_from_config(config)

    # If file declares grossing factor, use it unless config explicitly overrides
    file_grossing = declared_totals.get("grossing_factor")
    if file_grossing and config.get("grossing", {}).get("grossing_factor") is None:
        grossing.grossing_factor = file_grossing

    departments = _aggregate_departments(rooms, grossing)
    computed_nfa = sum(d.nfa_sf for d in departments)

    dept_declared = parse_meta.get("department_declared_totals", {})
    for dept in departments:
        if dept.name in dept_declared:
            dept.declared_total_sf = dept_declared[dept.name]

    study = ProgramStudy(
        source_file=str(path),
        grossing=grossing,
        rooms=rooms,
        departments=departments,
        totals={
            "nfa_sf": computed_nfa,
            "target_gsf": computed_nfa * grossing.combined_multiplier,
            "declared_nfa_sf": declared_totals.get("nfa_sf") or 0,
            "declared_gfa_sf": declared_totals.get("gfa_sf") or 0,
        },
        metadata={**file_meta, **parse_meta},
    )
    study.verification = _verify_study(study, declared_totals, dept_declared)
    return study
