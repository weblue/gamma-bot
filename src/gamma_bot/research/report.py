"""Deterministic, self-contained research artifacts; no raw market data exports."""
import csv
from dataclasses import fields
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit

from gamma_bot.data.io import SOURCE_FIELDS
from gamma_bot.data.provenance import to_jsonable
from gamma_bot.replay.engine import ReplayResult, SkipRecord, TradeRecord
from .metrics import compute_metrics, opportunity_key

REPORT_FILES = ('manifest.json', 'coverage.json', 'trades.csv', 'equity.csv', 'skips.csv', 'report.html')
_MANIFEST_FIELDS = frozenset({'schema_version', 'status', 'variant', 'experiment_scope', 'config',
    'input_hashes', 'sources', 'coverage', 'limitations', 'roll_history', 'excluded_sessions',
    'margin_used', 'ending_reserved_margin_cents', 'ending_equity_cents',
    'gross_realized_pnl_cents', 'fees_cents', 'code_revision'})
_METHODS = (
    'Money is USD cents; return, drawdown, win rate and exposure ratios are fractions, not percentages.',
    'Trade statistics aggregate FIFO slices by strategy, trigger, contract and New York signal session. '
    'Streaks use final exit order; a breakeven trade breaks a losing streak. Average losses are signed; '
    'profit factor is undefined when there are no losses.',
    'Marked drawdown scans every equity record in replay order, including duplicate timestamps, '
    'from initial capital. It is not a closed-trade-only drawdown.',
    'Exposure is the union of actual entry-to-exit slice intervals. Its denominator is the sum of '
    'first-to-last observed equity times on admitted contract sessions; excluded data-only warm-up '
    'sessions and overnight gaps are omitted. This is observed time, not complete session coverage.',
    'Risk overruns compare opportunity net loss including fees with the sum of planned slice risk. '
    'Planned risk is already total cents for the slice quantity. Gaps can exceed that plan. '
    'Per-slice realized_loss_cents and risk_overrun_cents are diagnostic; statistics use whole opportunities.',
    'target_ticks is the original FIFO entry target; exit_trigger_target_ticks identifies the actual '
    'target order causing the reduction and can differ. Exact entry and exit ticks remain in trades.csv.',
    'All results are offline simulations with fill, liquidity, roll and margin approximations; '
    'they do not establish profitability or broker executability.',
)


def _json(value):
    return json.dumps(to_jsonable(value), indent=2, sort_keys=True, allow_nan=False) + '\n'


def _safe_sources(sources):
    # Dataset ingestion already rejects credentials. Keep that boundary when a
    # caller constructs ReplayResult directly rather than through the importer.
    safe = {}
    for sid, source in sources.items():
        safe[sid] = {k: v for k, v in source.items() if k in SOURCE_FIELDS}
        for key in ('homepage', 'documentation_url'):
            if key in safe[sid]:
                url = urlsplit(safe[sid][key])
                if (url.scheme not in {'http', 'https'} or not url.netloc or url.username
                        or url.password or url.query or url.fragment):
                    del safe[sid][key]
    return safe


def _diagnostics(result, metrics):
    messages = []
    status = metrics['performance_status']
    unavailable = {
        'unsupported': 'Performance unavailable: requested experiment lacks required common input coverage.',
        'unresolved': 'Performance unavailable: unresolved exposure or pending orders; no final flat result exists.',
        'no_admissible_sessions': 'Performance unavailable: no admissible contract sessions. Excluded startup data is warm-up evidence only.',
        'missing_equity': 'Performance unavailable: no observed equity marks.',
        'accounting_mismatch': 'Performance unavailable: ledger, account totals or final equity do not reconcile.',
    }
    if status in unavailable:
        messages.append(unavailable[status])
    c = result.coverage
    if c.synthetic:
        messages.append('Synthetic mechanics demonstration only; numerical arithmetic is not historical performance.')
    if c.indicative:
        messages.append('Indicative prices or inputs are present; not verified historical performance or executable prices.')
    if c.unknown or not c.real:
        messages.append('Source provenance or historical availability is not fully verified.')
    if 'scenario' in c.margin_provenance:
        messages.append('Margin uses a scenario, not historical broker executability evidence.')
    variant = to_jsonable(result.manifest.get('variant', {}))
    if variant.get('omitted_rules'):
        messages.append('Reduced experiment; omitted_rules: ' + ', '.join(variant['omitted_rules']) + '. Full strategy performance is not established.')
    if not metrics['full_strategy_coverage']:
        messages.append('Full strategy coverage is not established; verified data and every required rule are necessary.')
    if metrics['performance_available'] and metrics['trade_count'] == 0:
        messages.append('Admissible observed period completed flat with no trades; trade distribution statistics are undefined.')
    if metrics['excluded_session_count']:
        messages.append('Excluded sessions (including data-only warm-up) are not strategy results; see roll_history and excluded_sessions.')
    messages.extend(result.manifest.get('limitations', ()))
    messages.extend(c.limitations)
    return tuple(dict.fromkeys(messages))


def _csv(path, columns, rows):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            encoded = to_jsonable(row)
            writer.writerow({key: json.dumps(value, sort_keys=True, allow_nan=False)
                if isinstance(value, (dict, list)) else value for key, value in encoded.items()})


def _table(rows):
    return '<table><tbody>' + ''.join('<tr><th>' + escape(str(key)) + '</th><td>'
        + escape('undefined' if value is None else str(value)) + '</td></tr>' for key, value in rows) + '</tbody></table>'


def _detail(title, value):
    return '<details><summary>' + escape(title) + '</summary><pre>' + escape(_json(value)) + '</pre></details>'


def write_report(result: ReplayResult, output_dir: Path) -> None:
    """Write six deterministic artifacts from derived replay evidence only.

    The manifest is additive to the replay schema (metrics, diagnostics, methods,
    config hash and unresolved exposure). CLI supplies its code revision. Callers
    must protect dataset/config input paths before choosing an output directory.
    """
    output_dir = Path(output_dir)
    metrics = compute_metrics(result)
    manifest = {k: v for k, v in result.manifest.items() if k in _MANIFEST_FIELDS}
    manifest['sources'] = _safe_sources(manifest.get('sources', {}))
    manifest['coverage'] = result.coverage
    manifest['config_sha256'] = sha256(json.dumps(to_jsonable(manifest['config']), sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    manifest['metrics'] = metrics
    manifest['report_diagnostics'] = _diagnostics(result, metrics)
    manifest['metric_methods'] = _METHODS
    manifest['unresolved_exposure'] = result.unresolved_exposure
    trades = []
    for row in result.trades:
        loss = max(0, -row.net_pnl_cents)
        trades.append(dict(to_jsonable(row), opportunity_id=json.dumps(opportunity_key(row), separators=(',', ':')),
            realized_loss_cents=loss, risk_overrun_cents=max(0, loss - row.planned_risk_cents)))
    trade_columns = [f.name for f in fields(TradeRecord)] + ['opportunity_id', 'realized_loss_cents', 'risk_overrun_cents']
    # Serialize before writing, so non-finite or otherwise invalid evidence cannot
    # leave a seemingly successful partial manifest.
    manifest_text, coverage_text = _json(manifest), _json(result.coverage)
    html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>MES offline research evidence</title><style>'
        'body{font:16px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#18242e;background:#f9fbfc}'
        'h1,h2{line-height:1.2}.diagnostics{padding:1rem 2rem;background:#fff1d6;border:2px solid #b56b13}'
        'table{border-collapse:collapse;background:white;width:100%}th,td{text-align:left;border:1px solid #cbd4db;padding:.35rem .7rem}'
        'th{width:50%}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:1rem 0;padding:.7rem;background:#edf2f5}'
        '</style></head><body><h1>MES offline research evidence</h1>'
        '<p>USD 10,000 starting simulated capital. Research release; no proven profitability claim.</p>'
        '<h2>Evidence and limitations</h2><ul class="diagnostics">'
        + ''.join('<li>' + escape(message) + '</li>' for message in manifest['report_diagnostics']) + '</ul>'
        + '<h2>Outcome</h2>' + _table(metrics.items())
        + '<h2>Unresolved exposure</h2><p>' + ('None recorded.' if not result.unresolved_exposure
            else 'Exposure remains. Completed flat performance is unavailable; inspect the exact lots below.') + '</p>'
        + (_detail('Unresolved lots and pending orders', result.unresolved_exposure) if result.unresolved_exposure else '')
        + '<h2>Reproducible artifacts</h2><p><a href="trades.csv">Exact FIFO trade slices</a> · '
        '<a href="equity.csv">Every observed equity mark</a> · <a href="skips.csv">Skip diagnostics</a> · '
        '<a href="coverage.json">Coverage JSON</a> · <a href="manifest.json">Manifest JSON</a></p>'
        + _detail('Trade slices: planned versus realized risk', trades)
        + _detail('Source, model and expiry metadata', manifest['sources'])
        + _detail('Coverage and missing rules', result.coverage)
        + _detail('Contract selection, roll and excluded sessions', {'roll_history': manifest.get('roll_history', ()),
            'excluded_sessions': manifest.get('excluded_sessions', ())})
        + _detail('Configuration, hashes and code revision', {key: manifest.get(key) for key in
            ('config', 'config_sha256', 'input_hashes', 'code_revision', 'variant', 'experiment_scope', 'margin_used')})
        + '<h2>Metric definitions</h2><ul>' + ''.join('<li>' + escape(message) + '</li>' for message in _METHODS)
        + '</ul></body></html>\n')
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'manifest.json').write_text(manifest_text, encoding='utf-8')
    (output_dir / 'coverage.json').write_text(coverage_text, encoding='utf-8')
    _csv(output_dir / 'trades.csv', trade_columns, trades)
    _csv(output_dir / 'equity.csv', ['sequence', 'at', 'equity_cents'],
         (dict(sequence=i, at=point.at, equity_cents=point.equity_cents) for i, point in enumerate(result.equity_curve)))
    _csv(output_dir / 'skips.csv', [f.name for f in fields(SkipRecord)], result.skips)
    (output_dir / 'report.html').write_text(html, encoding='utf-8')
