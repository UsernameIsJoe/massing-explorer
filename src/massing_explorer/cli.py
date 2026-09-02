from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_project_config
from .load import load_program_file
from .report import format_program_report


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
