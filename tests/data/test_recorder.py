"""Real fixture streams exercise durable recorder behavior without any provider."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from gamma_bot.data.io import decode_record, load_dataset
from gamma_bot.data.models import CoverageReport, MesBar, InvalidDatasetError
from conftest import bar_record

NOW = datetime(2026, 6, 15, 14, tzinfo=timezone.utc)
STOP = NOW + timedelta(minutes=1)


def bar(sequence=1, **changes):
    row = bar_record(sequence=sequence, **changes)
    row.pop('type')
    return decode_record(row, MesBar)


class FixtureAdapter:
    capabilities = CoverageReport(synthetic=True, classification='synthetic')

    def __init__(self, *connections):
        self.connections = iter(connections)
        self.calls = 0

    def events(self):
        self.calls += 1
        for item in next(self.connections, ()):
            if isinstance(item, Exception):
                raise item
            yield item


@pytest.fixture
def recorder(monkeypatch):
    import gamma_bot.data.recorder as module
    monkeypatch.setattr(module, '_now', lambda: NOW)
    # Only the real-time waiting boundary is replaced, never recorder logic/files.
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    return module


def paths(tmp_path):
    return tmp_path/'events.jsonl', tmp_path/'checkpoint.json'


def test_reconnect_preserves_unique_observations_and_explicit_gap(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    adapter = FixtureAdapter([bar(), ConnectionError('token=secret')], [bar(), bar(2)])
    report = recorder.record(adapter, output, checkpoint, stop_at=STOP)
    dataset = load_dataset(output)
    assert [e.sequence for e in dataset.events] == [1, 2]
    assert all(e.available_at == NOW for e in dataset.events)
    assert report.source_gaps['synthetic']
    assert any('disconnect' in item for item in dataset.limitations)
    assert report.synthetic and not report.real
    assert 'secret' not in ''.join(p.read_text() for p in tmp_path.iterdir())


def test_restart_rebuilds_identity_from_output_even_when_checkpoint_is_stale(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    recorder.record(FixtureAdapter([bar()]), output, checkpoint, stop_at=STOP)
    old_checkpoint = checkpoint.read_bytes()
    prefix = output.read_bytes()
    recorder.record(FixtureAdapter([bar(), bar(2)]), output, checkpoint, stop_at=STOP)
    checkpoint.write_bytes(old_checkpoint)
    recorder.record(FixtureAdapter([bar(), bar(2), bar(3)]), output, checkpoint, stop_at=STOP)
    assert output.read_bytes().startswith(prefix)
    assert [e.sequence for e in load_dataset(output).events] == [1, 2, 3]
    assert json.loads(checkpoint.read_text())['offset'] == len(output.read_bytes())


def test_conflicting_sequence_is_rejected_without_overwriting_observation(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    recorder.record(FixtureAdapter([bar()]), output, checkpoint, stop_at=STOP)
    original = output.read_bytes()
    with pytest.raises(InvalidDatasetError, match='conflicting'):
        recorder.record(FixtureAdapter([bar(close_ticks=24001)]), output, checkpoint, stop_at=STOP)
    assert output.read_bytes() == original


def test_late_and_out_of_order_observations_never_gain_historical_availability(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    newer = bar(2, event_at='2026-06-15T13:33:00Z', available_at='2026-06-15T13:33:01Z')
    recorder.record(FixtureAdapter([newer, bar()]), output, checkpoint, stop_at=STOP)
    events = load_dataset(output).events
    assert {e.available_at for e in events} == {NOW}
    assert load_dataset(output, as_of=NOW-timedelta(seconds=1)).events == ()
    assert {e.event_at.minute for e in events} == {31, 33}


def test_skipped_sequences_and_event_cadence_remain_visible_after_restart(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    later = bar(4, event_at='2026-06-15T13:34:00Z', available_at='2026-06-15T13:34:01Z')
    first = recorder.record(FixtureAdapter([bar(), later]), output, checkpoint, stop_at=STOP)
    restarted = recorder.record(FixtureAdapter([bar(), later]), output, checkpoint, stop_at=STOP)
    assert restarted.source_gaps == first.source_gaps
    assert any('sequence' in item for item in load_dataset(output).limitations)


def test_rate_limit_and_disconnect_retries_are_bounded_and_redacted(recorder, tmp_path, capsys):
    output, checkpoint = paths(tmp_path)
    adapter = FixtureAdapter(*[[recorder.RateLimited('api_key=TOPSECRET')] for _ in range(20)])
    report = recorder.record(adapter, output, checkpoint, stop_at=STOP)
    assert 1 < adapter.calls <= 4
    assert any('retry_exhausted' in item for item in report.limitations)
    assert not load_dataset(output).events
    assert 'TOPSECRET' not in str(report)+capsys.readouterr().out+capsys.readouterr().err
    assert 'TOPSECRET' not in ''.join(p.read_text() for p in tmp_path.iterdir())


def test_unexpected_adapter_errors_do_not_expose_exception_message(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    adapter = FixtureAdapter([ValueError('password=TOPSECRET')])
    with pytest.raises(recorder.RecorderError) as failure:
        recorder.record(adapter, output, checkpoint, stop_at=STOP)
    import traceback
    assert 'TOPSECRET' not in ''.join(traceback.format_exception(failure.value))
    assert 'TOPSECRET' not in ''.join(p.read_text() for p in tmp_path.iterdir())


@pytest.mark.parametrize('tail', [b'{"type":', b'\xff\xfe', b'{bad}\n'])
def test_torn_final_record_is_quarantined_before_prefix_repair(recorder, tmp_path, tail):
    output, checkpoint = paths(tmp_path)
    recorder.record(FixtureAdapter([bar()]), output, checkpoint, stop_at=STOP)
    prefix = output.read_bytes()
    output.write_bytes(prefix+tail)
    recorder.record(FixtureAdapter([bar(), bar(2)]), output, checkpoint, stop_at=STOP)
    assert output.read_bytes().startswith(prefix)
    assert [e.sequence for e in load_dataset(output).events] == [1, 2]
    assert any(p.read_bytes() == prefix+tail for p in tmp_path.glob('events.jsonl.quarantine-*'))
    assert any('recovery' in item for item in load_dataset(output).limitations)


def test_interior_corruption_fails_without_modifying_existing_bytes(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    recorder.record(FixtureAdapter([bar()]), output, checkpoint, stop_at=STOP)
    damaged = b'{bad}\n'+output.read_bytes()
    output.write_bytes(damaged)
    before = checkpoint.read_bytes()
    with pytest.raises(InvalidDatasetError):
        recorder.record(FixtureAdapter([bar(2)]), output, checkpoint, stop_at=STOP)
    assert output.read_bytes() == damaged and checkpoint.read_bytes() == before


def test_corrupt_checkpoint_is_preserved_and_output_recovers_dedupe(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    recorder.record(FixtureAdapter([bar()]), output, checkpoint, stop_at=STOP)
    checkpoint.write_bytes(b'{torn-checkpoint')
    recorder.record(FixtureAdapter([bar(), bar(2)]), output, checkpoint, stop_at=STOP)
    assert [e.sequence for e in load_dataset(output).events] == [1, 2]
    assert any(p.read_bytes() == b'{torn-checkpoint' for p in tmp_path.glob('checkpoint.json.quarantine-*'))
    assert any('checkpoint_recovery' in item for item in load_dataset(output).limitations)


def test_valid_unterminated_record_is_separated_before_append(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    recorder.record(FixtureAdapter([bar()]), output, checkpoint, stop_at=STOP)
    prefix = output.read_bytes().rstrip(b'\n')
    output.write_bytes(prefix)
    recorder.record(FixtureAdapter([bar(2)]), output, checkpoint, stop_at=STOP)
    assert output.read_bytes().startswith(prefix+b'\n')
    assert len(load_dataset(output).events) == 2


def test_stop_boundary_does_not_request_events_or_backdate_future_data(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    adapter = FixtureAdapter([bar()])
    recorder.record(adapter, output, checkpoint, stop_at=NOW)
    assert adapter.calls == 0
    assert output.read_bytes() == b''
    future = replace(bar(), available_at=STOP+timedelta(seconds=1))
    recorder.record(FixtureAdapter([future]), output, checkpoint, stop_at=STOP)
    assert not load_dataset(output).events


def test_output_checkpoint_alias_rejected_before_any_write(recorder, tmp_path):
    output, _ = paths(tmp_path)
    output.write_bytes(b'preserve')
    with pytest.raises(InvalidDatasetError):
        recorder.record(FixtureAdapter([bar()]), output, output, stop_at=STOP)
    assert output.read_bytes() == b'preserve'


@pytest.mark.parametrize('error_type', [InvalidDatasetError, RuntimeError])
def test_adapter_domain_errors_are_redacted_like_other_provider_failures(recorder, tmp_path, error_type):
    output, checkpoint = paths(tmp_path)
    adapter = FixtureAdapter([error_type('provider token=TOPSECRET')])
    with pytest.raises(recorder.RecorderError) as failure:
        recorder.record(adapter, output, checkpoint, stop_at=STOP)
    assert 'TOPSECRET' not in str(failure.value)


def test_adapter_cleanup_failure_is_redacted_and_durable_event_is_retained(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)

    class CleanupFailure(FixtureAdapter):
        def events(self):
            try:
                yield bar()
            finally:
                raise RuntimeError('provider token=TOPSECRET')

    with pytest.raises(recorder.RecorderError) as failure:
        recorder.record(CleanupFailure(), output, checkpoint, stop_at=STOP)
    assert 'TOPSECRET' not in str(failure.value)
    assert len(load_dataset(output).events) == 1


def test_capabilities_cannot_upgrade_recorded_input_to_full_historical_coverage(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    adapter = FixtureAdapter([bar()])
    adapter.capabilities = CoverageReport(real=True, unknown=False, classification='full')
    report = recorder.record(adapter, output, checkpoint, stop_at=STOP)
    assert not report.real and report.unknown and report.classification == 'incomplete'
    assert report.missing_rules and not report.intersection_dates


def test_reconnect_new_data_uses_later_receipt_and_does_not_backdate_duplicates(recorder, tmp_path, monkeypatch):
    output, checkpoint = paths(tmp_path)
    current = [NOW]
    monkeypatch.setattr(recorder, '_now', lambda: current[0])

    class LaterReconnect(FixtureAdapter):
        def events(self):
            self.calls += 1
            if self.calls == 1:
                yield bar()
                raise ConnectionError('private-provider-error')
            current[0] = NOW+timedelta(seconds=30)
            yield bar()
            yield bar(2)
            yield bar(3)

    report = recorder.record(LaterReconnect(), output, checkpoint, stop_at=STOP)
    events = load_dataset(output).events
    assert [(e.sequence, e.available_at) for e in events] == [
        (1, NOW), (2, NOW+timedelta(seconds=30)), (3, NOW+timedelta(seconds=30))]
    assert len(load_dataset(output, as_of=NOW+timedelta(seconds=10)).events) == 1
    assert (NOW, NOW+timedelta(seconds=30)) in report.source_gaps['synthetic']
    checkpoint_gaps = json.loads(checkpoint.read_text())['gaps']
    # A recovered stream must not keep manufacturing a disconnect for every row.
    assert len([g for g in checkpoint_gaps if g['reason'] == 'disconnect']) == 2


def test_existing_historical_sidecar_cannot_certify_unverified_forward_adapter(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    metadata = output.with_suffix('.metadata.json')
    output.write_bytes(b'')
    metadata.write_text(json.dumps({'sources': [{'source_id': 'synthetic',
        'classification': 'historical', 'availability_verified': True}]}))
    report = recorder.record(FixtureAdapter([bar()]), output, checkpoint, stop_at=STOP)
    assert not report.real and report.classification != 'full'


def test_capabilities_exception_cannot_expose_secrets_or_touch_existing_output(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    output.write_bytes(b'')

    class BadCapabilities:
        @property
        def capabilities(self):
            raise RuntimeError('api_key=TOPSECRET')

    with pytest.raises(recorder.RecorderError) as failure:
        recorder.record(BadCapabilities(), output, checkpoint, stop_at=STOP)
    assert 'TOPSECRET' not in str(failure.value)
    assert output.read_bytes() == b'' and not checkpoint.exists()


def test_redacted_failure_does_not_return_raw_exception_in_context(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    adapter = FixtureAdapter([RuntimeError('provider token=TOPSECRET')])
    with pytest.raises(recorder.RecorderError) as failure:
        recorder.record(adapter, output, checkpoint, stop_at=STOP)
    assert failure.value.__context__ is None


def test_checkpoint_cannot_alias_metadata_or_existing_event_hardlink(recorder, tmp_path):
    output, checkpoint = paths(tmp_path)
    output.write_bytes(b'preserve-existing')
    checkpoint.hardlink_to(output)
    for target in (checkpoint, output.with_suffix('.metadata.json')):
        with pytest.raises(InvalidDatasetError):
            recorder.record(FixtureAdapter([bar()]), output, target, stop_at=STOP)
    assert output.read_bytes() == checkpoint.read_bytes() == b'preserve-existing'
