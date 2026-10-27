import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from conftest import bar_record, gex_record, point_record, quote_record


@pytest.mark.parametrize("record", [
    point_record(value="NaN"), point_record(value="Infinity"), point_record(value="oops"),
    point_record(value=18.25), bar_record(volume=-1), bar_record(volume=True),
    quote_record(bid_ticks=24002, ask_ticks=24001), quote_record(bid_size=-1),
    bar_record(high_ticks=23999), bar_record(low_ticks=24003),
    bar_record(contract="ESM6"), bar_record(contract="MES"),
    bar_record(event_at="2026-06-15T13:31:00"),
    bar_record(available_at="2026-06-15T13:30:59Z"),
    bar_record(start_at="2026-06-15T13:32:00Z"),
    gex_record(oi_published_at="2026-06-15T13:31:02Z"),
    bar_record(unknown_required_field=1),
])
def test_rejects_corrupt_records(dataset_writer, record):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.models import InvalidDatasetError
    with pytest.raises(InvalidDatasetError):
        load_dataset(dataset_writer([record]))


def test_decodes_typed_values_preserves_unknown_liquidity_and_is_immutable(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.models import GexSnapshot, Quote
    from dataclasses import FrozenInstanceError
    dataset = load_dataset(dataset_writer([gex_record(), quote_record()]))
    gex = next(e for e in dataset.events if isinstance(e, GexSnapshot))
    quote = next(e for e in dataset.events if isinstance(e, Quote))
    assert gex.spot == Decimal("600")
    assert gex.negative_regions == () and gex.profile_available
    assert quote.bid_size is None and quote.ask_size is None
    assert quote.available_at.tzinfo == timezone.utc
    with pytest.raises(FrozenInstanceError):
        quote.bid_ticks = 1
    with pytest.raises(TypeError):
        dataset.source_metadata["new"] = {}


def test_deduplicates_exact_events_and_rejects_conflicting_sequences(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.models import InvalidDatasetError
    assert len(load_dataset(dataset_writer([bar_record(), bar_record()])).events) == 1
    with pytest.raises(InvalidDatasetError, match="conflict"):
        load_dataset(dataset_writer([bar_record(), bar_record(close_ticks=24001)]))


def test_as_of_filters_by_availability_and_sorts_deterministically(dataset_writer):
    from gamma_bot.data.io import load_dataset
    dataset = load_dataset(dataset_writer([
        quote_record(available_at="2026-06-15T13:32:00Z"), bar_record(),
    ]), as_of=datetime(2026, 6, 15, 13, 31, 1, tzinfo=timezone.utc))
    assert len(dataset.events) == 1
    assert dataset.events[0].sequence == 1


def test_unknown_event_only_provenance_does_not_become_real(tmp_path):
    from gamma_bot.data.io import load_dataset
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(bar_record()) + "\n")
    dataset = load_dataset(path)
    assert dataset.sessions == () and dataset.margin_points == ()
    assert dataset.coverage.real is False
    assert dataset.coverage.unknown is True


def test_options_validate_finite_greeks_and_oi_availability(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.models import InvalidDatasetError, OptionSnapshot
    row = dict(type="option_snapshot", source_id="synthetic", sequence=8,
               event_at="2026-06-15T13:31:00Z", available_at="2026-06-15T13:31:01Z",
               underlying="SPY", spot="600", annual_rate=0.04, dividend_yield=0.01,
               rate_provenance="scenario", dividend_provenance="scenario",
               rows=[dict(kind="call", strike="600", expires_at="2026-06-19T20:00:00Z",
                          multiplier=100, oi=10, oi_published_at="2026-06-15T10:00:00Z",
                          gamma=0.01, iv=0.2)])
    assert isinstance(load_dataset(dataset_writer([row])).events[0], OptionSnapshot)
    row["rows"][0]["gamma"] = float("nan")
    with pytest.raises(InvalidDatasetError):
        load_dataset(dataset_writer([row]))


def test_invalid_calendar_and_margin_are_not_ignored(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.models import InvalidDatasetError
    with pytest.raises(InvalidDatasetError):
        load_dataset(dataset_writer([bar_record()], sessions=[{
            "session_date": "2026-06-15", "open_at": "2026-06-15T13:30:00Z",
            "close_at": "2026-06-15T13:29:00Z", "schedule_available_at": "2026-06-14T00:00:00Z"}]))
    with pytest.raises(InvalidDatasetError):
        load_dataset(dataset_writer([bar_record()], margin_points=[{
            "contract": "MESM6", "available_at": "2026-06-14T00:00:00Z",
            "initial_cents": -1, "maintenance_cents": 1, "provenance": "historical"}]))


def test_session_date_cannot_disagree_with_new_york_open(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.models import InvalidDatasetError
    with pytest.raises(InvalidDatasetError):
        load_dataset(dataset_writer([bar_record()], sessions=[{
            "session_date": "2026-06-14", "open_at": "2026-06-15T13:30:00Z",
            "close_at": "2026-06-15T20:00:00Z", "schedule_available_at": "2026-06-14T00:00:00Z"}]))


def test_invalid_as_of_is_reported_as_invalid_dataset(prices_only_path):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.models import InvalidDatasetError
    with pytest.raises(InvalidDatasetError):
        load_dataset(prices_only_path, as_of="2026-06-15T13:31:01Z")


def test_import_memory_is_bounded_by_records_and_lines_not_whole_input(tmp_path):
    """Blank padding must not become a second in-memory copy of market history."""
    import hashlib
    import tracemalloc
    from gamma_bot.data.io import load_dataset

    path = tmp_path / "padded.jsonl"
    padding = b" " * 65535 + b"\n"
    record = (json.dumps(bar_record()) + "\n").encode()
    expected_hash = hashlib.sha256()
    with path.open("wb") as handle:
        for _ in range(256):  # 16 MiB, but only one retained market record.
            handle.write(padding)
            expected_hash.update(padding)
        handle.write(record)
        expected_hash.update(record)
    tracemalloc.start()
    try:
        dataset = load_dataset(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(dataset.events) == 1
    assert dataset.events[0].close_ticks == 24002
    assert dataset.input_hashes["padded.jsonl"] == expected_hash.hexdigest()
    assert peak < 4 * 1024 * 1024, f"Importer allocated {peak} bytes for one retained record"
