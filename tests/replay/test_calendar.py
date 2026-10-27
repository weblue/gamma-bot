from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from gamma_bot.data.models import MacroRelease, RunConfig, Session
from gamma_bot.replay.calendar import entry_gate, flatten_deadline


def eastern(day, clock):
    return datetime.fromisoformat(f"{day}T{clock}").replace(
        tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)


def session(day="2026-06-15", close="16:00:00"):
    opening = eastern(day, "09:30:00")
    return Session(session_date=opening.astimezone(ZoneInfo("America/New_York")).date(),
                   open_at=opening, close_at=eastern(day, close),
                   schedule_available_at=opening - timedelta(days=1))


def release(clock="10:00:00", available="08:00:00", event_id="ism"):
    return MacroRelease(event_id=event_id, category="ISMManufacturing",
                        scheduled_at=eastern("2026-06-15", clock),
                        schedule_available_at=eastern("2026-06-15", available))


@pytest.mark.parametrize("clock,want", [
    ("09:44:59", False), ("09:45:00", True), ("10:29:59", True),
    ("10:30:00", False), ("15:55:00", False),
])
def test_entry_window_has_exact_boundaries(clock, want):
    allowed, reasons = entry_gate(eastern("2026-06-15", clock), session(), (),
                                  schedules_verified=True)
    assert allowed is want
    assert bool(reasons) is not want


@pytest.mark.parametrize("day,utc_hour", [("2026-07-02", 16), ("2026-11-27", 17)])
def test_early_close_uses_local_session_time_through_dst(day, utc_hour):
    early = session(day, "13:00:00")
    deadline = flatten_deadline(early, (), early.open_at)
    assert deadline == datetime.fromisoformat(f"{day}T{utc_hour}:55:00+00:00")
    assert deadline.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") == "12:55"


@pytest.mark.parametrize("clock,blocked", [
    ("09:29:59", False), ("09:30:00", True), ("10:00:00", True),
    ("10:30:00", True), ("10:30:01", False),
])
def test_macro_blackout_includes_both_endpoints(clock, blocked):
    _, reasons = entry_gate(eastern("2026-06-15", clock), session(), (release(),),
                            schedules_verified=True)
    assert ("macro_blackout" in reasons) is blocked


def test_macro_flatten_starts_five_minutes_before_blackout():
    assert flatten_deadline(session(), (release(),), session().open_at) == eastern(
        "2026-06-15", "09:25:00")


def test_overlapping_releases_form_one_continuous_blackout():
    releases = (release(), release("10:45:00", event_id="other"))
    assert flatten_deadline(session(), releases, eastern("2026-06-15", "10:31:00")) == eastern(
        "2026-06-15", "09:25:00")
    assert flatten_deadline(session(), releases, eastern("2026-06-15", "11:15:01")) == eastern(
        "2026-06-15", "15:55:00")


def test_missing_or_future_known_schedule_rejects_entries():
    now = eastern("2026-06-15", "09:45:00")
    assert not entry_gate(now, session(), (), schedules_verified=False)[0]
    unknown = release(available="09:50:00")
    allowed, reasons = entry_gate(now, session(), (unknown,), schedules_verified=True)
    assert not allowed
    assert "macro_schedule_unavailable" in reasons
    # The future-known release must not leak into today's flatten decision.
    assert flatten_deadline(session(), (unknown,), now) == eastern("2026-06-15", "15:55:00")


def test_next_day_release_does_not_force_todays_flatten():
    tomorrow = MacroRelease(event_id="tomorrow", category="CPI",
                            scheduled_at=eastern("2026-06-16", "08:30:00"),
                            schedule_available_at=eastern("2026-06-15", "08:00:00"))
    assert flatten_deadline(session(), (tomorrow,), session().open_at) == eastern(
        "2026-06-15", "15:55:00")


def test_naive_decision_times_are_rejected():
    with pytest.raises(ValueError):
        entry_gate(datetime(2026, 6, 15, 9, 45), session(), (), schedules_verified=True)


def test_flatten_deadline_blocks_an_otherwise_valid_entry():
    allowed, reasons = entry_gate(eastern("2026-06-15", "09:55:00"),
                                  session(close="10:00:00"), (), schedules_verified=True)
    assert not allowed
    assert "flatten_required" in reasons


def test_actual_session_open_is_required_in_addition_to_wall_clock_window():
    normal = session()
    delayed = Session(session_date=normal.session_date,
                      open_at=eastern("2026-06-15", "10:00:00"),
                      close_at=normal.close_at, schedule_available_at=normal.schedule_available_at)
    assert not entry_gate(eastern("2026-06-15", "09:45:00"), delayed, (),
                          schedules_verified=True)[0]


def test_configured_windows_are_used_without_changing_defaults():
    config = RunConfig(entry_start=time(10), entry_end=time(10, 15),
                       flatten_minutes=10, macro_window_minutes=15)
    assert not entry_gate(eastern("2026-06-15", "09:50:00"), session(), (),
                          schedules_verified=True, config=config)[0]
    assert entry_gate(eastern("2026-06-15", "10:00:00"), session(), (),
                      schedules_verified=True, config=config)[0]
    assert flatten_deadline(session(), (release(),), session().open_at,
                            config=config) == eastern("2026-06-15", "09:35:00")
