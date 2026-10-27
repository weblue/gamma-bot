"""Causal session and macro entry rules. These rules never gate exits."""
from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from gamma_bot.data.models import InvalidDatasetError, MacroRelease, RunConfig, Session


_DEFAULTS = RunConfig()
_EASTERN = ZoneInfo("America/New_York")


def _aware(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise InvalidDatasetError("decision timestamp must be timezone aware")


def _blackouts(releases: Sequence[MacroRelease], now: datetime,
               config: RunConfig) -> tuple[tuple[datetime, datetime], ...]:
    """Union only intervals whose schedules were published by the decision."""
    window = timedelta(minutes=config.macro_window_minutes)
    intervals = sorted((r.scheduled_at - window, r.scheduled_at + window)
                       for r in releases if r.schedule_available_at <= now)
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def entry_gate(now: datetime, session: Session, releases: Sequence[MacroRelease], *,
               schedules_verified: bool, config: RunConfig = _DEFAULTS
               ) -> tuple[bool, tuple[str, ...]]:
    """Require a complete calendar attested as available *at now* by caller.

    schedules_verified must include Dataset.macro_coverage_dates and its
    publication time; an empty releases sequence alone is not certification.
    Margin, shutdown, position ownership and sizing are separate entry gates.
    """
    _aware(now)
    if type(schedules_verified) is not bool:
        raise InvalidDatasetError("schedules_verified: expected bool")
    reasons = []
    if not schedules_verified or session.schedule_available_at > now:
        reasons.append("schedules_unverified")
    if any(r.schedule_available_at > now for r in releases):
        reasons.append("macro_schedule_unavailable")
    if not session.open_at <= now < session.close_at:
        reasons.append("outside_session")
    local = now.astimezone(_EASTERN)
    if local.date() != session.session_date or not config.entry_start <= local.time() < config.entry_end:
        reasons.append("outside_entry_window")
    if any(start <= now <= end for start, end in _blackouts(releases, now, config)):
        reasons.append("macro_blackout")
    if now >= flatten_deadline(session, releases, now, config=config):
        reasons.append("flatten_required")
    return not reasons, tuple(reasons)


def flatten_deadline(session: Session, releases: Sequence[MacroRelease], now: datetime,
                     *, config: RunConfig = _DEFAULTS) -> datetime:
    """Earliest relevant close/blackout deadline, including an overdue one.

    An active union keeps its original deadline until its inclusive end. The
    caller must flatten when now >= the returned value, regardless of entries.
    """
    _aware(now)
    lead = timedelta(minutes=config.flatten_minutes)
    deadlines = [session.close_at - lead]
    for start, end in _blackouts(releases, now, config):
        if end >= max(now, session.open_at) and start < session.close_at:
            deadlines.append(start - lead)
    return min(deadlines)
