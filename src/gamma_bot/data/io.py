"""Strict, offline JSONL ingestion. Raw vendor files require an explicit adapter."""
from dataclasses import fields, MISSING, replace
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints
from urllib.parse import urlsplit

from .semantics import experiment_gex_identity
from .models import (Dataset, GexSnapshot, InvalidDatasetError, MacroRelease,
                     MarginPoint, MesBar, NegativeRegion, OptionRow, OptionSnapshot,
                     PricePoint, Quote, RULE_IDS, Session, Trade, Wall)


EVENT_TYPES = {"mes_bar": MesBar, "quote": Quote, "trade": Trade,
               "price_point": PricePoint, "gex_snapshot": GexSnapshot,
               "option_snapshot": OptionSnapshot}
TABLE_TYPES = {"sessions": Session, "macro_releases": MacroRelease, "margin_points": MarginPoint}
SOURCE_FIELDS = frozenset({"source_id", "classification", "limitations", "availability_verified",
                          "timestamp_semantics", "model_semantics", "roll_policy", "interval_seconds",
                          "documentation_url", "homepage", "reuse_status", "model_id", "expiry_days",
                          "profile_boundary_convention", "gex_units", "ingested_at", "underlying", "provenance"})


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidDatasetError("duplicate JSON object field")
        result[key] = value
    return result


def _bad_constant(_):
    raise InvalidDatasetError("non-finite JSON number")


def read_json(text):
    return json.loads(text, object_pairs_hook=_object, parse_constant=_bad_constant)


def decode(value, hint, name):
    origin, args = get_origin(hint), get_args(hint)
    if value is None and type(None) in args:
        return None
    if origin in (Union, UnionType):
        nonnull = [a for a in args if a is not type(None)]
        if len(nonnull) == 1:
            return decode(value, nonnull[0], name)
    if hint is datetime:
        if not isinstance(value, str):
            raise InvalidDatasetError(f"{name}: expected ISO timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidDatasetError(f"{name}: timestamp must be timezone-aware")
        return parsed.astimezone(timezone.utc)
    if hint is date:
        if not isinstance(value, str):
            raise InvalidDatasetError(f"{name}: expected ISO date")
        return date.fromisoformat(value)
    if hint is time:
        if not isinstance(value, str):
            raise InvalidDatasetError(f"{name}: expected local HH:MM time")
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is not None:
            raise InvalidDatasetError(f"{name}: expected local session time")
        return parsed
    if hint is Decimal:
        if not isinstance(value, str):
            raise InvalidDatasetError(f"{name}: Decimal must serialize as a string")
        parsed = Decimal(value)
        if not parsed.is_finite():
            raise InvalidDatasetError(f"{name}: Decimal must be finite")
        return parsed
    if origin in (tuple, frozenset):
        if not isinstance(value, list):
            raise InvalidDatasetError(f"{name}: expected JSON array")
        converted = [decode(item, args[0], name) for item in value]
        return tuple(converted) if origin is tuple else frozenset(converted)
    if hint in (Wall, NegativeRegion, OptionRow):
        return decode_record(value, hint)
    if origin is Literal:
        return value  # The immutable model validates literals and primitive types.
    return value


def decode_record(record, cls):
    if not isinstance(record, dict):
        raise InvalidDatasetError("record must be a JSON object")
    hints = get_type_hints(cls)
    allowed = {f.name for f in fields(cls)}
    if record.keys() - allowed:
        raise InvalidDatasetError("unknown record fields")
    required = {f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING}
    if required - record.keys():
        raise InvalidDatasetError("missing required record fields: " + ", ".join(sorted(required - record.keys())))
    try:
        return cls(**{name: decode(value, hints[name], name) for name, value in record.items()})
    except InvalidDatasetError:
        raise
    except (ValueError, TypeError, InvalidOperation, OverflowError) as exc:
        raise InvalidDatasetError(f"invalid {cls.__name__} field encoding") from exc


def _load_lines(path, cls=None):
    result = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = read_json(line)
                if cls is None:
                    if not isinstance(record, dict) or record.get("type") not in EVENT_TYPES:
                        raise InvalidDatasetError("missing or unknown event type")
                    kind = EVENT_TYPES[record["type"]]
                    record = {k: v for k, v in record.items() if k != "type"}
                else:
                    kind = cls
                result.append(decode_record(record, kind))
            except (InvalidDatasetError, ValueError, TypeError) as exc:
                # Do not echo record bytes or metadata values into diagnostics.
                detail = str(exc) if isinstance(exc, InvalidDatasetError) else "malformed JSON"
                raise InvalidDatasetError(f"{path.name}:{line_number}: {detail}") from exc
    return result


def _file_hash(path):
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(path):
    if not path.exists():
        return {}, (), (), None
    raw = read_json(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.keys() - {"sources", "limitations", "macro_coverage_dates", "macro_schedule_available_at"}:
        raise InvalidDatasetError("metadata: unknown fields; credentials must never enter datasets")
    sources = raw.get("sources", [])
    limitations = raw.get("limitations", [])
    if not isinstance(sources, list) or not isinstance(limitations, list) or any(type(s) is not str for s in limitations):
        raise InvalidDatasetError("metadata: sources and limitations must be arrays")
    result = {}
    for source in sources:
        if not isinstance(source, dict) or source.keys() - SOURCE_FIELDS:
            raise InvalidDatasetError("metadata: unknown source fields; credentials are forbidden")
        sid = source.get("source_id")
        if not isinstance(sid, str) or not sid.strip() or sid in result:
            raise InvalidDatasetError("metadata: missing or duplicate source ID")
        if source.get("classification", "unknown") not in {"historical", "synthetic", "indicative", "unknown"}:
            raise InvalidDatasetError("metadata: invalid classification")
        if type(source.get("availability_verified", False)) is not bool:
            raise InvalidDatasetError("metadata: availability_verified must be boolean")
        if "interval_seconds" in source and (type(source["interval_seconds"]) is not int or source["interval_seconds"] <= 0):
            raise InvalidDatasetError("metadata: interval_seconds must be positive integer")
        if "limitations" in source and (not isinstance(source["limitations"], list) or
                                         any(type(v) is not str for v in source["limitations"])):
            raise InvalidDatasetError("metadata: invalid source limitations")
        for key in SOURCE_FIELDS - {"limitations", "availability_verified", "interval_seconds", "expiry_days"}:
            if key in source and (not isinstance(source[key], str) or not source[key].strip()):
                raise InvalidDatasetError("metadata: source text fields must be nonempty strings")
        if "expiry_days" in source and (not isinstance(source["expiry_days"], list) or
                                         any(type(v) is not int or v < 0 for v in source["expiry_days"])):
            raise InvalidDatasetError("metadata: invalid expiry universe")
        # URL fields intentionally exclude credentials, tokens and query strings.
        for key in ("homepage", "documentation_url"):
            if key in source:
                url = urlsplit(source[key])
                if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
                    raise InvalidDatasetError("metadata: public credential-free URLs required")
        if "ingested_at" in source:
            decode(source["ingested_at"], datetime, "ingested_at")
        result[sid] = source
    dates = raw.get("macro_coverage_dates", [])
    if not isinstance(dates, list):
        raise InvalidDatasetError("metadata: macro_coverage_dates must be array")
    publication = (decode(raw["macro_schedule_available_at"], datetime, "macro_schedule_available_at")
                   if "macro_schedule_available_at" in raw else None)
    return result, tuple(limitations), tuple(decode(d, date, "macro_coverage_dates") for d in dates), publication


def _dedupe(rows, key):
    unique = {}
    for row in rows:
        identity = key(row)
        if identity in unique and unique[identity] != row:
            raise InvalidDatasetError("conflicting duplicate record")
        unique[identity] = row
    return list(unique.values())


def load_dataset(path: Path, *, as_of: datetime | None = None) -> Dataset:
    """Load normalized files only; validate before applying an availability cutoff."""
    path = Path(path)
    if as_of is not None:
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise InvalidDatasetError("as_of must be timezone-aware")
        as_of = as_of.astimezone(timezone.utc)
    directory = path.is_dir()
    event_path = path / "events.jsonl" if directory else path
    metadata_path = path / "metadata.json" if directory else path.with_suffix(".metadata.json")
    try:
        events = _dedupe(_load_lines(event_path), lambda e: (e.source_id, e.sequence))
        sources, limitations, macro_dates, macro_publication = _metadata(metadata_path)
        paths = [event_path] + ([metadata_path] if metadata_path.exists() else [])
        tables = {}
        for name, cls in TABLE_TYPES.items():
            table_path = path / f"{name}.jsonl"
            tables[name] = []
            if directory and table_path.exists():
                paths.append(table_path)
                rows = _load_lines(table_path, cls)
                key = {"sessions": lambda r: r.session_date,
                       "macro_releases": lambda r: r.event_id,
                       "margin_points": lambda r: (r.contract, r.available_at)}[name]
                tables[name] = _dedupe(rows, key)
        experiment_gex_identity(Dataset(events=tuple(events), source_metadata=sources))
        if as_of is not None:
            events = [e for e in events if e.available_at <= as_of]
            for name in tables:
                attr = "available_at" if name == "margin_points" else "schedule_available_at"
                tables[name] = [r for r in tables[name] if getattr(r, attr) <= as_of]
            if macro_publication is None or macro_publication > as_of:
                macro_dates = ()
        events.sort(key=lambda e: (e.available_at, e.event_at, e.source_id, e.sequence))
        tables["sessions"].sort(key=lambda r: r.open_at)
        tables["macro_releases"].sort(key=lambda r: (r.scheduled_at, r.event_id))
        tables["margin_points"].sort(key=lambda r: (r.available_at, r.contract))
        for event in events:
            sources.setdefault(event.source_id, {"source_id": event.source_id, "classification": "unknown",
                                                 "limitations": ["No source provenance established."]})
        dataset = Dataset(events=tuple(events), source_metadata=sources,
                          limitations=limitations, macro_coverage_dates=macro_dates,
                          macro_schedule_available_at=macro_publication,
                          input_hashes={p.name: _file_hash(p) for p in paths},
                          **{name: tuple(rows) for name, rows in tables.items()})
        from .coverage import audit_dataset
        return replace(dataset, coverage=audit_dataset(dataset, RULE_IDS))
    except InvalidDatasetError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        raise InvalidDatasetError("cannot read normalized dataset: " + type(exc).__name__) from exc
