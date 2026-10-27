from conftest import bar_record, gex_record, point_record, quote_record
import pytest


def test_price_only_input_does_not_claim_full_coverage(prices_only_path):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.coverage import audit_dataset
    report = audit_dataset(load_dataset(prices_only_path), frozenset({"gex", "cvd", "vix"}))
    assert {"gex", "cvd", "vix"} <= report.missing_rules.keys()
    assert report.classification != "full"
    assert report.synthetic and not report.real
    assert report.intersection_dates == ()


def test_single_vix_and_slow_gex_do_not_claim_warmup_or_migration(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.coverage import audit_dataset
    report = audit_dataset(load_dataset(dataset_writer([bar_record(), gex_record(), point_record()])),
                           frozenset({"wall_migration", "vix"}))
    assert {"wall_migration", "vix"} <= report.missing_rules.keys()
    assert report.source_freshness["synthetic"]["max_latency_seconds"] == 1.0


def test_indicative_underlying_cannot_satisfy_actual_midpoint_mapping(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.coverage import audit_dataset
    rows = [gex_record()]
    for i in range(5):
        rows += [quote_record(sequence=10+i, event_at=f"2026-06-15T13:3{i}:00Z",
                              available_at=f"2026-06-15T13:3{i}:01Z"),
                 point_record(sequence=20+i, symbol="SPY", price_kind="indicative", value="600",
                              event_at=f"2026-06-15T13:3{i}:00Z",
                              available_at=f"2026-06-15T13:3{i}:01Z")]
    report = audit_dataset(load_dataset(dataset_writer(rows)), frozenset({"mapping"}))
    assert "mapping" in report.missing_rules
    assert report.indicative


def test_disjoint_rule_dates_have_no_supported_common_interval(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.coverage import audit_dataset
    report = audit_dataset(load_dataset(dataset_writer([
        bar_record(), gex_record(event_at="2026-06-16T13:31:00Z",
                                 available_at="2026-06-16T13:31:01Z")
    ])), frozenset({"mes_prices", "gex"}))
    assert report.intersection_dates == ()
    assert report.classification != "full"


def test_unknown_rules_fail_closed(prices_only_path):
    import pytest
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.coverage import audit_dataset
    from gamma_bot.data.models import CapabilityError
    with pytest.raises(CapabilityError):
        audit_dataset(load_dataset(prices_only_path), frozenset({"typo_filter"}))


def test_quote_arriving_after_unknown_trade_cannot_establish_cvd(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.coverage import audit_dataset
    trade = dict(type="trade", source_id="synthetic", sequence=2, contract="MESM6",
                 event_at="2026-06-15T13:31:00Z", available_at="2026-06-15T13:31:01Z",
                 price_ticks=24001, size=1, aggressor="unknown")
    dataset = load_dataset(dataset_writer([quote_record(sequence=3), trade]))
    report = audit_dataset(dataset, frozenset({"cvd"}))
    assert "cvd" in report.missing_rules


def test_margin_scenario_never_establishes_historical_broker_evidence(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.coverage import audit_dataset
    metadata = {"sources": [{"source_id": "synthetic", "classification": "historical",
                             "availability_verified": True}]}
    dataset = load_dataset(dataset_writer([bar_record()], metadata=metadata, margin_points=[{
        "contract": "MESM6", "available_at": "2026-06-14T00:00:00Z",
        "initial_cents": 100000, "maintenance_cents": 90000, "provenance": "scenario"}]))
    report = audit_dataset(dataset, frozenset({"mes_prices", "margin"}))
    assert report.margin_provenance == frozenset({"scenario"})
    assert report.classification != "full"


def test_supported_synthetic_inputs_keep_evidence_and_margin_labels(complete_synthetic_path):
    from datetime import date
    from gamma_bot.data.io import load_dataset
    report = load_dataset(complete_synthetic_path).coverage
    assert report.supported_rules == frozenset({"mes_prices", "gex", "negative_regions", "mapping",
                                               "cvd", "vix", "wall_migration", "opening_range", "macro", "margin"})
    assert dict(report.missing_rules) == {}
    assert report.intersection_dates == (date(2026, 6, 15),)
    assert report.classification == "synthetic" and not report.real
    assert report.margin_provenance == frozenset({"scenario"})


def test_gap_diagnostics_do_not_mix_instruments(dataset_writer):
    from gamma_bot.data.io import load_dataset
    metadata = {"sources": [{"source_id": "synthetic", "classification": "synthetic", "interval_seconds": 60}]}
    rows = [point_record(sequence=1), point_record(sequence=2, symbol="SPY", value="600",
                                                  event_at="2026-06-15T13:40:00Z",
                                                  available_at="2026-06-15T13:40:01Z"),
            point_record(sequence=3, event_at="2026-06-15T13:33:00Z",
                         available_at="2026-06-15T13:33:01Z")]
    gaps = load_dataset(dataset_writer(rows, metadata=metadata)).coverage.source_gaps["synthetic"]
    assert len(gaps) == 1
    assert (gaps[0][1] - gaps[0][0]).total_seconds() == 120


def test_empty_macro_calendar_requires_as_of_publication(dataset_writer):
    from gamma_bot.data.io import load_dataset
    metadata = {"macro_coverage_dates": ["2026-06-15"],
                "sources": [{"source_id": "synthetic", "classification": "synthetic"}]}
    report = load_dataset(dataset_writer([bar_record()], metadata=metadata)).coverage
    assert "macro" in report.missing_rules


def test_tied_different_source_quote_is_not_known_to_precede_unknown_trade(dataset_writer):
    from gamma_bot.data.io import load_dataset
    trade = dict(type="trade", source_id="z-trades", sequence=2, contract="MESM6",
                 event_at="2026-06-15T13:31:00Z", available_at="2026-06-15T13:31:01Z",
                 price_ticks=24001, size=1, aggressor="unknown")
    report = load_dataset(dataset_writer([quote_record(source_id="a-quotes"), trade])).coverage
    assert "cvd" in report.missing_rules


def test_delayed_session_open_does_not_shift_fixed_opening_range(dataset_writer):
    from gamma_bot.data.io import load_dataset
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 6, 15, 14, tzinfo=timezone.utc)
    rows = [bar_record(sequence=i, start_at=(start+timedelta(minutes=i)).isoformat(),
                       event_at=(start+timedelta(minutes=i+1)).isoformat(),
                       available_at=(start+timedelta(minutes=i+1, seconds=1)).isoformat()) for i in range(15)]
    session = dict(session_date="2026-06-15", open_at=start.isoformat(),
                   close_at="2026-06-15T20:00:00Z", schedule_available_at="2026-06-14T00:00:00Z")
    report = load_dataset(dataset_writer(rows, sessions=[session])).coverage
    assert "opening_range" in report.missing_rules


@pytest.mark.parametrize("pattern", ["no_walls", "excessive_latency", "short_span"])
def test_historical_scale_incomplete_wall_inputs_finish_promptly(dataset_writer, tmp_path, pattern):
    """A day-sized incomplete input must report missing coverage within five seconds."""
    from datetime import datetime, timedelta, timezone
    import json
    import subprocess
    import sys

    start = datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc)
    rows = []
    for i in range(1440):
        at = start + timedelta(seconds=i / 10 if pattern == "short_span" else i)
        latency = 600 if pattern == "excessive_latency" else 1
        changes = {"walls": []} if pattern == "no_walls" else {}
        rows.append(gex_record(sequence=i, event_at=at.isoformat(),
                               available_at=(at + timedelta(seconds=latency)).isoformat(), **changes))
    path = dataset_writer(rows)
    output = tmp_path / "audit.json"
    result = subprocess.run([sys.executable, "-m", "gamma_bot", "audit", "--dataset", str(path),
                             "--output", str(output)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2, result.stderr
    report = json.loads(output.read_text())
    assert "wall_migration" in report["missing_rules"]
    assert report["synthetic"] is True


@pytest.mark.parametrize("minute_offsets, supported", [
    ([0, 1, 5], True), ([0, 5, 10], True), ([0, 2, 4, 6], True),
    ([0, 0, 5], False), ([0, 5, 15], False), ([0, 1, 4], False),
])
def test_wall_window_preserves_distinct_spacing_and_inclusive_boundaries(dataset_writer, minute_offsets, supported):
    from datetime import datetime, timedelta, timezone
    from gamma_bot.data.io import load_dataset

    start = datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc)
    rows = [gex_record(sequence=i, event_at=(start + timedelta(minutes=offset)).isoformat(),
                       available_at=(start + timedelta(minutes=offset, seconds=1)).isoformat())
            for i, offset in enumerate(minute_offsets)]
    report = load_dataset(dataset_writer(rows)).coverage
    assert ("wall_migration" in report.supported_rules) is supported
