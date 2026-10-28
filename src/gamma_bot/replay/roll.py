"""Causal forward-only quarterly selection from complete regular-session bars.

Startup selects the nearest delivery month strictly AFTER the session month,
then compares only its immediate next quarter. Delivery-month sessions require
expiry metadata absent from schema v1, so they are conservatively excluded;
this is a research restriction, not an exchange expiry rule. Short years resolve
to the nearest year to the session year (ties earlier). Alias ambiguity excludes.
"""
from datetime import datetime, timedelta
import re

from gamma_bot.data.models import MesBar, Session


def delivery(contract: str, year: int) -> tuple[int, int]:
    match = re.fullmatch(r'MES([HMUZ])(\d{1,4})|MES(\d{4})([HMUZ])', contract)
    code, digits = (match[1], match[2]) if match[1] else (match[4], match[3])
    value = int(digits)
    if len(digits) < 4:
        scale = 10 ** len(digits)
        base = year // scale * scale + value
        value = min((base-scale, base, base+scale), key=lambda y: (abs(y-year), y))
    return value, {'H': 3, 'M': 6, 'U': 9, 'Z': 12}[code]


class RollSelector:
    def __init__(self):
        self.active: str | None = None
        self._bars: dict[object, dict[str, list[MesBar]]] = {}

    def observe(self, bar: MesBar, session: Session) -> None:
        if session.open_at <= bar.start_at < bar.event_at <= session.close_at:
            self._bars.setdefault(session.session_date, {}).setdefault(bar.contract, []).append(bar)

    def select(self, session: Session, previous: Session | None, now: datetime
               ) -> tuple[str | None, tuple[str, ...]]:
        volumes = {}
        ambiguous = set()
        if previous is not None and previous.close_at <= now:
            expected = set()
            at = previous.open_at
            while at < previous.close_at:
                expected.add(at)
                at += timedelta(minutes=1)
            for contract, bars in self._bars.get(previous.session_date, {}).items():
                admitted = [b for b in bars if b.available_at <= now]
                if len({b.source_id for b in admitted}) > 1:
                    ambiguous.add(contract)
                    continue
                starts = [b.start_at for b in admitted]
                if (set(starts) == expected and len(starts) == len(expected) and
                        all(b.event_at-b.start_at == timedelta(minutes=1) for b in admitted)):
                    volumes[contract] = sum(b.volume for b in admitted)
        period = session.session_date.year, session.session_date.month
        key = lambda c: delivery(c, session.session_date.year)
        if self.active is None:
            candidates = sorted((c for c in volumes.keys() | ambiguous if key(c) > period), key=lambda c: (key(c), c))
            if not candidates:
                reason = 'contract_expiry_unverified' if volumes else 'roll_evidence_missing'
                return None, (reason,)
            if candidates[0] in ambiguous:
                return None, ('roll_volume_source_ambiguous',)
            self.active = candidates[0]
        year, month = key(self.active)
        next_period = (year+1, 3) if month == 12 else (year, month+3)
        if self.active in ambiguous or any(key(c) == next_period for c in ambiguous):
            return None, ('roll_volume_source_ambiguous',)
        if self.active not in volumes:
            return None, ('roll_evidence_missing',)
        aliases = [c for c in volumes if key(c) == key(self.active)]
        challengers = [c for c in volumes if key(c) == next_period]
        if len(aliases) != 1 or len(challengers) > 1:
            return None, ('roll_contract_alias_ambiguous',)
        if not challengers:
            return None, ('roll_challenger_evidence_missing',)
        if volumes[challengers[0]] > volumes[self.active]:
            self.active = challengers[0]
        # An ineligible retained contract may still supply causal comparison
        # evidence for its adjacent successor; never execute the ineligible one.
        if key(self.active) <= period:
            return None, ('contract_expiry_unverified',)
        return self.active, ()
