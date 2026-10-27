import json
import subprocess
import sys


def test_cli_reports_missing_inputs_with_incomplete_exit(prices_only_path, tmp_path):
    output = tmp_path / "audit.json"
    result = subprocess.run([sys.executable, "-m", "gamma_bot", "audit", "--dataset",
                             str(prices_only_path), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 2
    report = json.loads(output.read_text())
    assert {"gex", "cvd", "vix"} <= report["missing_rules"].keys()
    assert "gex" in result.stdout and "missing" in result.stdout.lower()


def test_cli_invalid_input_is_diagnostic_and_does_not_write_success(tmp_path, capsys):
    from gamma_bot.cli import main
    path = tmp_path / "broken.jsonl"
    path.write_text('{"bad": true}\n')
    output = tmp_path / "out.json"
    assert main(["audit", "--dataset", str(path), "--output", str(output)]) == 1
    assert "invalid" in capsys.readouterr().err.lower()
    assert not output.exists()


def test_cli_cannot_overwrite_input(prices_only_path, tmp_path, capsys):
    from gamma_bot.cli import main
    path = tmp_path / "events.jsonl"
    original = prices_only_path.read_bytes()
    path.write_bytes(original)
    assert main(["audit", "--dataset", str(path), "--output", str(path)]) == 1
    assert "invalid" in capsys.readouterr().err.lower()
    assert path.read_bytes() == original


def test_cli_supported_synthetic_coverage_returns_zero_with_synthetic_label(complete_synthetic_path, tmp_path, capsys):
    from gamma_bot.cli import main
    output = tmp_path / "audit.json"
    assert main(["audit", "--dataset", str(complete_synthetic_path), "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["classification"] == "synthetic"
    assert report["real"] is False and report["missing_rules"] == {}
    assert "synthetic" in capsys.readouterr().out
