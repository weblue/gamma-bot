"""Frozen chronological offline studies; no optimization or holdout selection.

The common calendar is independent of strategy signals and realized P&L. Missing
hypothesis inputs remain unavailable experiments on that same calendar.
"""
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256
from decimal import Decimal
import json
from pathlib import Path
from types import MappingProxyType
from zoneinfo import ZoneInfo

from gamma_bot.data.models import Dataset, InvalidDatasetError, MesBar, RunConfig
from gamma_bot.data.provenance import to_jsonable
from gamma_bot.replay.engine import ReplayResult, run_replay
from gamma_bot.replay.roll import RollSelector
from gamma_bot.strategies import (Variant, ReversalStrategy, BreakoutStrategy,
                                  OpeningRangeBaseline)

ET = ZoneInfo('America/New_York')
COMPLETENESS_POLICY_VERSION = 2
COMPLETENESS_RULE = (
    'Session schedule and explicit macro-date coverage published by open; causal '
    'current/adjacent-quarter roll selection from preceding complete session; '
    'selected MES contract has exactly one single-source one-minute bar per '
    'regular-session event-time minute, regardless of publication latency; '
    'selected-contract margin available by open. Actual publication times still '
    'gate replay and preceding-session roll evidence. Feature/filter availability '
    'is reported per experiment, not used to select the common session calendar. '
    'No P&L or signals are used.'
)


def _json(value):
    return json.dumps(to_jsonable(value), indent=2, sort_keys=True, allow_nan=False) + '\n'


def _freeze_json(path, payload):
    """Never overwrite a frozen study, including when later inputs are added."""
    path = Path(path)
    encoded = to_jsonable(payload)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise InvalidDatasetError('invalid frozen artifact') from exc
        if existing != encoded:
            raise InvalidDatasetError('frozen study differs; choose a new study output path')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write(_json(payload))


def freeze_split(session_dates: Sequence[date], output: Path) -> tuple[tuple[date, ...], tuple[date, ...]]:
    """Persist sorted unique complete dates, floor(70%) development, rest holdout.

    Fewer than two dates freezes an explicitly insufficient pipeline
    demonstration with no development/evaluation split. Callers supply complete
    dates; ``complete_session_dates`` implements the study's input-only rule.
    """
    if any(type(day) is not date for day in session_dates):
        raise InvalidDatasetError('split requires session dates')
    days = tuple(sorted(set(session_dates)))
    cut = len(days) * 7 // 10
    enough = 0 < cut < len(days)
    development, evaluation = (days[:cut], days[cut:]) if enough else ((), ())
    _freeze_json(output, {'schema_version': 1,
        'completeness_policy_version': COMPLETENESS_POLICY_VERSION, 'status': 'chronological_evaluation' if enough else 'pipeline_demonstration',
        'complete_session_dates': days, 'development_fraction': '0.70',
        'development': development, 'evaluation': evaluation})
    return development, evaluation


def complete_session_dates(dataset: Dataset) -> tuple[date, ...]:
    """Determine common complete sessions reproducibly from input evidence only."""
    return _session_dates(dataset, require_complete=True)


def _session_dates(dataset, *, require_complete):
    sessions = sorted(dataset.sessions, key=lambda s: s.open_at)
    bars = defaultdict(list)
    for event in dataset.events:
        if isinstance(event, MesBar):
            bars[event.start_at.astimezone(ET).date()].append(event)
    selector = RollSelector()
    result = []
    for index, session in enumerate(sessions):
        previous = sessions[index-1] if index else None
        active, _ = selector.select(session, previous, session.open_at)
        rows = [b for b in bars[session.session_date] if b.contract == active
                and session.open_at <= b.start_at < session.close_at]
        expected = set()
        at = session.open_at
        while at < session.close_at:
            expected.add(at)
            at += timedelta(minutes=1)
        complete = (len(rows) == len(expected) and {b.start_at for b in rows} == expected
            and len({b.source_id for b in rows}) == 1
            and all(b.event_at-b.start_at == timedelta(minutes=1) for b in rows))
        calendar = (session.schedule_available_at <= session.open_at
            and session.session_date in dataset.macro_coverage_dates
            and dataset.macro_schedule_available_at is not None
            and dataset.macro_schedule_available_at <= session.open_at)
        margin = any(m.contract == active and m.available_at <= session.open_at for m in dataset.margin_points)
        if active and (complete if require_complete else bool(rows)) and calendar and margin:
            result.append(session.session_date)
        for event in bars[session.session_date]:
            selector.observe(event, session)
    return tuple(result)


def default_variants() -> tuple[Variant, ...]:
    """Both full hypotheses, every supported single removal, and matched baseline."""
    variants = []
    for constructor in (ReversalStrategy, BreakoutStrategy):
        full = constructor.full_variant
        variants.append(full)
        for rule in sorted(constructor.optional_filters):
            variants.append(Variant(name=full.name+'_without_'+rule,
                enabled_filters=full.enabled_filters-{rule}, omitted_rules=(rule,)))
    return (*variants, OpeningRangeBaseline.full_variant)


def _constructor(variant):
    for constructor in (ReversalStrategy, BreakoutStrategy, OpeningRangeBaseline):
        try:
            constructor(variant=variant)
            return constructor
        except ValueError:
            pass
    raise InvalidDatasetError('variant does not match a supported strategy family')


def sensitivity_configs(config: RunConfig) -> Mapping[str, RunConfig]:
    """Freeze one-at-a-time candidates; they do not measure joint interactions."""
    if config.mapping_min_pairs < 5:
        raise InvalidDatasetError('mapping sensitivity requires at least five valid pairs')
    scenarios = {'reference': config}
    for field, values, label in (
        ('mapping_window_seconds', (300, 600, 900), 'mapping'),
        ('round_trip_fee_cents', (200, 300, 500), 'fee'),
        ('slippage_ticks', (1, 2, 4), 'slippage')):
        for value in values:
            if value != getattr(config, field):
                scenarios[f'{label}_{value}'] = replace(config, **{field: value})
    return MappingProxyType(scenarios)


def experiment_plan(dataset: Dataset, config: RunConfig, variants: Sequence[Variant]):
    """Pure preflight plan, also used by CLI before any artifact is written."""
    if config.initial_equity_cents != 1_000_000 or config.development_fraction != Decimal('0.70'):
        raise InvalidDatasetError('study requires USD 10,000 initial capital and a 70/30 split')
    if not variants or len({v.name for v in variants}) != len(variants):
        raise InvalidDatasetError('provide nonempty variants with unique names')
    # Names become artifact directories; prohibit path traversal for direct callers.
    if any(not v.name.isascii() or not v.name.replace('_', '').replace('-', '').isalnum() for v in variants):
        raise InvalidDatasetError('variant name must be a safe artifact identifier')
    families = {v.name: _constructor(v).family for v in variants}
    scenarios = sensitivity_configs(config)
    complete = complete_session_dates(dataset)
    cut = len(complete)*7//10
    enough = 0 < cut < len(complete)
    parts = ({'development': complete[:cut], 'evaluation': complete[cut:]} if enough else
             {'demonstration': _session_dates(dataset, require_complete=False)})
    return {'schema_version': 1, 'completeness_policy_version': COMPLETENESS_POLICY_VERSION,
        'status': 'chronological_evaluation' if enough else 'pipeline_demonstration',
        'selection_policy': 'none', 'completeness_rule': COMPLETENESS_RULE,
        'complete_session_dates': complete, 'parts': parts,
        'variants': {v.name: {'family': families[v.name], 'variant': v} for v in variants},
        'scenarios': scenarios, 'input_hashes': dataset.input_hashes,
        'dataset_sha256': sha256(_json(dataset).encode('utf-8')).hexdigest(),
        'limitations': ('Frozen one-at-a-time sensitivity scenarios do not measure interactions between simultaneous changes.',
            'Every run starts with independent USD 10,000 capital and fresh strategy state; actual replay resizes for its costs.',
            'No parameter selection, holdout optimization, funding recommendation or profitability claim.',
            'Session and opportunity counts are descriptive; dependent observations and small samples limit inference. No confidence interval or significance claim.',
            'Insufficient complete sessions are a pipeline demonstration; partial observed sessions are never validation.',
            'Synthetic/indicative/scenario/unknown inputs remain mechanics evidence only; coverage and unresolved outcomes retain their report gates.')}


def evaluate(dataset: Dataset, config: RunConfig, variants: Sequence[Variant],
             split_path: Path) -> Mapping[str, ReplayResult]:
    """Freeze the split AND all actual run configurations before any replay.

    Mapping keys are ``part/variant/scenario``. Every attempted configuration,
    including unavailable and zero-trade outcomes, remains present. The frozen
    manifest is ``experiments.json`` beside split_path. Input hashes bind the
    study to the supplied bytes; use a new study path for changed data/configs.
    """
    variants = tuple(variants)
    plan = experiment_plan(dataset, config, variants)
    manifest_path = Path(split_path).with_name('experiments.json')
    if manifest_path.resolve() == Path(split_path).resolve():
        raise InvalidDatasetError('split path must differ from experiments.json')
    freeze_split(plan['complete_session_dates'], split_path)
    _freeze_json(manifest_path, plan)
    results = {}
    for part, dates in plan['parts'].items():
        # Keep all preceding sessions/events for causal forward-only rolls. Stop
        # before future input availability; no later-period evidence is admitted.
        last = max(dates) if dates else date.min
        data = replace(dataset,
            events=tuple(e for e in dataset.events if e.available_at.astimezone(ET).date() <= last),
            sessions=tuple(s for s in dataset.sessions if s.session_date <= last))
        for variant in variants:
            constructor = _constructor(variant)
            for scenario, candidate in plan['scenarios'].items():
                strategy = constructor(candidate, variant=variant)
                results[f'{part}/{variant.name}/{scenario}'] = run_replay(data, candidate,
                    variant, strategies=[strategy], admitted_session_dates=dates)
    return MappingProxyType(results)
