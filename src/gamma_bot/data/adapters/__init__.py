"""Provider-neutral contract. No production source is currently verified/registered."""
from collections.abc import Iterator
from typing import Protocol

from ..models import CoverageReport, Event


class SourceAdapter(Protocol):
    """Supply credential-free typed events with source-wide stable sequences.

    Each call opens/reopens a bounded, cooperatively timed stream. Replays are
    permitted: the recorder rebuilds identities from durable output. Adapters
    must bound blocking reads so the foreground caller can honor its stop time,
    and close their own connections when the iterator is closed. Credentials
    belong only in private provider configuration, never events/capabilities.
    """

    @property
    def capabilities(self) -> CoverageReport:
        """Declared potential; never evidence that missing observations exist."""
        ...

    def events(self) -> Iterator[Event]:
        ...
