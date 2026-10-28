"""Single-writer foreground recording; no network provider or service registration."""
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from .adapters import SourceAdapter
from .io import EVENT_TYPES, decode_record, load_dataset, read_json
from .models import CoverageReport, Event, InvalidDatasetError
from .provenance import to_jsonable


class RecorderError(ValueError):
    """A sanitized adapter failure; raw provider exception text is never exposed."""


class RateLimited(ConnectionError):
    """Transient throttling; generic bounded backoff, not a provider policy."""


def _now():
    return datetime.now(timezone.utc)


def _source_call(operation):
    """Sanitize at the source boundary, without retaining raw exception context."""
    try:
        return operation()
    except StopIteration:
        raise
    except RateLimited:
        error = RateLimited('source rate limited')
    except (ConnectionError, TimeoutError):
        error = ConnectionError('source disconnected')
    except Exception:
        error = RecorderError('recorder: adapter failed; provider details suppressed')
    raise error


def _json(value):
    return (json.dumps(to_jsonable(value), sort_keys=True, allow_nan=False,
                       separators=(',', ':'))+'\n').encode('utf-8')


def _row(event):
    return dict(to_jsonable(event), type=next(k for k, cls in EVENT_TYPES.items() if type(event) is cls))


def _identity_hash(event):
    row = _row(event)
    # Arrival floors may change on redelivery; source content must remain stable.
    row.pop('available_at')
    return sha256(_json(row)).hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic(path, value):
    descriptor, name = tempfile.mkstemp(prefix=path.name+'.tmp-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _quarantine(path):
    """Retain the complete original, fsynced before any repair/replacement."""
    descriptor, name = tempfile.mkstemp(prefix=path.name+'.quarantine-', dir=path.parent)
    with os.fdopen(descriptor, 'wb') as target, path.open('rb') as source:
        shutil.copyfileobj(source, target)
        target.flush()
        os.fsync(target.fileno())
    _sync_directory(path.parent)
    return name


def _scan(path):
    """Validate all complete observations before permitting any prefix repair."""
    events, seen, truncate = [], {}, None
    if not path.exists():
        return events, seen, truncate
    size = path.stat().st_size
    with path.open('rb') as handle:
        while line := handle.readline():
            offset = handle.tell()-len(line)
            if not line.strip():
                continue
            try:
                row = read_json(line.decode('utf-8'))
                kind = row.pop('type')
                event = decode_record(row, EVENT_TYPES[kind])
            except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
                if handle.tell() != size:
                    raise InvalidDatasetError('recorder: corrupt interior observation; bytes preserved') from None
                truncate = offset
                break
            key = (event.source_id, event.sequence)
            digest = _identity_hash(event)
            if key in seen and seen[key] != digest:
                raise InvalidDatasetError('recorder: conflicting existing sequence; bytes preserved')
            seen[key] = digest
            events.append(event)
    return events, seen, truncate


def _checkpoint_gaps(path, output):
    if not path.exists():
        return [], False
    try:
        data = read_json(path.read_text(encoding='utf-8'))
        if set(data) != {'version', 'offset', 'sha256', 'gaps'} or data['version'] != 1:
            raise ValueError
        offset = data['offset']
        if type(offset) is not int or offset < 0 or not isinstance(data['gaps'], list):
            raise ValueError
        digest = sha256()
        with output.open('rb') as handle:
            remaining = offset
            while remaining:
                block = handle.read(min(65536, remaining))
                if not block:
                    raise ValueError
                digest.update(block)
                remaining -= len(block)
        if digest.hexdigest() != data['sha256']:
            raise ValueError
        gaps = []
        for gap in data['gaps']:
            if set(gap) != {'source_id', 'start', 'end', 'reason'} or gap['reason'] not in {
                    'disconnect', 'rate_limit', 'sequence', 'restart', 'recovery'}:
                raise ValueError
            start, end = (datetime.fromisoformat(gap[k].replace('Z', '+00:00')) for k in ('start', 'end'))
            if not start.tzinfo or not end.tzinfo or start > end or not isinstance(gap['source_id'], str):
                raise ValueError
            gaps.append(dict(gap, start=start, end=end))
        return gaps, False
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, FileNotFoundError):
        _quarantine(path)
        return [], True


def record(adapter: SourceAdapter, output: Path, checkpoint: Path, *, stop_at: datetime) -> CoverageReport:
    """Append until stream exhaustion, stop time, or three total reconnect retries.

    A single process owns these paths. The synchronous adapter must bound reads;
    this function cannot interrupt a blocked provider iterator. No resume cursor
    is trusted: identities/hashes are rebuilt from output, so adapters may replay.
    Newly observed events retain event time and gain an arrival availability floor.
    """
    if not isinstance(stop_at, datetime) or stop_at.tzinfo is None or stop_at.utcoffset() is None:
        raise InvalidDatasetError('recorder: stop_at must be timezone-aware')
    stop_at = stop_at.astimezone(timezone.utc)
    output, checkpoint = Path(output), Path(checkpoint)
    metadata = output.with_suffix('.metadata.json')
    targets = (output, checkpoint, metadata)
    for index, target in enumerate(targets):
        for other in targets[index+1:]:
            if target.resolve() == other.resolve() or (target.exists() and other.exists() and target.samefile(other)):
                raise InvalidDatasetError('recorder: event, metadata and checkpoint paths must differ')
    events, seen, truncate = _scan(output)
    # Validate existing sidecar before modifying any event data.
    if metadata.exists():
        from .io import _metadata
        _metadata(metadata)
    try:
        capabilities = adapter.capabilities
        if not isinstance(capabilities, CoverageReport):
            raise TypeError
    except Exception:
        capabilities = None
    if capabilities is None:
        raise RecorderError('recorder: invalid adapter capabilities')
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
    warnings = []
    if truncate is not None:
        _quarantine(output)
        with output.open('r+b') as handle:
            handle.truncate(truncate)
            handle.flush()
            os.fsync(handle.fileno())
        warnings.append('Recorder recovery: trailing bytes retained in recoverable quarantine; continuity is unverified.')
    if not output.exists():
        with output.open('xb') as handle:
            handle.flush()
            os.fsync(handle.fileno())
        _sync_directory(output.parent)
    gaps, checkpoint_recovered = _checkpoint_gaps(checkpoint, output)
    if checkpoint_recovered:
        warnings.append('Recorder checkpoint_recovery: original checkpoint quarantined; continuity is unverified.')
    dataset = load_dataset(output)
    warnings = list(dict.fromkeys((*dataset.limitations, *warnings)))
    sources = {key: to_jsonable(value) for key, value in dataset.source_metadata.items()}
    latest = {}
    for event in events:
        previous = latest.get(event.source_id)
        if previous is None or event.sequence > previous.sequence:
            latest[event.source_id] = event
    digest = sha256()
    with output.open('rb') as handle:
        for block in iter(lambda: handle.read(65536), b''):
            digest.update(block)
    offset = output.stat().st_size

    def gap(source, start, end, reason):
        item = dict(source_id=source, start=start, end=max(start, end), reason=reason)
        if item not in gaps:
            gaps.append(item)
        warning = f'Recorder gap {reason}: source {source}, {to_jsonable(start)} to {to_jsonable(max(start, end))}; continuity is unverified.'
        if warning not in warnings:
            warnings.append(warning)

    for source, event in latest.items():
        if _now() > event.available_at:
            gap(source, event.available_at, min(_now(), stop_at), 'restart')

    def save():
        # Persist warnings before checkpoint so a crash never hides recorded gaps.
        original = read_json(metadata.read_text()) if metadata.exists() else {}
        _atomic(metadata, dict(original, sources=list(sources.values()), limitations=warnings))
        _atomic(checkpoint, dict(version=1, offset=offset, sha256=digest.hexdigest(), gaps=gaps))

    save()
    retries, pending = 0, {}
    with output.open('ab') as handle:
        # A complete final JSON value without newline is not a corrupt record.
        if offset:
            with output.open('rb') as tail:
                tail.seek(-1, os.SEEK_END)
                newline_needed = tail.read(1) != b'\n'
            if newline_needed:
                handle.write(b'\n')
                handle.flush()
                os.fsync(handle.fileno())
                digest.update(b'\n')
                offset += 1
        while _now() < stop_at:
            stream = None
            failure = None
            try:
                stream = _source_call(lambda: iter(adapter.events()))
                while _now() < stop_at:
                    try:
                        event = _source_call(lambda: next(stream))
                    except StopIteration:
                        break
                    observed = _now()
                    if observed >= stop_at:
                        break
                    if type(event) not in EVENT_TYPES.values():
                        raise RecorderError('recorder: adapter yielded an invalid event')
                    key = event.source_id, event.sequence
                    content_hash = _identity_hash(event)
                    if key in seen:
                        if seen[key] != content_hash:
                            raise InvalidDatasetError('recorder: conflicting duplicate sequence')
                        continue
                    available = max(event.available_at, observed)
                    if available > stop_at:
                        warnings.append('Recorder skipped future availability beyond stop_at; continuity is unverified.')
                        continue
                    event = replace(event, available_at=available)
                    previous = latest.get(event.source_id)
                    if previous is not None:
                        if event.sequence > previous.sequence+1:
                            gap(event.source_id, previous.available_at, available, 'sequence')
                        if event.source_id in pending:
                            gap(event.source_id, previous.available_at, available, pending.pop(event.source_id))
                    sources.setdefault(event.source_id, dict(source_id=event.source_id,
                        classification='synthetic' if capabilities.synthetic else 'indicative' if capabilities.indicative else 'unknown',
                        availability_verified=False,
                        timestamp_semantics='Forward observation availability is floored at recorder receipt; no historical availability attestation.'))
                    # Existing metadata cannot attest to an unrelated forward adapter.
                    sources[event.source_id]['availability_verified'] = False
                    if sources[event.source_id].get('classification') == 'historical':
                        sources[event.source_id]['classification'] = 'unknown'
                        warning = 'Recorder source availability is unverified; prior historical metadata does not certify this adapter.'
                        if warning not in warnings:
                            warnings.append(warning)
                    raw = _json(_row(event))
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                    digest.update(raw)
                    offset += len(raw)
                    seen[key] = content_hash
                    if previous is None or event.sequence > previous.sequence:
                        latest[event.source_id] = event
                    save()
                break  # Normal exhaustion is a completed bounded stream.
            except (ConnectionError, TimeoutError) as exc:
                reason = 'rate_limit' if isinstance(exc, RateLimited) else 'disconnect'
                warnings.append(f'Recorder {reason}: source stream interrupted; continuity is unverified.')
                for source, previous in latest.items():
                    pending[source] = reason
                    gap(source, previous.available_at, min(_now(), stop_at), reason)
                save()
                if retries >= 3:
                    warnings.append('Recorder retry_exhausted: bounded reconnect attempts ended; continuity is unverified.')
                    break
                delay = min(0.1 * 2**retries, max(0, (stop_at-_now()).total_seconds()))
                retries += 1
                time.sleep(delay)
            except (InvalidDatasetError, RecorderError):
                raise
            except Exception:
                failure = 'recorder: adapter failed; provider details suppressed'
            finally:
                if stream is not None and hasattr(stream, 'close'):
                    try:
                        stream.close()
                    except Exception:
                        failure = 'recorder: adapter cleanup failed; provider details suppressed'
                    if failure:
                        raise RecorderError(failure) from None
            if failure:
                # Raise outside the except suite to suppress secret-bearing context.
                raise RecorderError(failure) from None
    save()
    report = load_dataset(output).coverage
    combined = {source: list(values) for source, values in report.source_gaps.items()}
    for item in gaps:
        combined.setdefault(item['source_id'], []).append((item['start'], item['end']))
    return replace(report, source_gaps={source: tuple(sorted(set(values))) for source, values in combined.items()})
