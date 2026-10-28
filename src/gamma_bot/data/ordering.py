"""Causal predecessor evidence, distinct from deterministic source-name sorting."""
from collections.abc import Iterable
from typing import TypeVar

from .models import MarketEvent


T = TypeVar("T", bound=MarketEvent)


def causally_precedes(earlier: MarketEvent, later: MarketEvent) -> bool:
    """Timestamp order is usable; an exact tie needs same-source sequence order.

    Source names make replay deterministic but establish no cross-feed causality.
    Source-local sequence values are comparable only within their own source.
    """
    earlier_time = (earlier.available_at, earlier.event_at)
    later_time = (later.available_at, later.event_at)
    return earlier_time < later_time or (
        earlier_time == later_time and earlier.source_id == later.source_id
        and earlier.sequence < later.sequence
    )


def latest_causal_predecessor(candidates: Iterable[T], event: MarketEvent) -> T | None:
    """Select the newest eligible market observation, not the latest publication.

    Rank event time first so late older records cannot displace newer market
    observations. Availability breaks event-time ties; unresolved cross-feed
    ties stay unavailable rather than being broken by source name or sequence.
    """
    eligible = [candidate for candidate in candidates if causally_precedes(candidate, event)]
    if not eligible:
        return None
    latest_time = max((candidate.event_at, candidate.available_at) for candidate in eligible)
    latest = [candidate for candidate in eligible
              if (candidate.event_at, candidate.available_at) == latest_time]
    if len({candidate.source_id for candidate in latest}) != 1:
        return None
    return max(latest, key=lambda candidate: candidate.sequence)
