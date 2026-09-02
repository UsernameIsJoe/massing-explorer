from __future__ import annotations

from .models import ProgramStudy


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
