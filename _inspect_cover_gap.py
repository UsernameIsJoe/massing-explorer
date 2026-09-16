"""Inspect why COVER misses the 53c win combo on the Underwood brief."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from massing_explorer.brief import apply_brief, parse_brief
from massing_explorer.explore import archive as archive_mod
from massing_explorer.explore.cover import build_cover_plan, run_cover
from massing_explorer.explore.partitions import apply_partition
from massing_explorer.explore.p_pool import partition_key, seed_p_pool
from massing_explorer.explore.realize import realize
from massing_explorer.load import load_program_file
from massing_explorer.session import StudySession

ROOT = Path(__file__).resolve().parent
GSF = ROOT / "examples" / "Underwood_Elementary_Space_Summary_GSF_Tweaked.xlsx"
CONFIG = ROOT / "config" / "project.example.yaml"
BRIEF = (
    "3 masses, max 3 floors. length max 60 meters. gym and dining together and "
    "double height. art and music prefer on ground floor. media prefer on top "
    "floor above admin. admin have to be on ground floor. core academic and "
    "special ed width has to be 80 feet. mass ratio have to be between 2:5 and "
    "5:8. prefer 3 floors."
)

WIN_GROUPS = [
    {
        "id": "arts_support",
        "name": "Arts Support",
        "departments": [
            "ADMINISTRATION & GUIDANCE",
            "ART & MUSIC",
            "CUSTODIAL & MAINTENANCE",
            "MEDICAL",
        ],
        "story_count": 1,
    },
    {
        "id": "academic_support",
        "name": "Academic Support",
        "departments": ["CORE ACADEMIC", "MEDIA CENTER", "SPECIAL EDUCATION"],
        "story_count": 3,
    },
    {
        "id": "athletics_dining_support",
        "name": "Athletics Dining Support",
        "departments": ["DINING & FOOD SERVICE", "HEALTH & PHYSICAL EDUCATION"],
        "story_count": 2,
    },
]


def _dept_sig(entry: dict) -> frozenset:
    snap = (entry.get("snapshot") or {}).get("masses") or []
    return frozenset(frozenset(m.get("departments") or []) for m in snap)


def main() -> None:
    import massing_explorer.session as session_mod

    tmp = tempfile.TemporaryDirectory()
    orig = session_mod.STUDIES_DIR
    session_mod.STUDIES_DIR = Path(tmp.name) / "studies"
    try:
        program = load_program_file(GSF, config_path=CONFIG)
        session = StudySession(
            study_id="cover_gap", program=program, config_path=str(CONFIG)
        )
        # Apply brief constraints without running full search.
        parsed = parse_brief(BRIEF, session.department_names())
        from massing_explorer.brief import apply_parsed_brief

        apply_parsed_brief(session, parsed)
        session.save()

        win_key = partition_key(WIN_GROUPS)
        plan = build_cover_plan(session)
        plan_keys = [partition_key(p.get("groups") or []) for p in plan.partitions]
        print("cover plan partitions", len(plan.partitions))
        print("win in initial COVER plan", win_key in plan_keys)

        # Does win org+stories realize legal under brief constraints?
        apply_partition(session, WIN_GROUPS)
        for m in session.masses:
            if "CORE ACADEMIC" in (m.departments or []):
                m.story_count = 3
            elif "HEALTH & PHYSICAL EDUCATION" in (m.departments or []):
                m.story_count = 2
            else:
                m.story_count = 1
            session.constraints.pop(f"{m.id}_width_ft", None)
        session.constraints.setdefault("explore", {}).pop("realize_cache", None)
        _r, perf = realize(session)
        print(
            "win realize fits",
            perf.get("fits_limitations"),
            "kinds",
            perf.get("failed_kinds"),
            "pref",
            {
                k: perf.get(k)
                for k in (
                    "preference_alignment",
                    "program_coherence",
                    "fits_limitations",
                )
            },
        )

        # Run COVER-only and see if win org/stories appear.
        archive = archive_mod.empty_archive()

        def evaluate(sess, store, reason):
            result, p = realize(sess)
            archive_mod.insert(store, sess, result, p, reason=reason)

        session.constraints["cover_budget"] = {
            "start": 40,
            "step_small": 10,
            "step_large": 20,
            "max": 100,
        }
        report = run_cover(session, archive, evaluate=evaluate)
        cells = list((archive.get("cells") or {}).values())
        legal = [c for c in cells if c.get("fits_limitations")]
        win_sig = frozenset(
            frozenset(g["departments"]) for g in WIN_GROUPS
        )
        org_hits = [c for c in cells if _dept_sig(c) == win_sig]
        story_hits = [
            c
            for c in org_hits
            if (c.get("stories") or {}).get("academic_support") == 3
            and (c.get("stories") or {}).get("arts_support") == 1
            and (c.get("stories") or {}).get("athletics_dining_support") == 2
        ]
        pool = (archive.get("p_pool") or {}).get("entries") or {}
        print("COVER attempts", archive.get("attempts"), "legal", len(legal))
        print("win org samples", len(org_hits), "win org+story", len(story_hits))
        print(
            "win in p_pool",
            any(partition_key(e.get("groups") or []) == win_key for e in pool.values()),
        )
        print("p_pool statuses", Counter(str(e.get("status")) for e in pool.values()))
        print(
            "cover report",
            {
                k: report.get(k)
                for k in ("stagnant", "incomplete", "attempts", "legal")
            },
        )
        if org_hits:
            fk = Counter()
            for c in org_hits:
                for k in (c.get("performance") or {}).get("failed_kinds") or []:
                    fk[k] += 1
            print("win-org failed_kinds", dict(fk))
            print(
                "sample win-org stories",
                [c.get("stories") for c in org_hits[:5]],
            )
    finally:
        session_mod.STUDIES_DIR = orig
        tmp.cleanup()


if __name__ == "__main__":
    main()
