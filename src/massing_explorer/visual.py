from __future__ import annotations

from pathlib import Path

from .massing_models import MassingStudyResult, SolvedMass

DEPT_PALETTE = (
    "#4C78A8",
    "#F58518",
    "#54A24B",
    "#E45756",
    "#B279A2",
    "#72B7B2",
    "#EECA3B",
    "#9D755D",
    "#BAB0AC",
)


def _short(name: str, limit: int = 16) -> str:
    """Trim a department name for a narrow drawing label."""
    name = name.replace(" & ", "/").title()
    return name if len(name) <= limit else name[: limit - 1] + "\u2026"


def _department_colors(mass: SolvedMass) -> dict[str, str]:
    """Stable colour per department across every floor of a mass."""
    seen: list[str] = []
    for floor in mass.floors:
        for alloc in floor.allocations:
            if alloc.department not in seen:
                seen.append(alloc.department)
    for dept in mass.departments:
        if dept not in seen:
            seen.append(dept)
    return {d: DEPT_PALETTE[i % len(DEPT_PALETTE)] for i, d in enumerate(seen)}


def render_massing_visual(
    result: MassingStudyResult,
    output_path: str | Path,
) -> Path:
    """
    Simple checkpoint visual: plan footprints + stacked elevation bars.
    Requires matplotlib.
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for visuals. pip install matplotlib"
        ) from e

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    masses = result.masses
    if not masses:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "No masses to draw", ha="center", va="center")
        ax.axis("off")
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)
        return out

    n = len(masses)
    fig, axes = plt.subplots(2, n, figsize=(4.2 * n, 7), squeeze=False)
    colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2", "#72B7B2"]

    for i, mass in enumerate(masses):
        color = colors[i % len(colors)]
        ax_plan = axes[0][i]
        ax_elev = axes[1][i]
        ground = mass.floors[0]
        w, l = ground.width_ft, ground.length_ft

        # Plan of the most informative floor: a voided level if one exists
        # (that is where an L-shaped leftover appears), otherwise ground.
        plan_floor = next((f for f in mass.floors if f.voids), ground)
        pw, pl = plan_floor.width_ft, plan_floor.length_ft
        dept_colors = _department_colors(mass)
        ax_plan.add_patch(
            FancyBboxPatch(
                (0, 0),
                pw,
                pl,
                boxstyle="square,pad=0",
                facecolor="#f4f4f4",
                edgecolor="#222",
                linewidth=1.5,
            )
        )
        drawn = False
        for alloc in plan_floor.allocations:
            for piece in alloc.footprints:
                ax_plan.add_patch(
                    Rectangle(
                        (piece.x_ft, piece.y_ft),
                        piece.width_ft,
                        piece.length_ft,
                        facecolor=dept_colors.get(alloc.department, color),
                        edgecolor="#222",
                        alpha=0.7,
                        linewidth=0.8,
                    )
                )
                if piece.area_sf > plan_floor.area_sf * 0.08:
                    ax_plan.text(
                        piece.x_ft + piece.width_ft / 2,
                        piece.y_ft + piece.length_ft / 2,
                        _short(alloc.department, 14),
                        ha="center",
                        va="center",
                        fontsize=7,
                    )
                drawn = True
        if not drawn:
            ax_plan.add_patch(
                FancyBboxPatch(
                    (0, 0),
                    pw,
                    pl,
                    boxstyle="square,pad=0",
                    facecolor=color,
                    edgecolor="#222",
                    alpha=0.55,
                    linewidth=1.5,
                )
            )
        for v in plan_floor.voids:
            ax_plan.add_patch(
                Rectangle(
                    (v.x_ft, v.y_ft),
                    v.width_ft,
                    v.length_ft,
                    facecolor="none",
                    linestyle="--",
                    edgecolor="#111",
                    linewidth=1.2,
                    hatch="///",
                )
            )
            ax_plan.text(
                v.x_ft + v.width_ft / 2,
                v.y_ft + v.length_ft / 2,
                f"void\n{v.room}",
                ha="center",
                va="center",
                fontsize=7,
            )

        # Stepped mass: outline every upper floor over the ground fill so the
        # set-back reads in plan. Width is constant, so floors nest along length.
        if mass.is_stepped:
            for fl in mass.floors[1:]:
                ax_plan.add_patch(
                    Rectangle(
                        (0, 0),
                        fl.width_ft,
                        fl.length_ft,
                        fill=False,
                        edgecolor="#222",
                        linewidth=1.0,
                        linestyle=(0, (4, 2)),
                    )
                )
                ax_plan.text(
                    fl.width_ft * 0.97,
                    fl.length_ft,
                    f"L{fl.level}",
                    ha="right",
                    va="bottom",
                    fontsize=6.5,
                    color="#333",
                )

        ax_plan.set_xlim(-5, max(w, 1) + 10)
        ax_plan.set_ylim(-5, max(l, 1) + 10)
        ax_plan.set_aspect("equal")
        fit = "PASS" if mass.fit_pass else "FAIL"
        if any(a.shape in {"L", "U"} for a in plan_floor.allocations):
            shape = f"L{plan_floor.level} L-plan"
        elif mass.is_stepped:
            shape = "stepped"
        else:
            shape = f"L{plan_floor.level} plan"
        ax_plan.set_title(
            f"{mass.name}\n{pw:.0f} x {pl:.0f} ft {shape}, {len(mass.floors)} stories\n"
            f"GSF {mass.actual_gsf:,.0f}/{mass.target_gsf:,.0f} [{fit}]",
            fontsize=9,
        )
        ax_plan.set_xlabel("ft")
        ax_plan.set_ylabel("ft")

        # Elevation: each story split horizontally by allocated program area,
        # so "what program on which level" reads directly off the drawing.
        story_h = 14  # ft display height per story
        dept_colors = _department_colors(mass)
        y = 0
        base_area = mass.floors[0].area_sf or 1.0
        for fl in mass.floors:
            # Bar length tracks the plate, so a step-back reads as a shorter
            # bar; the void is then hatched off the end of that plate.
            plate_w = w * max(fl.area_sf / base_area, 0.05)
            usable_frac = fl.usable_area_sf / fl.area_sf if fl.area_sf else 1.0
            bar_w = plate_w * max(usable_frac, 0.15)

            if usable_frac < 0.999:
                ax_elev.add_patch(
                    Rectangle(
                        (bar_w, y),
                        max(plate_w - bar_w, 0),
                        story_h - 1,
                        facecolor="none",
                        edgecolor="#555",
                        hatch="///",
                        linewidth=0.8,
                    )
                )

            if fl.allocations and fl.allocated_gsf > 0:
                x = 0.0
                for alloc in fl.allocations:
                    seg = bar_w * (alloc.gsf / fl.allocated_gsf)
                    ax_elev.add_patch(
                        Rectangle(
                            (x, y),
                            seg,
                            story_h - 1,
                            facecolor=dept_colors[alloc.department],
                            edgecolor="#222",
                            alpha=0.75,
                        )
                    )
                    if seg > bar_w * 0.16:
                        ax_elev.text(
                            x + seg / 2,
                            y + story_h / 2 - 0.5,
                            f"{_short(alloc.department)}\n{alloc.gsf:,.0f}",
                            ha="center",
                            va="center",
                            fontsize=6.5,
                        )
                    x += seg
            else:
                ax_elev.add_patch(
                    Rectangle(
                        (0, y),
                        bar_w,
                        story_h - 1,
                        facecolor="#bbbbbb" if fl.voids else color,
                        edgecolor="#222",
                        alpha=0.7,
                    )
                )

            note = f"L{fl.level}  {fl.usable_area_sf:,.0f} SF"
            if fl.voids:
                note += " (void)"
            ax_elev.text(
                plate_w + w * 0.03,
                y + story_h / 2 - 0.5,
                note,
                ha="left",
                va="center",
                fontsize=7,
                color="#333",
            )
            y += story_h

        ax_elev.set_xlim(0, max(w, 1) * 1.6)
        ax_elev.set_ylim(0, story_h * len(mass.floors) + 2)
        ax_elev.set_aspect("equal")
        ax_elev.set_title("Program by level", fontsize=10)
        ax_elev.set_xlabel("share of floor area")
        ax_elev.set_ylabel("height (schematic)")

    fig.suptitle(
        f"Massing Explorer — {result.study_id or 'study'} (checkpoint visual)",
        fontsize=12,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def render_site_plan(
    result: MassingStudyResult,
    output_path: str | Path,
    max_total_length_ft: float | None = None,
) -> Path:
    """
    Composite plan: masses laid end-to-end along the shared-width axis so the
    combined length reads against the site limit.
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for visuals. pip install matplotlib"
        ) from e

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    masses = [m for m in result.masses if m.floors]
    if not masses:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "No masses to draw", ha="center", va="center")
        ax.axis("off")
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)
        return out

    colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2", "#72B7B2"]
    # Keep pairing members contiguous so a shared width reads as one bar
    masses.sort(key=lambda m: (m.pairing_id == "", m.pairing_id))
    total_length = sum(m.floors[0].length_ft for m in masses)
    max_width = max(m.floors[0].width_ft for m in masses)
    widths = {round(m.floors[0].width_ft, 1) for m in masses}

    fig, ax = plt.subplots(figsize=(max(10, total_length / 30), 6))

    x = 0.0
    for i, mass in enumerate(masses):
        ground = mass.floors[0]
        w, l = ground.width_ft, ground.length_ft
        color = colors[i % len(colors)]
        ax.add_patch(
            Rectangle(
                (x, 0),
                l,
                w,
                facecolor=color,
                edgecolor="#222",
                alpha=0.55,
                linewidth=1.5,
            )
        )
        ax.text(
            x + l / 2,
            w / 2,
            f"{mass.name}\n{l:.0f} x {w:.0f} ft\n{len(mass.floors)} stories",
            ha="center",
            va="center",
            fontsize=9,
        )
        ax.annotate(
            "",
            xy=(x, -max_width * 0.12),
            xytext=(x + l, -max_width * 0.12),
            arrowprops={"arrowstyle": "<->", "color": "#444"},
        )
        ax.text(
            x + l / 2,
            -max_width * 0.19,
            f"{l:.0f} ft",
            ha="center",
            fontsize=8,
            color="#444",
        )
        x += l

    if max_total_length_ft:
        ok = total_length <= max_total_length_ft + 1.0
        ax.axvline(
            max_total_length_ft,
            color="#c00" if not ok else "#2a2",
            linestyle="--",
            linewidth=1.5,
        )
        ax.text(
            max_total_length_ft,
            max_width * 1.08,
            f"site limit {max_total_length_ft:g} ft"
            f" [{'OK' if ok else 'OVER'}]",
            ha="right",
            fontsize=9,
            color="#c00" if not ok else "#2a2",
        )

    ax.set_xlim(-total_length * 0.05, max(total_length, max_total_length_ft or 0) * 1.08)
    ax.set_ylim(-max_width * 0.32, max_width * 1.25)
    ax.set_aspect("equal")
    ax.set_xlabel("combined length (ft)")
    ax.set_ylabel("width (ft)")
    width_note = (
        f"shared width {max_width:.0f} ft"
        if len(widths) == 1
        else f"widths {min(widths):.0f}-{max_width:.0f} ft"
    )
    ax.set_title(
        f"Site plan — combined length {total_length:.0f} ft, {width_note}",
        fontsize=12,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def render_planner_trace(
    report: dict,
    output_path: str | Path,
    title: str = "Strategy planner",
) -> Path:
    """Accept/reject trace for one planner turn. Not a massing drawing."""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for visuals. pip install matplotlib"
        ) from e

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = list(report.get("results") or []) + [
        {
            "op": d.get("op") or "DROPPED",
            "kind": d.get("kind") or "dropped",
            "reason": d.get("reason") or "",
        }
        for d in (report.get("dropped") or [])
    ]
    if not rows:
        rows = [{"op": "(none)", "kind": "dropped", "reason": report.get("note") or "No actions."}]

    colors = {
        "applied": "#54A24B",
        "illegal": "#E45756",
        "unsupported": "#9E9E9E",
        "dropped": "#EECA3B",
    }
    fig_h = max(4.2, 1.6 + 0.72 * len(rows))
    fig, ax = plt.subplots(figsize=(11.2, fig_h))
    ax.set_xlim(0, 10)
    ax.set_ylim(-0.4, len(rows) + 1.6)
    ax.axis("off")
    ax.set_title(title, fontsize=14, fontweight="bold", loc="left", pad=8)
    counts = (
        f"applied {int(report.get('applied') or 0)}   "
        f"illegal {int(report.get('illegal') or 0)}   "
        f"unsupported {int(report.get('unsupported') or 0)}"
    )
    ax.text(0.1, len(rows) + 0.95, counts, fontsize=11, color="#333")
    ax.text(
        0.1,
        len(rows) + 0.55,
        "The engine falsifies each move. The planner does not draw feet.",
        fontsize=9,
        color="#555",
        style="italic",
    )
    for i, row in enumerate(reversed(rows)):
        y = i + 0.15
        kind = str(row.get("kind") or "dropped")
        color = colors.get(kind, "#BAB0AC")
        ax.add_patch(
            FancyBboxPatch(
                (0.15, y),
                9.6,
                0.62,
                boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor=color,
                edgecolor="none",
                alpha=0.22,
            )
        )
        ax.add_patch(
            FancyBboxPatch(
                (0.15, y),
                1.7,
                0.62,
                boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor=color,
                edgecolor="none",
            )
        )
        ax.text(
            1.0,
            y + 0.31,
            kind.upper(),
            ha="center",
            va="center",
            fontsize=8,
            color="white",
            fontweight="bold",
        )
        op = str(row.get("op") or "")
        reason = str(row.get("reason") or "")
        if len(reason) > 88:
            reason = reason[:85] + "..."
        ax.text(2.05, y + 0.38, op, va="center", fontsize=10, fontweight="bold", color="#222")
        ax.text(2.05, y + 0.14, reason, va="center", fontsize=8, color="#444")
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def render_explain_board(
    explain: dict,
    robustness: dict | None,
    output_path: str | Path,
    title: str = "Empty cells and robustness",
) -> Path:
    """Why archive regions are empty, and whether the kept strategy survives area shock."""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for visuals. pip install matplotlib"
        ) from e

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sentences = list((explain or {}).get("items") or [])
    if not sentences and (explain or {}).get("sentences"):
        sentences = [{"kind": "unsampled", "sentence": s} for s in explain["sentences"]]
    probes = list((robustness or {}).get("probes") or [])
    n_left = max(1, len(sentences))
    n_right = max(1, len(probes))
    rows = max(n_left, n_right)
    fig_h = max(5.5, 2.2 + 0.78 * rows)
    fig, axes = plt.subplots(1, 2, figsize=(14.5, fig_h))
    colors = {
        "unsupported": "#9E9E9E",
        "infeasible": "#E45756",
        "locked": "#F58518",
        "unsampled": "#4C78A8",
        "survive": "#54A24B",
        "collapse": "#E45756",
    }

    def _panel(ax, heading, lines, kind_key):
        ax.set_xlim(0, 10)
        ax.set_ylim(-0.3, len(lines) + 1.4)
        ax.axis("off")
        ax.set_title(heading, fontsize=12, fontweight="bold", loc="left")
        if not lines:
            ax.text(0.2, 0.5, "None.", fontsize=9, color="#666")
            return
        for i, row in enumerate(reversed(lines)):
            y = i + 0.12
            kind = str(row.get(kind_key) or row.get("kind") or "")
            color = colors.get(kind, "#BAB0AC")
            ax.add_patch(
                FancyBboxPatch(
                    (0.12, y),
                    9.7,
                    0.7,
                    boxstyle="round,pad=0.02,rounding_size=0.08",
                    facecolor=color,
                    edgecolor="none",
                    alpha=0.18,
                )
            )
            ax.add_patch(
                FancyBboxPatch(
                    (0.12, y),
                    1.85,
                    0.7,
                    boxstyle="round,pad=0.02,rounding_size=0.08",
                    facecolor=color,
                    edgecolor="none",
                )
            )
            ax.text(
                1.05,
                y + 0.35,
                kind.upper()[:12],
                ha="center",
                va="center",
                fontsize=7.5,
                color="white",
                fontweight="bold",
            )
            text = str(row.get("sentence") or row.get("why") or "")
            if len(text) > 96:
                text = text[:93] + "..."
            ax.text(2.15, y + 0.35, text, va="center", fontsize=8, color="#222")

    _panel(axes[0], "Why cells are empty or illegal", sentences[:10], "kind")
    probe_rows = []
    for item in probes:
        label = f"{item.get('department', '')}  {item.get('delta_pct', 0):+g}%"
        probe_rows.append(
            {
                "kind": item.get("outcome") or "",
                "why": f"{label} — {item.get('why') or ''}",
            }
        )
    heading = (
        f"Same-strategy shock  "
        f"survive {(robustness or {}).get('survived', 0)}  "
        f"collapse {(robustness or {}).get('collapsed', 0)}"
    )
    _panel(axes[1], heading, probe_rows, "kind")
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.995)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def render_csp_board(
    report: dict,
    output_path: str | Path,
    title: str = "CSP owns P",
) -> Path:
    """Constraint graph and feasible partitions. Not a massing drawing."""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for visuals. pip install matplotlib"
        ) from e

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    atoms = list(report.get("atoms") or [])
    apart = list(report.get("apart") or [])
    together = list(report.get("together") or [])
    chosen = list(report.get("chosen") or [])
    rejected = list(report.get("rejected") or [])
    rows = max(len(atoms) + len(apart) + len(together) + 2, len(chosen) + len(rejected) + 2, 4)
    fig_h = max(5.8, 2.4 + 0.62 * rows)
    fig, axes = plt.subplots(1, 2, figsize=(14.8, fig_h))

    ax = axes[0]
    ax.set_xlim(0, 10)
    ax.set_ylim(-0.4, rows + 1.3)
    ax.axis("off")
    ax.set_title("Variables and hard constraints", fontsize=12, fontweight="bold", loc="left")
    y = rows + 0.15
    for atom in atoms:
        y -= 0.72
        ax.add_patch(
            FancyBboxPatch(
                (0.12, y),
                9.7,
                0.62,
                boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor="#4C78A8",
                edgecolor="none",
                alpha=0.18,
            )
        )
        ax.add_patch(
            FancyBboxPatch(
                (0.12, y),
                1.85,
                0.62,
                boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor="#4C78A8",
                edgecolor="none",
            )
        )
        ax.text(1.05, y + 0.31, "ATOM", ha="center", va="center", fontsize=7.5, color="white", fontweight="bold")
        depts = ", ".join(str(d) for d in (atom.get("departments") or []))
        if len(depts) > 70:
            depts = depts[:67] + "..."
        ax.text(2.15, y + 0.38, str(atom.get("name") or atom.get("id") or ""), va="center", fontsize=9, fontweight="bold")
        ax.text(2.15, y + 0.14, depts or "(empty)", va="center", fontsize=7.5, color="#444")
    for pair in together:
        y -= 0.62
        ax.add_patch(
            FancyBboxPatch(
                (0.12, y), 9.7, 0.52, boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor="#54A24B", edgecolor="none", alpha=0.2,
            )
        )
        ax.text(0.3, y + 0.26, f"together  {' + '.join(str(x) for x in pair)}", va="center", fontsize=8, color="#1b5e20")
    for pair in apart:
        y -= 0.62
        ax.add_patch(
            FancyBboxPatch(
                (0.12, y), 9.7, 0.52, boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor="#E45756", edgecolor="none", alpha=0.2,
            )
        )
        ax.text(0.3, y + 0.26, f"apart  {' ≠ '.join(str(x) for x in pair)}", va="center", fontsize=8, color="#7f1d1d")
    if not together and not apart:
        y -= 0.5
        ax.text(0.2, y, "No keep-together or keep-apart clauses. Coverage is the only hard constraint.", fontsize=8, color="#555")

    ax = axes[1]
    ax.set_xlim(0, 10)
    ax.set_ylim(-0.4, rows + 1.3)
    ax.axis("off")
    shown = int(report.get("shown") or len(chosen))
    feasible = int(report.get("feasible_count") or 0)
    ax.set_title(
        f"Feasible partitions  {feasible} legal / {shown} shown",
        fontsize=12,
        fontweight="bold",
        loc="left",
    )
    y = rows + 0.15
    if report.get("locked"):
        y -= 0.55
        ax.text(0.2, y, "P is locked. CSP did not sample other organizations.", fontsize=8, color="#F58518")
    for item in chosen:
        y -= 0.72
        color = "#54A24B" if item.get("reason") == "stated grouping" else "#4C78A8"
        ax.add_patch(
            FancyBboxPatch(
                (0.12, y), 9.7, 0.62, boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor=color, edgecolor="none", alpha=0.18,
            )
        )
        ax.add_patch(
            FancyBboxPatch(
                (0.12, y), 1.85, 0.62, boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor=color, edgecolor="none",
            )
        )
        tag = "STATED" if item.get("reason") == "stated grouping" else "CSP"
        ax.text(1.05, y + 0.31, tag, ha="center", va="center", fontsize=7.5, color="white", fontweight="bold")
        groups = item.get("groups") or []
        parts = []
        for group in groups:
            parts.append("+".join(str(d).split()[0] for d in (group.get("departments") or [])))
        line = "  |  ".join(parts) if parts else str(item.get("reason") or "")
        if len(line) > 78:
            line = line[:75] + "..."
        ax.text(2.15, y + 0.38, str(item.get("reason") or ""), va="center", fontsize=8.5, fontweight="bold")
        ax.text(2.15, y + 0.14, line, va="center", fontsize=7.5, color="#444")
    for item in rejected[:3]:
        y -= 0.58
        ax.add_patch(
            FancyBboxPatch(
                (0.12, y), 9.7, 0.48, boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor="#E45756", edgecolor="none", alpha=0.16,
            )
        )
        label = str(item.get("label") or "")
        if len(label) > 70:
            label = label[:67] + "..."
        ax.text(0.3, y + 0.24, f"illegal  {label}", va="center", fontsize=7.5, color="#7f1d1d")
    y -= 0.55
    ax.text(
        0.2,
        max(y, 0.1),
        str(report.get("note") or "The LLM does not invent P."),
        fontsize=8,
        color="#333",
        style="italic",
    )

    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.995)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def render_topology_board(
    report: dict,
    output_path: str | Path,
    title: str = "Drawable T and stated D",
) -> Path:
    """Catalog of topologies the engine can draw, and the stated site. Not a courtyard."""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for visuals. pip install matplotlib"
        ) from e

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    drawable = list(report.get("drawable") or [])
    unsupported = [{"kind": k, "status": "unsupported"} for k in (report.get("unsupported") or [])]
    site = dict(report.get("site") or {})
    site_rows = [
        {
            "kind": "frontage",
            "status": "stated" if site.get("frontage_ft") else "missing",
            "text": (
                f"frontage {site.get('frontage_ft'):g} ft"
                if site.get("frontage_ft")
                else "frontage not stated"
            ),
        },
        {
            "kind": "width",
            "status": "stated" if site.get("max_width_ft") else "missing",
            "text": (
                f"width cap {site.get('max_width_ft'):g} ft"
                if site.get("max_width_ft")
                else "width not stated"
            ),
        },
        {
            "kind": "bar length",
            "status": "stated" if site.get("max_building_length_ft") else "missing",
            "text": (
                f"per-bar cap {site.get('max_building_length_ft'):g} ft (not a pairing length)"
                if site.get("max_building_length_ft")
                else "per-bar length not stated"
            ),
        },
    ]
    for name in site.get("unsupported") or []:
        site_rows.append(
            {"kind": name, "status": "unsupported", "text": f"{name} wait until those inputs exist"}
        )
    rows = max(len(drawable) + len(unsupported) + 1, len(site_rows) + 1, 5)
    fig_h = max(5.6, 2.2 + 0.7 * rows)
    fig, axes = plt.subplots(1, 2, figsize=(14.8, fig_h))
    colors = {
        "stated": "#54A24B",
        "available": "#4C78A8",
        "drawable": "#4C78A8",
        "idle": "#BAB0AC",
        "missing_d": "#F58518",
        "missing": "#F58518",
        "locked": "#F58518",
        "unsupported": "#9E9E9E",
    }

    def _panel(ax, heading, lines):
        ax.set_xlim(0, 10)
        ax.set_ylim(-0.3, rows + 1.2)
        ax.axis("off")
        ax.set_title(heading, fontsize=12, fontweight="bold", loc="left")
        y = rows + 0.1
        for row in lines:
            y -= 0.7
            status = str(row.get("status") or "")
            color = colors.get(status, "#BAB0AC")
            ax.add_patch(
                FancyBboxPatch(
                    (0.12, y), 9.7, 0.6, boxstyle="round,pad=0.02,rounding_size=0.08",
                    facecolor=color, edgecolor="none", alpha=0.18,
                )
            )
            ax.add_patch(
                FancyBboxPatch(
                    (0.12, y), 2.15, 0.6, boxstyle="round,pad=0.02,rounding_size=0.08",
                    facecolor=color, edgecolor="none",
                )
            )
            ax.text(
                1.2, y + 0.3, status.upper()[:12], ha="center", va="center",
                fontsize=7, color="white", fontweight="bold",
            )
            label = str(row.get("kind") or "")
            extra = str(row.get("text") or "")
            ax.text(2.45, y + 0.38, label.replace("_", " "), va="center", fontsize=9, fontweight="bold")
            if extra:
                ax.text(2.45, y + 0.14, extra, va="center", fontsize=7.5, color="#444")

    left = list(drawable) + unsupported
    _panel(axes[0], "T — only what layout can draw", left)
    _panel(axes[1], "D — stated rectangle only", site_rows)
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.995)
    fig.text(
        0.5,
        0.02,
        str(report.get("note") or "Courtyard stays unsupported until layout can draw it."),
        ha="center",
        fontsize=8,
        color="#333",
        style="italic",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.98))
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def render_mcts_tree(
    report: dict,
    output_path: str | Path,
    title: str = "MCTS over design actions",
) -> Path:
    """UCT tree on typed moves. Not a width enumerator."""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for visuals. pip install matplotlib"
        ) from e

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    nodes = list(report.get("nodes") or [])
    if not nodes:
        nodes = [{"id": 0, "parent": None, "depth": 0, "op": "root", "kind": "root", "visits": 0, "q": 0, "prior": 1, "reason": ""}]
    by_depth: dict[int, list[dict]] = {}
    for node in nodes:
        by_depth.setdefault(int(node.get("depth") or 0), []).append(node)
    depths = sorted(by_depth)
    cols = max(len(by_depth[d]) for d in depths)
    fig_w = max(12.5, 2.4 * cols)
    fig_h = max(6.2, 1.8 + 1.7 * (max(depths) + 1))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(-0.4, cols + 0.4)
    ax.set_ylim(-0.55, max(depths) + 1.55)
    ax.axis("off")
    ax.set_title(title, fontsize=14, fontweight="bold", loc="left", pad=10)
    counts = (
        f"simulations {int(report.get('simulations') or 0)}   "
        f"applied {int(report.get('applied') or 0)}   "
        f"illegal {int(report.get('illegal') or 0)}   "
        f"unsupported {int(report.get('unsupported') or 0)}"
    )
    ax.text(0, max(depths) + 1.28, counts, fontsize=10, color="#333")
    ax.text(
        0,
        max(depths) + 1.05,
        str(report.get("baseline") or "search.py remains the enumeration baseline. MCTS does not sit on feet."),
        fontsize=8,
        color="#555",
        style="italic",
    )
    colors = {
        "root": "#4C78A8",
        "applied": "#54A24B",
        "illegal": "#E45756",
        "unsupported": "#9E9E9E",
        "pending": "#EECA3B",
    }
    pos: dict[int, tuple[float, float]] = {}
    for depth in depths:
        row = by_depth[depth]
        y = max(depths) - depth
        for i, node in enumerate(row):
            x = i + (cols - len(row)) / 2.0
            pos[int(node["id"])] = (x, y)

    for node in nodes:
        child_id = int(node["id"])
        parent_id = node.get("parent")
        if parent_id is None or int(parent_id) not in pos:
            continue
        x0, y0 = pos[int(parent_id)]
        x1, y1 = pos[child_id]
        ax.add_patch(
            FancyArrowPatch(
                (x0 + 0.55, y0 + 0.02),
                (x1 + 0.55, y1 + 0.58),
                arrowstyle="-|>",
                mutation_scale=8,
                linewidth=0.8,
                color="#888",
            )
        )

    for node in nodes:
        x, y = pos[int(node["id"])]
        kind = str(node.get("kind") or "pending")
        color = colors.get(kind, "#BAB0AC")
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                1.1,
                0.62,
                boxstyle="round,pad=0.02,rounding_size=0.08",
                facecolor=color,
                edgecolor="#333" if node.get("planner_prior") else "none",
                linewidth=1.4 if node.get("planner_prior") else 0,
                alpha=0.9,
            )
        )
        label = str(node.get("op") or "")
        if len(label) > 18:
            label = label[:16] + "…"
        ax.text(x + 0.55, y + 0.4, label, ha="center", va="center", fontsize=7, color="white", fontweight="bold")
        stats = f"n={int(node.get('visits') or 0)}  Q={float(node.get('q') or 0):.2f}"
        if node.get("planner_prior"):
            stats = "PRIOR  " + stats
        ax.text(x + 0.55, y + 0.16, stats, ha="center", va="center", fontsize=6.5, color="white")

    path = report.get("best_path") or []
    if path:
        bits = " → ".join(f"{p.get('op')} ({p.get('kind')})" for p in path)
        if len(bits) > 110:
            bits = bits[:107] + "..."
        ax.text(0, -0.35, "Best applied path: " + bits, fontsize=8, color="#222")
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def render_bayes_board(
    report: dict,
    output_path: str | Path,
    title: str = "Bayesian optimization — evaluation budget",
) -> Path:
    """GP + EI over typed actions. Not a shape generator."""
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError as e:
        raise ImportError(
            "matplotlib is required for visuals. pip install matplotlib"
        ) from e

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    candidates = list(report.get("candidates") or [])
    picked = list(report.get("picked") or [])
    rows = max(len(candidates), len(picked), 3)
    fig_h = max(5.6, 2.2 + 0.7 * rows)
    fig, axes = plt.subplots(1, 2, figsize=(14.8, fig_h))
    colors = {
        "applied": "#54A24B",
        "duplicate": "#F58518",
        "illegal": "#E45756",
        "unsupported": "#9E9E9E",
        "ei": "#4C78A8",
    }

    def _panel(ax, heading, lines, kind_key, text_key):
        ax.set_xlim(0, 10)
        ax.set_ylim(-0.3, rows + 1.4)
        ax.axis("off")
        ax.set_title(heading, fontsize=12, fontweight="bold", loc="left")
        y = rows + 0.15
        if not lines:
            ax.text(0.2, 0.5, "None.", fontsize=9, color="#666")
            return
        for row in lines:
            y -= 0.7
            kind = str(row.get(kind_key) or "ei")
            color = colors.get(kind, "#4C78A8")
            ax.add_patch(
                FancyBboxPatch(
                    (0.12, y), 9.7, 0.6, boxstyle="round,pad=0.02,rounding_size=0.08",
                    facecolor=color, edgecolor="none", alpha=0.18,
                )
            )
            ax.add_patch(
                FancyBboxPatch(
                    (0.12, y), 1.9, 0.6, boxstyle="round,pad=0.02,rounding_size=0.08",
                    facecolor=color, edgecolor="none",
                )
            )
            ax.text(1.07, y + 0.3, kind.upper()[:12], ha="center", va="center", fontsize=7, color="white", fontweight="bold")
            text = str(row.get(text_key) or row.get("op") or "")
            extra = ""
            if row.get("ei") is not None:
                extra = f"  EI {float(row.get('ei') or 0):.3f}"
            if row.get("std") is not None:
                extra += f"  σ {float(row.get('std') or 0):.2f}"
            ax.text(2.2, y + 0.38, text, va="center", fontsize=8.5, fontweight="bold")
            ax.text(2.2, y + 0.14, extra.strip() or str(row.get("reason") or ""), va="center", fontsize=7.5, color="#444")

    cand_rows = [{"kind": "ei", "op": c.get("op"), "ei": c.get("ei"), "std": c.get("std")} for c in candidates]
    _panel(axes[0], "Unevaluated actions (GP + EI)", cand_rows, "kind", "op")
    heading = f"Spent {int(report.get('spent') or 0)} / {int(report.get('budget') or 0)} evaluations"
    _panel(axes[1], heading, picked, "kind", "op")
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.995)
    fig.text(
        0.5,
        0.02,
        str(report.get("note") or "Budget manager, not a generator. Does not sit on feet."),
        ha="center",
        fontsize=8,
        color="#333",
        style="italic",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.98))
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out
