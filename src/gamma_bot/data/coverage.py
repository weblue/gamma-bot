"""Conservative input-capability audit, not a signal or profitability verdict."""
from collections import defaultdict
from bisect import bisect_left, bisect_right
from datetime import time, timedelta
from zoneinfo import ZoneInfo

from .ordering import causally_precedes
from .models import (CapabilityError, CoverageReport, Dataset, GexSnapshot, MesBar,
                     PricePoint, Quote, RULE_IDS, RunConfig, Trade)


ET = ZoneInfo("America/New_York")
REASONS = {
    "mes_prices": "Missing explicit MES execution prices.",
    "gex": "Missing non-indicative as-of GEX snapshots with model and expiry universe.",
    "negative_regions": "Missing available modeled profile and documented boundary membership convention.",
    "mapping": "Missing five contemporaneous actual underlying midpoints paired with MES quotes in five minutes.",
    "cvd": "Missing MES trades with real aggressor direction or ordered quote-classification inputs.",
    "vix": "Missing ten consecutive completed one-minute VIX index observations.",
    "wall_migration": "Missing three same-model wall snapshots spanning 5–15 minutes with gaps at most five minutes.",
    "opening_range": "Missing complete 09:30–09:45 ET one-minute MES bars and an available session schedule.",
    "macro": "Missing explicit verified macro calendar coverage, including dates with no releases.",
    "margin": "Missing causal MES margin history or an explicitly labeled margin scenario.",
}


def _day(at):
    return at.astimezone(ET).date()


def _minute(at):
    return at.replace(second=0, microsecond=0)


def _has_wall_window(snapshots):
    times = sorted({snapshot.event_at for snapshot in snapshots})
    gap = timedelta(minutes=5)
    for middle, at in enumerate(times):
        # For this middle observation, the earliest eligible predecessor and
        # latest eligible successor maximize span without exceeding either gap.
        left = bisect_left(times, at - gap, hi=middle)
        right = bisect_right(times, at + gap, lo=middle + 1) - 1
        if left < middle < right and times[right] - times[left] >= gap:
            # Two gaps of at most 5 minutes also imply a span <= 10 (< 15).
            return True
    return False


def audit_dataset(dataset: Dataset, required_rules: frozenset[str], *,
                  config: RunConfig | None = None) -> CoverageReport:
    """Report potential rule inputs; per-decision freshness and causality still apply.

    A common date is a necessary overlap check, not a claim of complete intraday
    coverage. Synthetic/indicative/unknown data never classify as full evidence.
    """
    cfg = config or RunConfig()
    if required_rules - RULE_IDS:
        raise CapabilityError("unknown required rule IDs: " + ", ".join(sorted(required_rules - RULE_IDS)))
    dates = {r: set() for r in RULE_IDS}
    quotes = [e for e in dataset.events if isinstance(e, Quote)]
    bars = [e for e in dataset.events if isinstance(e, MesBar)]
    trades = [e for e in dataset.events if isinstance(e, Trade)]
    gex = [e for e in dataset.events if isinstance(e, GexSnapshot) and e.provenance != "indicative"]
    points = [e for e in dataset.events if isinstance(e, PricePoint)]
    for e in bars + quotes + trades:
        dates["mes_prices"].add(_day(e.event_at))
    for e in gex:
        dates["gex"].add(_day(e.event_at))
        meta = dataset.source_metadata.get(e.source_id, {})
        if e.profile_available and meta.get("profile_boundary_convention"):
            dates["negative_regions"].add(_day(e.event_at))
    for e in trades:
        if e.aggressor != "unknown":
            dates["cvd"].add(_day(e.event_at))
        elif any(q.contract == e.contract and causally_precedes(q, e) and
                 timedelta(0) <= e.event_at - q.event_at <= timedelta(seconds=60) for q in quotes):
            dates["cvd"].add(_day(e.event_at))
    # VIX observations are minute-completion labels, not daily closes.
    vix_by_day = defaultdict(set)
    for e in points:
        if (e.symbol == "VIX" and e.price_kind == "index" and e.event_at == _minute(e.event_at)
                and e.available_at - e.event_at <= timedelta(seconds=90)):
            vix_by_day[_day(e.event_at)].add(e.event_at)
    for day, minute_ends in vix_by_day.items():
        if any(all(end - timedelta(minutes=i) in minute_ends for i in range(10)) for end in minute_ends):
            dates["vix"].add(day)
    # Mapping must use the snapshot's actual underlying, never an adjusted close.
    for underlying in {e.underlying for e in gex}:
        paired = defaultdict(set)
        for p in points:
            if p.symbol != underlying or p.price_kind != "mid":
                continue
            if p.available_at - p.event_at > timedelta(seconds=60):
                continue
            for q in quotes:
                at = max(p.available_at, q.available_at)
                if (timedelta(0) <= at - p.event_at <= timedelta(seconds=60) and
                        timedelta(0) <= at - q.event_at <= timedelta(seconds=60)):
                    paired[(_day(at), q.contract)].add(_minute(at))
        for (day, _), minutes in paired.items():
            if any(sum(timedelta(0) <= end - m < timedelta(seconds=cfg.mapping_window_seconds) for m in minutes) >= max(5, cfg.mapping_min_pairs) for end in minutes):
                dates["mapping"].add(day)
    groups = defaultdict(list)
    for e in gex:
        if e.walls and e.available_at - e.event_at <= timedelta(minutes=5):
            groups[(e.source_id, e.underlying, e.model_id, e.expiry_days, _day(e.event_at))].append(e)
    for key, snapshots in groups.items():
        if _has_wall_window(snapshots):
            dates["wall_migration"].add(key[-1])
    for session in dataset.sessions:
        start = session.open_at
        if start.astimezone(ET).time() != time(9, 30):
            continue
        needed = {start + timedelta(minutes=i) for i in range(15)}
        for contract in {e.contract for e in bars}:
            found = {e.start_at for e in bars if e.contract == contract and
                     e.event_at-e.start_at == timedelta(minutes=1) and
                     e.available_at <= start+timedelta(minutes=16)}
            if needed <= found and session.close_at >= start+timedelta(minutes=15):
                dates["opening_range"].add(session.session_date)
    if dataset.macro_schedule_available_at is not None:
        dates["macro"].update(s.session_date for s in dataset.sessions
                              if s.session_date in dataset.macro_coverage_dates
                              and dataset.macro_schedule_available_at <= s.open_at)
    # A historical/scenario margin may be effective before the first event day.
    for day in dates["mes_prices"]:
        prices = [e for e in bars+quotes+trades if _day(e.event_at) == day]
        if prices and all(any(m.contract == e.contract and m.available_at <= e.available_at
                              for m in dataset.margin_points) for e in prices):
            dates["margin"].add(day)

    supported = frozenset(rule for rule, days in dates.items() if days)
    missing = {rule: REASONS[rule] for rule in sorted(required_rules - supported)}
    if 'mapping' in missing and (cfg.mapping_window_seconds, cfg.mapping_min_pairs) != (300, 5):
        missing['mapping'] = (f'Missing {max(5, cfg.mapping_min_pairs)} contemporaneous actual underlying '
            f'midpoints paired with MES quotes in {cfg.mapping_window_seconds} trailing seconds.')
    common = set.intersection(*(dates[r] for r in required_rules)) if required_rules else set()
    freshness, gaps = {}, {}
    for source in sorted({e.source_id for e in dataset.events}):
        rows = [e for e in dataset.events if e.source_id == source]
        freshness[source] = {
            "first_event_at": min(e.event_at for e in rows),
            "last_event_at": max(e.event_at for e in rows),
            "first_available_at": min(e.available_at for e in rows),
            "last_available_at": max(e.available_at for e in rows),
            "max_latency_seconds": max((e.available_at-e.event_at).total_seconds() for e in rows),
        }
        # Gaps are computed within each instrument/event stream, not across symbols.
        streams = defaultdict(set)
        for e in rows:
            instrument = getattr(e, "contract", getattr(e, "symbol", getattr(e, "underlying", "")))
            streams[(type(e).__name__, instrument)].add(e.event_at)
        interval = dataset.source_metadata.get(source, {}).get("interval_seconds", 60)
        gaps[source] = tuple(sorted({(a, b) for times in streams.values()
                                     for a, b in zip(sorted(times), sorted(times)[1:])
                                     if (b-a).total_seconds() > interval}))
    sources = [dataset.source_metadata.get(s, {}) for s in {e.source_id for e in dataset.events}]
    synthetic = any(s.get("classification") == "synthetic" for s in sources)
    indicative = (any(s.get("classification") == "indicative" for s in sources) or
                  any(isinstance(e, PricePoint) and e.price_kind == "indicative" or
                      isinstance(e, GexSnapshot) and e.provenance == "indicative" for e in dataset.events))
    unknown = not sources or any(s.get("classification", "unknown") == "unknown" or
                                 s.get("classification") == "historical" and not s.get("availability_verified")
                                 for s in sources)
    real = bool(sources) and all(s.get("classification") == "historical" and s.get("availability_verified") for s in sources)
    margins = frozenset(m.provenance for m in dataset.margin_points)
    classification = "incomplete"
    if not missing and common and real and not indicative and "scenario" not in margins:
        classification = "full"
    elif synthetic:
        classification = "synthetic"
    elif indicative:
        classification = "indicative"
    limitations = list(dataset.limitations)
    for source in sources:
        limitations.extend(source.get("limitations", ()))
    if synthetic:
        limitations.append("Synthetic mechanics only; no historical strategy evidence.")
    if unknown:
        limitations.append("Source provenance or historical availability is unknown.")
    if not common:
        limitations.append("No common date with every required input established.")
    limitations.append("Coverage describes input potential; every decision still requires causal joins, warm-up and freshness gates.")
    if "scenario" in margins:
        limitations.append("Margin includes a scenario, not historical broker executability evidence.")
    return CoverageReport(supported_rules=supported, missing_rules=missing,
                          intersection_dates=tuple(sorted(common)), source_freshness=freshness,
                          source_gaps=gaps, real=real, synthetic=synthetic, indicative=indicative,
                          unknown=unknown, margin_provenance=margins, classification=classification,
                          limitations=tuple(dict.fromkeys(limitations)))
