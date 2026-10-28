"""Offline research commands."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
from typing import Sequence

from .config import load_config
from .data.coverage import audit_dataset
from .data.io import load_dataset
from .data.models import CapabilityError, InvalidDatasetError, RULE_IDS
from .data.provenance import protect_inputs, to_jsonable


def _replay(args) -> int:
    from .replay.engine import run_replay
    from .strategies import ReversalStrategy, BreakoutStrategy, OpeningRangeBaseline

    config = load_config(args.config)
    constructors = {'reversal': ReversalStrategy, 'breakout': BreakoutStrategy,
                    'opening_range_baseline': OpeningRangeBaseline}
    strategy = constructors[args.variant](config)
    dataset = load_dataset(args.dataset)
    result = run_replay(dataset, config, strategy.variant, strategies=[strategy])
    payloads = {name: getattr(result, name) for name in
                ('trades', 'equity_curve', 'skips', 'unresolved_exposure', 'coverage', 'manifest')}
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=False)
    payloads['manifest'] = dict(result.manifest, code_revision=revision.stdout.strip() if revision.returncode == 0 else 'unknown')
    for name in payloads:
        target = args.output / (name + '.json')
        protect_inputs(args.dataset, target)
        if target.resolve() == args.config.resolve() or (
                target.exists() and target.samefile(args.config)):
            raise InvalidDatasetError('output must not overwrite config input')
    args.output.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (args.output / (name + '.json')).write_text(
            json.dumps(to_jsonable(payload), indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    print(f"Replay: {result.manifest['status']}; {len(result.trades)} closed trade slices; coverage {result.coverage.classification}.")
    for rule, reason in result.coverage.missing_rules.items():
        print(f'Missing {rule}: {reason}')
    if result.unresolved_exposure:
        print('Unresolved exposure: see unresolved_exposure.json; no end-of-file close invented.')
    print(f'Replay artifacts written to {args.output}')
    return 2 if result.manifest['status'] == 'unsupported' else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gamma_bot")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="validate normalized files and report missing strategy inputs")
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser('replay', help='replay one explicit offline MES strategy experiment')
    replay.add_argument('--dataset', type=Path, required=True)
    replay.add_argument('--config', type=Path, required=True)
    replay.add_argument('--variant', choices=('reversal', 'breakout', 'opening_range_baseline'), required=True)
    replay.add_argument('--output', type=Path, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    try:
        if args.command == 'replay':
            return _replay(args)
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
