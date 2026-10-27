import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest


def bar_record(**changes):
    """Synthetic minute bar; literal expected values are owned by each test."""
    row = dict(type="mes_bar", source_id="synthetic", sequence=1,
               contract="MESM6", start_at="2026-06-15T13:30:00Z",
               event_at="2026-06-15T13:31:00Z", available_at="2026-06-15T13:31:01Z",
               open_ticks=24000, high_ticks=24004, low_ticks=23996,
               close_ticks=24002, volume=10)
    return row | changes


def gex_record(**changes):
    row = dict(type="gex_snapshot", source_id="synthetic", sequence=2,
               event_at="2026-06-15T13:31:00Z", available_at="2026-06-15T13:31:01Z",
               underlying="SPY", spot="600", total_gex="1000",
               walls=[dict(wall_id="s", kind="support", strike="599", concentration="100")],
               negative_regions=[], profile_available=True, model_id="test-model",
               expiry_days=[0, 1], oi_published_at="2026-06-15T10:00:00Z",
               provenance="modeled")
    return row | changes


def quote_record(**changes):
    row = dict(type="quote", source_id="synthetic", sequence=3,
               event_at="2026-06-15T13:31:00Z", available_at="2026-06-15T13:31:01Z",
               contract="MESM6", bid_ticks=24000, ask_ticks=24001)
    return row | changes


def point_record(**changes):
    row = dict(type="price_point", source_id="synthetic", sequence=4,
               event_at="2026-06-15T13:31:00Z", available_at="2026-06-15T13:31:01Z",
               symbol="VIX", value="18.25", price_kind="index")
    return row | changes


@pytest.fixture
def dataset_writer(tmp_path):
    def write(events, metadata=None, **tables):
        directory = tmp_path / f"dataset-{len(list(tmp_path.iterdir()))}"
        directory.mkdir()
        (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        (directory / "metadata.json").write_text(json.dumps(metadata or {
            "sources": [{"source_id": "synthetic", "classification": "synthetic",
                         "limitations": ["Synthetic mechanics fixture; no market evidence."]}],
            "limitations": ["Synthetic mechanics only"],
        }))
        for name, rows in tables.items():
            (directory / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        return directory
    return write


@pytest.fixture
def prices_only_path():
    return Path(__file__).parent / "fixtures" / "synthetic-prices.jsonl"


@pytest.fixture
def complete_synthetic_path(dataset_writer):
    """Input potential for every rule, without asserting any trade would qualify."""
    start = datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc)
    def stamp(minutes, seconds=0):
        return (start + timedelta(minutes=minutes, seconds=seconds)).isoformat()
    rows = [bar_record(sequence=i, start_at=stamp(i), event_at=stamp(i+1),
                       available_at=stamp(i+1, 1)) for i in range(15)]
    rows += [point_record(sequence=100+i, event_at=stamp(i+1), available_at=stamp(i+1, 1))
             for i in range(10)]
    rows += [gex_record(sequence=200+i, event_at=stamp(i), available_at=stamp(i, 1))
             for i in (0, 3, 6)]
    for i in range(5):
        rows += [quote_record(sequence=300+i, event_at=stamp(i), available_at=stamp(i, 1)),
                 point_record(sequence=400+i, symbol="SPY", price_kind="mid", value="600",
                              event_at=stamp(i), available_at=stamp(i, 1))]
    rows.append(dict(type="trade", source_id="synthetic", sequence=500, contract="MESM6",
                     event_at=stamp(5), available_at=stamp(5, 1), price_ticks=24001, size=1,
                     aggressor="buy"))
    return dataset_writer(rows, metadata={
        "sources": [{"source_id": "synthetic", "classification": "synthetic",
                     "profile_boundary_convention": "lower inclusive, upper exclusive",
                     "interval_seconds": 60, "limitations": ["Synthetic test only"]}],
        "macro_coverage_dates": ["2026-06-15"],
        "macro_schedule_available_at": "2026-06-14T00:00:00Z",
    }, sessions=[dict(session_date="2026-06-15", open_at=stamp(0), close_at=stamp(390),
                      schedule_available_at="2026-06-14T00:00:00Z")],
       macro_releases=[dict(event_id="synthetic-ism", category="ISMServices",
                            scheduled_at=stamp(30), schedule_available_at="2026-06-14T00:00:00Z")],
       margin_points=[dict(contract="MESM6", available_at="2026-06-14T00:00:00Z",
                           initial_cents=100000, maintenance_cents=90000, provenance="scenario")])
