"""Credential-free, deterministic research manifests."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
import json
from pathlib import Path

from .io import load_dataset
from .models import InvalidDatasetError, RunConfig


def to_jsonable(value):
    if is_dataclass(value):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (frozenset, set)):
        return [to_jsonable(v) for v in sorted(value)]
    if isinstance(value, (tuple, list)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def protect_inputs(dataset_path: Path, output: Path) -> None:
    """An output may live beside inputs but must never replace an input file."""
    dataset_path, output = Path(dataset_path), Path(output)
    inputs = ([dataset_path / name for name in ("events.jsonl", "metadata.json", "sessions.jsonl",
                                               "macro_releases.jsonl", "margin_points.jsonl")]
              if dataset_path.is_dir() else [dataset_path, dataset_path.with_suffix(".metadata.json")])
    for source in inputs:
        if output.resolve() == source.resolve() or (output.exists() and source.exists() and output.samefile(source)):
            raise InvalidDatasetError("output must not overwrite dataset inputs")


def write_manifest(dataset_path: Path, config: RunConfig, code_revision: str, output: Path) -> None:
    protect_inputs(dataset_path, output)
    dataset = load_dataset(dataset_path)
    payload = {
        "schema_version": 1,
        "code_revision": code_revision,
        "config": to_jsonable(config),
        "input_hashes": dict(dataset.input_hashes),
        "source_ids": sorted(dataset.source_metadata),
        "sources": to_jsonable(dataset.source_metadata),
        "coverage": to_jsonable(dataset.coverage),
        "limitations": list(dataset.coverage.limitations),
    }
    Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
