import hashlib
import json

import pytest


def test_manifest_binds_bytes_config_revision_and_limitations(prices_only_path, tmp_path, monkeypatch):
    from gamma_bot.config import RunConfig
    from gamma_bot.data.provenance import write_manifest
    monkeypatch.setenv("IBKR_PASSWORD", "must-never-appear")
    output = tmp_path / "manifest.json"
    write_manifest(prices_only_path, RunConfig(), "abc123", output)
    result = json.loads(output.read_text())
    assert result["code_revision"] == "abc123"
    assert result["input_hashes"][prices_only_path.name] == hashlib.sha256(prices_only_path.read_bytes()).hexdigest()
    assert result["config"]["initial_equity_cents"] == 1_000_000
    assert result["source_ids"] == ["synthetic_fixture"]
    assert result["limitations"]
    assert "must-never-appear" not in output.read_text()


def test_metadata_credentials_are_rejected_before_manifest(dataset_writer, tmp_path):
    from gamma_bot.config import RunConfig
    from gamma_bot.data.models import InvalidDatasetError
    from gamma_bot.data.provenance import write_manifest
    from conftest import bar_record
    path = dataset_writer([bar_record()], metadata={"sources": [{
        "source_id": "synthetic", "classification": "synthetic", "api_key": "secret"}]})
    with pytest.raises(InvalidDatasetError):
        write_manifest(path, RunConfig(), "abc", tmp_path / "out.json")


@pytest.mark.parametrize("contents", [
    'risk_fraction = "0.02"', 'initial_equity_cents = 10000.0',
    'risk_fraction = "NaN"', 'required_rules = ["mes_prices"]',
    'vix_periods = 0', 'slippage_ticks = -1', 'api_key = "secret"',
])
def test_config_rejects_unit_errors_risk_breaches_and_implicit_filter_removal(tmp_path, contents):
    from gamma_bot.config import load_config
    from gamma_bot.data.models import InvalidDatasetError
    path = tmp_path / "config.toml"
    path.write_text(contents)
    with pytest.raises(InvalidDatasetError):
        load_config(path)


def test_config_loads_explicit_cost_scenario_without_rounding_money(tmp_path):
    from gamma_bot.config import load_config
    from decimal import Decimal
    path = tmp_path / "config.toml"
    path.write_text('round_trip_fee_cents = 500\nrisk_fraction = "0.005"\n')
    config = load_config(path)
    assert config.round_trip_fee_cents == 500
    assert config.risk_fraction == Decimal("0.005")


def test_manifest_cannot_overwrite_dataset_bytes(prices_only_path, tmp_path):
    from gamma_bot.config import RunConfig
    from gamma_bot.data.models import InvalidDatasetError
    from gamma_bot.data.provenance import write_manifest
    path = tmp_path / "events.jsonl"
    original = prices_only_path.read_bytes()
    path.write_bytes(original)
    with pytest.raises(InvalidDatasetError):
        write_manifest(path, RunConfig(), "abc", path)
    assert path.read_bytes() == original
