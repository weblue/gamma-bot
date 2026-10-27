"""Offline research commands."""
import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .data.coverage import audit_dataset
from .data.io import load_dataset
from .data.models import CapabilityError, InvalidDatasetError, RULE_IDS
from .data.provenance import protect_inputs, to_jsonable


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gamma_bot")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="validate normalized files and report missing strategy inputs")
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    try:
        protect_inputs(args.dataset, args.output)
        dataset = load_dataset(args.dataset)
        report = audit_dataset(dataset, RULE_IDS)
        args.output.write_text(json.dumps(to_jsonable(report), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Coverage: {report.classification}; {len(dataset.events)} validated events.")
        for rule, reason in report.missing_rules.items():
            print(f"Missing {rule}: {reason}")
        if not report.intersection_dates:
            print("Missing common coverage: no date with every required input established.")
        print(f"Audit written to {args.output}")
        return 2 if report.missing_rules or not report.intersection_dates else 0
    except (InvalidDatasetError, CapabilityError, OSError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 1
