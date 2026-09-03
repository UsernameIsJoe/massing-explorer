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

        # Plan
        ax_plan.add_patch(
            FancyBboxPatch(
                (0, 0),
                w,
                l,
                boxstyle="square,pad=0",
                facecolor=color,
                edgecolor="#222",
                alpha=0.55,
                linewidth=1.5,
            )
        )
        # Void outline on plan if present on floor 1
        void_floor = next((f for f in mass.floors if f.voids), None)
        if void_floor:
            for v in void_floor.voids:
                vw = min(v.width_ft, w)
                vl = min(v.length_ft, l)
                ax_plan.add_patch(
                    Rectangle(
                        (2, 2),
                        vw,
                        vl,
                        fill=False,
                        linestyle="--",
                        edgecolor="#111",
                        linewidth=1.2,
                        label="void",
                    )
                )
                ax_plan.text(
                    2 + vw / 2,
                    2 + vl / 2,
                    "void",
                    ha="center",
                    va="center",
                    fontsize=8,
                )

        ax_plan.set_xlim(-5, max(w, 1) + 10)
        ax_plan.set_ylim(-5, max(l, 1) + 10)
        ax_plan.set_aspect("equal")
        fit = "PASS" if mass.fit_pass else "FAIL"
        ax_plan.set_title(
            f"{mass.name}\n{w:.0f} x {l:.0f} ft plan, {len(mass.floors)} stories\n"
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
        for fl in mass.floors:
            scale = fl.usable_area_sf / fl.area_sf if fl.area_sf else 1
            bar_w = w * max(scale, 0.15)

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
                bar_w + w * 0.03,
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
