from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_project_config
from .load import load_program_file
from .report import format_program_report


def cmd_chat(args: argparse.Namespace) -> int:
    from .chat import run_chat_loop
    from .ollama_client import OllamaClient, OllamaError
    from .session import StudySession, slugify_study_id

    study_id = slugify_study_id(args.study)

    try:
        if args.program:
            program = load_program_file(args.program, config_path=args.config)
            session = StudySession(
                study_id=study_id,
                program=program,
                config_path=args.config or "",
                model=args.model,
            )
            session.save()
            print(f"Created study '{study_id}' from {args.program}")
        else:
            session = StudySession.load(study_id)
            if args.model:
                session.model = args.model
            print(f"Resumed study '{study_id}'")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Start a new study with: chat --study NAME --program FILE.xlsx")
        return 1

    client = OllamaClient(model=session.model)
    try:
        models = client.list_models()
        if models and session.model not in models:
            # Allow partial match (e.g. llama3.1 vs llama3.1:8b)
            match = next((m for m in models if m.startswith(session.model)), None)
            if match:
                client.model = match
                session.model = match
            else:
                print(f"Warning: model '{session.model}' not in Ollama. Available: {models[:5]}")
    except OllamaError as e:
        print(f"Warning: {e}")

    run_chat_loop(session, client)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    study = load_program_file(args.program, config_path=args.config)
    report = format_program_report(study)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"Report written to {out}")

    print(report)

    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(study.to_dict(), indent=2), encoding="utf-8")
        print(f"JSON written to {json_path}")

    if args.strict and not study.verification_passed:
        return 1
    return 0


def cmd_solve(args: argparse.Namespace) -> int:
    from .report import format_massing_report
    from .session import StudySession, slugify_study_id
    from .solver import solve_massing_study
    from .tools import pair_masses, set_grouping
    from .visual import render_massing_visual, render_site_plan

    study_id = slugify_study_id(args.study)

    if args.program:
        program = load_program_file(args.program, config_path=args.config)
        session = StudySession(
            study_id=study_id,
            program=program,
            config_path=args.config or "",
        )
    else:
        try:
            session = StudySession.load(study_id)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            print("Provide --program or an existing --study")
            return 1

    if args.config:
        session.config_path = args.config

    # Optional demo grouping if none set
    if not session.masses and args.demo_grouping:
        depts = session.department_names()
        academic = [d for d in depts if "ACADEMIC" in d.upper() or "SPECIAL" in d.upper()]
        hpe = [d for d in depts if "HEALTH" in d.upper() or "PHYSICAL" in d.upper() or "DINING" in d.upper()]
        rest = [d for d in depts if d not in academic and d not in hpe]
        masses = []
        if academic:
            masses.append(
                {
                    "id": "academic",
                    "name": "Academic Wing",
                    "departments": academic,
                    "story_count": 3,
                }
            )
        if hpe:
            masses.append(
                {
                    "id": "hpe_dining",
                    "name": "HPE / Dining",
                    "departments": hpe,
                    "story_count": 2,
                }
            )
        if rest:
            masses.append(
                {
                    "id": "support",
                    "name": "Support / Admin",
                    "departments": rest,
                    "story_count": 2,
                }
            )
        set_grouping(session, masses)
        # Gym is typically double-height
        if "Gymnasium" not in session.double_height_rooms:
            session.double_height_rooms.append("Gymnasium")
        session.constraints.setdefault("academic_width_ft", 80)
        session.constraints.setdefault("hpe_dining_width_ft", 100)
        session.save()
        print("Applied demo grouping (academic / hpe_dining / support).")

    if not session.masses:
        print("No masses defined. Use chat to set groupings, or pass --demo-grouping.")
        return 1

    # Apply CLI width overrides
    if args.width:
        session.constraints["fixed_width_ft"] = args.width
        session.constraints["academic_width_ft"] = args.width
    if args.tolerance is not None:
        session.constraints["gsf_tolerance"] = args.tolerance
    if args.max_length is not None:
        session.constraints["max_building_length_ft"] = args.max_length
    if args.max_total_length is not None:
        session.constraints["max_total_length_ft"] = args.max_total_length

    if args.pair:
        mass_ids = [m.strip() for m in args.pair.split(",") if m.strip()]
        pair_result = pair_masses(session, mass_ids, args.pair_length)
        if not pair_result.get("ok"):
            print(f"Pairing error: {pair_result.get('error')}")
            return 1
        print(
            f"Paired {', '.join(mass_ids)} in {args.pair_length:g} ft -> "
            f"shared width {pair_result['shared_width_ft']:g} ft"
        )

    result = solve_massing_study(session, config_path=session.config_path or None)
    session.last_massing = result.to_dict()
    session.save()

    report = format_massing_report(result)
    print(report)

    out_dir = Path(args.output_dir) if args.output_dir else session.study_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "massing_report.txt"
    report_path.write_text(report, encoding="utf-8")
    json_path = out_dir / "massing_study.json"
    json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"JSON:   {json_path}")

    if args.visual:
        visual_path = Path(args.visual)
        try:
            render_massing_visual(result, visual_path)
            print(f"Visual: {visual_path}")
            site_path = visual_path.with_name(f"{visual_path.stem}_site{visual_path.suffix}")
            limit = session.constraints.get("max_total_length_ft") or (
                args.pair_length if args.pair else None
            )
            render_site_plan(result, site_path, float(limit) if limit else None)
            print(f"Site plan: {site_path}")
        except ImportError as e:
            print(f"Visual skipped: {e}")

    return 0 if all(v.passed or v.check.startswith("anchor") for v in result.validation if "gsf_fit" in v.check) else 0


def cmd_search(args: argparse.Namespace) -> int:
    from .search import SiteEnvelope, apply_scheme, search_schemes
    from .session import StudySession, slugify_study_id
    from .solver import solve_massing_study
    from .report import format_massing_report
    from .visual import render_massing_visual, render_site_plan

    study_id = slugify_study_id(args.study)
    if args.program:
        program = load_program_file(args.program, config_path=args.config)
        session = StudySession(
            study_id=study_id, program=program, config_path=args.config or ""
        )
    else:
        try:
            session = StudySession.load(study_id)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            print("Provide --program or an existing --study")
            return 1

    if args.config:
        session.config_path = args.config
    if not session.masses:
        print("No masses defined. Use chat or `solve --demo-grouping` first.")
        return 1

    envelope = SiteEnvelope(
        max_building_length_ft=args.max_length,
        max_building_width_ft=args.max_width,
        max_total_length_ft=args.max_total_length,
        max_stories=args.max_stories,
    )
    print("SITE ENVELOPE")
    for key, value in envelope.to_dict().items():
        print(f"  {key}: {value}")
    print(f"  preference: {args.preference}\n")

    candidates, notes = search_schemes(
        session,
        envelope,
        preference=args.preference,
        top_n=args.top,
        config_path=session.config_path or None,
    )
    for note in notes:
        print(f"  note: {note}")

    if not candidates:
        print("\nNo scheme fits this envelope.")
        return 1

    print(f"{len(candidates)} verified scheme(s), best first:\n")
    for i, cand in enumerate(candidates):
        print(f"  [{i}] {cand.summary()}")
        print(
            f"      mean stories {cand.metrics['mean_stories']:.1f} | "
            f"score {cand.score:.3f}"
        )

    if args.apply is None:
        print("\nRe-run with --apply <index> to write one into the study.")
        return 0

    if args.apply < 0 or args.apply >= len(candidates):
        print(f"\n--apply must be 0..{len(candidates) - 1}")
        return 1

    chosen = candidates[args.apply]
    apply_scheme(session, chosen)
    print(f"\nApplied [{args.apply}] {chosen.summary()}")

    result = solve_massing_study(session, config_path=session.config_path or None)
    session.last_massing = result.to_dict()
    session.save()
    report = format_massing_report(result)
    print(report)

    if args.visual:
        path = Path(args.visual)
        try:
            render_massing_visual(result, path)
            site = path.with_name(f"{path.stem}_site{path.suffix}")
            render_site_plan(result, site, envelope.max_total_length_ft)
            print(f"Visual: {path}\nSite plan: {site}")
        except ImportError as e:
            print(f"Visual skipped: {e}")
    return 0


def cmd_config_check(args: argparse.Namespace) -> int:
    config = load_project_config(args.config)
    print(json.dumps(config, indent=2))
    anchor = config.get("anchor_rooms", {})
    if anchor:
        print("\nAnchor rooms:")
        for name, spec in anchor.items():
            w = spec.get("min_width_ft", "?")
            l = spec.get("min_length_ft", "?")
            print(f"  {name}: {w} x {l} ft")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="massing_explorer",
        description="Program-to-massing dimension study tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Parse program file and print study report")
    ingest.add_argument("program", help="Path to Excel (.xlsx) or CSV program file")
    ingest.add_argument(
        "--config", "-c", help="Project config YAML (default: config/project.yaml)"
    )
    ingest.add_argument("--output", "-o", help="Write text report to file")
    ingest.add_argument("--json", "-j", help="Write ProgramStudy JSON to file")
    ingest.add_argument(
        "--strict", action="store_true", help="Exit 1 if verification errors"
    )
    ingest.set_defaults(func=cmd_ingest)

    cfg = sub.add_parser("config", help="Show loaded project config")
    cfg.add_argument("config", help="Path to project YAML")
    cfg.set_defaults(func=cmd_config_check)

    chat = sub.add_parser("chat", help="Chat with local LLM about program grouping")
    chat.add_argument("--study", "-s", required=True, help="Study ID (saved under studies/)")
    chat.add_argument("--program", "-p", help="Program Excel/CSV (required for new study)")
    chat.add_argument("--config", "-c", help="Project config YAML")
    chat.add_argument(
        "--model", "-m", default="qwen2.5:7b", help="Ollama model (default: qwen2.5:7b)"
    )
    chat.set_defaults(func=cmd_chat)

    solve = sub.add_parser(
        "solve", help="Solve mass footprints and validate GSF / anchor rooms"
    )
    solve.add_argument("--study", "-s", required=True, help="Study ID")
    solve.add_argument("--program", "-p", help="Program file (new study or refresh)")
    solve.add_argument("--config", "-c", help="Project config YAML")
    solve.add_argument(
        "--demo-grouping",
        action="store_true",
        help="If no masses set, apply a simple 3-mass demo grouping",
    )
    solve.add_argument("--width", type=float, help="Fixed width (ft) for all masses")
    solve.add_argument(
        "--tolerance", type=float, help="GSF tolerance fraction (default 0.03)"
    )
    solve.add_argument(
        "--pair",
        help="Comma-separated mass ids to share a width, e.g. academic,support",
    )
    solve.add_argument(
        "--pair-length",
        type=float,
        default=0.0,
        help="Total combined length (ft) for --pair masses",
    )
    solve.add_argument(
        "--max-length", type=float, help="Max length (ft) allowed per mass"
    )
    solve.add_argument(
        "--max-total-length",
        type=float,
        help="Max combined length (ft) for all masses on the site",
    )
    solve.add_argument(
        "--visual",
        default="output/massing_checkpoint.png",
        help="Write plan/elevation PNG (default: output/massing_checkpoint.png)",
    )
    solve.add_argument("--output-dir", "-o", help="Directory for report/json (default: studies/<id>)")
    solve.set_defaults(func=cmd_solve)

    search = sub.add_parser(
        "search", help="Find story counts and widths that fit a site envelope"
    )
    search.add_argument("--study", "-s", required=True, help="Study ID")
    search.add_argument("--program", "-p", help="Program file (new study)")
    search.add_argument("--config", "-c", help="Project config YAML")
    search.add_argument(
        "--max-total-length", type=float, help="Max combined length (ft) on the site"
    )
    search.add_argument("--max-length", type=float, help="Max length (ft) per mass")
    search.add_argument("--max-width", type=float, help="Max width (ft) per mass")
    search.add_argument(
        "--max-stories", type=int, default=4, help="Story ceiling (default 4)"
    )
    search.add_argument(
        "--preference",
        default="balanced",
        choices=["balanced", "low_rise", "compact"],
        help="Ranking bias (default balanced)",
    )
    search.add_argument("--top", type=int, default=3, help="How many schemes to show")
    search.add_argument(
        "--apply", type=int, help="Apply scheme by index and print the full report"
    )
    search.add_argument("--visual", help="PNG path (only with --apply)")
    search.set_defaults(func=cmd_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
