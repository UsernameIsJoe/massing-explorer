from __future__ import annotations

from .massing_models import MassingStudyResult
from .models import ProgramStudy


def format_massing_report(result: MassingStudyResult) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("MASSING EXPLORER - DIMENSION STUDY REPORT")
    lines.append("=" * 72)
    if result.study_id:
        lines.append(f"Study: {result.study_id}")
    lines.append(f"GSF tolerance: +/-{result.gsf_tolerance * 100:.0f}%")
    if result.rationale:
        lines.append(f"Method: {result.rationale}")
    lines.append("")

    if not result.masses:
        lines.append("No solved masses.")
        lines.append("")
        for v in result.validation:
            icon = "OK" if v.passed else "FAIL"
            lines.append(f"  [{icon}] {v.message}")
        lines.append("=" * 72)
        return "\n".join(lines)

    for mass in result.masses:
        fit = "PASS" if mass.fit_pass else "FAIL"
        lines.append(f"MASS: {mass.name} [{mass.id}]")
        lines.append(f"  Departments: {', '.join(mass.departments)}")
        pair_note = f" | Paired: {mass.pairing_id}" if mass.pairing_id else ""
        lines.append(
            f"  Fixed {mass.fixed_side}: {mass.fixed_dim_ft:.1f} ft | "
            f"Stories: {len(mass.floors)}{pair_note}"
        )
        lines.append(
            f"  Target GSF: {mass.target_gsf:,.0f} | Actual: {mass.actual_gsf:,.0f} | "
            f"Fit: {mass.fit_delta_sf:+,.0f} [{fit}]"
        )
        lines.append("  Floors:")
        for fl in mass.floors:
            void_note = ""
            if fl.voids:
                names = ", ".join(v.room for v in fl.voids)
                void_note = (
                    f"  void: {names} "
                    f"(-{sum(v.area_sf for v in fl.voids):,.0f} SF)"
                )
            lines.append(
                f"    L{fl.level}: {fl.width_ft:.1f} x {fl.length_ft:.1f} ft = "
                f"{fl.area_sf:,.0f} SF footprint, {fl.usable_area_sf:,.0f} SF usable"
                f"{void_note}"
            )
            if fl.allocations:
                for a in fl.allocations:
                    flag = " (split)" if a.split else ""
                    lines.append(
                        f"         {a.gsf:>9,.0f} SF  {a.department}{flag}"
                    )
                lines.append(
                    f"         {'-' * 9}  {fl.utilization * 100:.0f}% of usable area "
                    f"({fl.allocated_gsf:,.0f} SF allocated)"
                )
            elif fl.programs:
                lines.append(f"         programs: {', '.join(fl.programs)}")
        lines.append("")

    lines.append("VALIDATION")
    for v in result.validation:
        icon = "OK" if v.passed else "FAIL"
        lines.append(f"  [{icon}] {v.message}")
    lines.append("")

    if result.compromised_anchor_rooms:
        lines.append("COMPROMISED ANCHOR ROOMS")
        for c in result.compromised_anchor_rooms:
            lines.append(
                f"  - {c.room} in [{c.mass_id}]: needs {c.required_ft} ft, "
                f"available {c.available_ft} ft ({c.reason})"
            )
    else:
        lines.append("COMPROMISED ANCHOR ROOMS: none")

    if result.resize_suggestions:
        lines.append("")
        lines.append("RESIZE SUGGESTIONS")
        for s in result.resize_suggestions:
            lines.append(f"  - [{s.mass_id}] {s.issue}")
            lines.append(f"    -> {s.suggestion}")

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


def format_program_report(study: ProgramStudy) -> str:
    lines: list[str] = []
    g = study.grossing

    lines.append("=" * 72)
    lines.append("MASSING EXPLORER - PROGRAM STUDY REPORT")
    lines.append("=" * 72)
    lines.append(f"Source: {study.source_file}")
    if study.metadata.get("sheet"):
        lines.append(f"Sheet:  {study.metadata['sheet']}")
    lines.append(f"Rooms:  {len(study.rooms)}")
    lines.append("")
    lines.append("GROSSING")
    lines.append(f"  Area adjustment:  {g.area_adjustment:.3f}")
    lines.append(f"  Grossing factor:  {g.grossing_factor:.3f}")
    lines.append(f"  Combined:         {g.combined_multiplier:.3f}")
    lines.append("")

    lines.append("DEPARTMENTS")
    lines.append(f"  {'Department':<40} {'NFA (SF)':>12} {'Target GSF':>14} {'Rooms':>6}")
    lines.append("  " + "-" * 76)
    for dept in sorted(study.departments, key=lambda d: -d.nfa_sf):
        lines.append(
            f"  {dept.name:<40} {dept.nfa_sf:>12,.0f} {dept.target_gsf:>14,.0f} {dept.room_count:>6}"
        )
    lines.append("  " + "-" * 76)
    lines.append(
        f"  {'TOTAL':<40} {study.totals['nfa_sf']:>12,.0f} "
        f"{study.totals['target_gsf']:>14,.0f} {len(study.rooms):>6}"
    )
    lines.append("")

    if study.totals.get("declared_nfa_sf"):
        lines.append("DECLARED TOTALS (from file)")
        lines.append(f"  Declared NFA: {study.totals['declared_nfa_sf']:,.0f} SF")
    if study.totals.get("declared_gfa_sf"):
        lines.append(f"  Declared GFA: {study.totals['declared_gfa_sf']:,.0f} SF")
    lines.append("")

    lines.append("VERIFICATION")
    for msg in study.verification:
        icon = "OK" if msg.passed else msg.severity.upper()
        lines.append(f"  [{icon}] {msg.message}")
    lines.append("")

    lines.append("ROOM DETAIL (by department)")
    for dept in sorted(study.departments, key=lambda d: d.name):
        lines.append(f"\n  [{dept.name}]")
        for room in dept.rooms:
            lines.append(
                f"    {room.qty:>3}x {room.area_sf:>8,.0f} SF  "
                f"= {room.total_area_sf:>8,.0f} SF  {room.room_name}"
            )

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)
