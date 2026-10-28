from dataclasses import replace
import csv
import json
from pathlib import Path

import pytest

from gamma_bot.cli import main
from gamma_bot.data.io import load_dataset
from gamma_bot.data.models import RunConfig
from gamma_bot.replay.engine import run_replay
from gamma_bot.research.report import write_report
from gamma_bot.strategies import OpeningRangeBaseline, ReversalStrategy
from .conftest import result_for, trade

FIXTURE = Path('tests/fixtures/synthetic_replay')


def replay(strategy=None):
    strategy = strategy or OpeningRangeBaseline()
    return run_replay(load_dataset(FIXTURE), RunConfig(), strategy.variant, strategies=[strategy])


def test_synthetic_report_exports_exact_ledger_and_reconciles_marks(tmp_path):
    result = replay()
    write_report(result, tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {'manifest.json', 'coverage.json', 'trades.csv', 'equity.csv', 'skips.csv', 'report.html'}
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    m = manifest['metrics']
    assert m['performance_status'] == 'synthetic_mechanics'
    assert (m['net_pnl_cents'], m['return_fraction']) == (1075, 0.001075)
    assert m['max_drawdown_cents'] == 400
    assert m['historical_performance_available'] == 0
    with (tmp_path / 'trades.csv').open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]['target_ticks'] == '24017'
    assert rows[0]['exit_trigger_target_ticks'] == '24017'
    assert rows[0]['planned_risk_cents'] == '1175'
    assert rows[0]['realized_loss_cents'] == '0'
    assert sum(int(row['net_pnl_cents']) for row in rows) + 1_000_000 == manifest['ending_equity_cents']
    with (tmp_path / 'equity.csv').open() as f:
        marks = list(csv.DictReader(f))
    assert len(marks) == len(result.equity_curve)
    assert min(int(row['equity_cents']) for row in marks) == 999600
    assert int(marks[-1]['equity_cents']) == 1001075
    html = (tmp_path / 'report.html').read_text()
    for phrase in ('Synthetic mechanics', 'scenario', 'omitted_rules', 'Delivery-month', 'expiry', 'Roll volume', '1075'):
        assert phrase in html
    assert 'http://cdn' not in html and '<script' not in html
    assert manifest['input_hashes'] and len(manifest['config_sha256']) == 64
    assert manifest['variant']['omitted_rules']
    assert 'events.jsonl' in manifest['input_hashes'] and 'events' not in manifest


def test_html_escapes_source_and_report_text_and_preserves_model_metadata(tmp_path):
    result = result_for((replace(trade(-1500), exit_trigger_target_ticks=24010),))
    sources = {'<script>alert(1)</script>': {'model_id': '<img src=x onerror=alert(1)>',
        'expiry_days': (0, 7), 'api_key': 'DO-NOT-EXPORT', 'raw_events': ['licensed'],
        'documentation_url': 'https://example.com/?token=DO-NOT-EXPORT'}}
    result = replace(result, manifest=dict(result.manifest, sources=sources, raw_events=['licensed']))
    write_report(result, tmp_path)
    html = (tmp_path / 'report.html').read_text()
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert '<script>' not in html and '<img src=x' not in html
    assert '&lt;script&gt;' in html and '&lt;img' in html
    assert manifest['sources']['<script>alert(1)</script>']['expiry_days'] == [0, 7]
    assert 'DO-NOT-EXPORT' not in ''.join(p.read_text() for p in tmp_path.iterdir())
    with (tmp_path / 'trades.csv').open() as f:
        row, = csv.DictReader(f)
    assert (row['target_ticks'], row['exit_trigger_target_ticks']) == ('24020', '24010')
    assert (row['realized_loss_cents'], row['risk_overrun_cents']) == ('1500', '500')


@pytest.mark.parametrize('kind', ['unsupported', 'unresolved', 'indicative', 'excluded'])
def test_caveats_appear_in_machine_and_html_outputs(kind, tmp_path):
    result = replay(ReversalStrategy()) if kind == 'unsupported' else replay()
    if kind == 'unresolved':
        result = replace(result, unresolved_exposure={'position_quantity': 1, 'pending_quantity': 0})
    elif kind == 'indicative':
        result = replace(result, coverage=replace(result.coverage, synthetic=False, indicative=True, classification='indicative'))
    elif kind == 'excluded':
        rows = tuple(dict(row, contract=None) for row in result.manifest['roll_history'])
        result = replace(result, manifest=dict(result.manifest, roll_history=rows, excluded_sessions=rows))
    write_report(result, tmp_path)
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    html = (tmp_path / 'report.html').read_text()
    if kind != 'indicative':
        assert manifest['metrics']['return_fraction'] is None
        assert 'Performance unavailable' in html
    else:
        assert 'Indicative' in html and manifest['metrics']['historical_performance_available'] == 0
    assert manifest['report_diagnostics'] and 'Unresolved exposure' in html
    assert json.loads((tmp_path / 'coverage.json').read_text())['classification'] == result.coverage.classification


def test_cli_preserves_json_and_adds_reports_deterministically(tmp_path):
    one, two = tmp_path / 'one', tmp_path / 'two'
    for output in (one, two):
        assert main(['replay', '--dataset', str(FIXTURE), '--config', 'config/research.toml', '--variant', 'opening_range_baseline', '--output', str(output)]) == 0
    assert {p.name for p in one.iterdir()} == {'manifest.json', 'coverage.json', 'trades.json', 'equity_curve.json', 'skips.json', 'unresolved_exposure.json', 'trades.csv', 'equity.csv', 'skips.csv', 'report.html'}
    for path in one.iterdir():
        assert path.read_bytes() == (two / path.name).read_bytes()
    assert json.loads((one / 'manifest.json').read_text())['code_revision']


def test_cli_preflights_report_symlinks_before_any_output(tmp_path):
    target = tmp_path / 'config.toml'
    target.write_text(Path('config/research.toml').read_text())
    output = tmp_path / 'report'
    output.mkdir()
    (output / 'trades.csv').symlink_to(target)
    original = target.read_bytes()
    assert main(['replay', '--dataset', str(FIXTURE), '--config', str(target), '--variant', 'opening_range_baseline', '--output', str(output)]) == 1
    assert target.read_bytes() == original
    assert not (output / 'manifest.json').exists()
