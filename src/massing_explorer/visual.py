from __future__ import annotations

from pathlib import Path

from .massing_models import MassingStudyResult


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
        ax_plan.set_title(
            f"{mass.name}\n{w:g} x {l:.0f} ft plan",
            fontsize=10,
        )
        ax_plan.set_xlabel("ft")
        ax_plan.set_ylabel("ft")
        fit = "PASS" if mass.fit_pass else "FAIL"
        ax_plan.text(
            0,
            -0.08,
            f"GSF {mass.actual_gsf:,.0f}/{mass.target_gsf:,.0f} [{fit}]",
            transform=ax_plan.transAxes,
            fontsize=8,
        )

        # Elevation (stacked usable areas as stories)
        story_h = 14  # ft display height per story
        y = 0
        for fl in mass.floors:
            scale = fl.usable_area_sf / fl.area_sf if fl.area_sf else 1
            bar_w = w * max(scale, 0.15)
            face = "#bbbbbb" if fl.voids else color
            ax_elev.add_patch(
                Rectangle(
                    (0, y),
                    bar_w,
                    story_h - 1,
                    facecolor=face,
                    edgecolor="#222",
                    alpha=0.7,
                )
            )
            label = f"L{fl.level}  {fl.usable_area_sf:,.0f} SF"
            if fl.voids:
                label += " (void)"
            ax_elev.text(bar_w / 2, y + story_h / 2 - 0.5, label, ha="center", va="center", fontsize=8)
            y += story_h

        ax_elev.set_xlim(0, max(w, 1) * 1.2)
        ax_elev.set_ylim(0, story_h * len(mass.floors) + 2)
        ax_elev.set_aspect("equal")
        ax_elev.set_title("Elevation (schematic)", fontsize=10)
        ax_elev.set_xlabel("width (ft)")
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
