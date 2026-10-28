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


def test_record_explicitly_reports_no_verified_source_and_writes_nothing(tmp_path, capsys):
    from gamma_bot.cli import main
    output = tmp_path / 'events.jsonl'
    checkpoint = tmp_path / 'checkpoint.json'
    assert main(['record', '--output', str(output), '--checkpoint', str(checkpoint),
                 '--stop-at', '2026-10-28T15:00:00Z']) == 2
    diagnostic = capsys.readouterr().out
    assert 'no_verified_source' in diagnostic
    assert 'No verified free permitted' in diagnostic
    assert list(tmp_path.iterdir()) == []


def test_record_rejects_naive_stop_time_without_writing(tmp_path, capsys):
    from gamma_bot.cli import main
    assert main(['record', '--output', str(tmp_path/'events.jsonl'), '--checkpoint',
                 str(tmp_path/'checkpoint.json'), '--stop-at', '2026-10-28T15:00:00']) == 1
    assert 'aware' in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
