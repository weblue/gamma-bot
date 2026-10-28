# Gamma Bot

Offline MES research tools, starting with validated data import and an honest
coverage audit. The free-data feasibility outcome is **no_gex_history**: no
complete historical GEX overlap is verified, and no strategy performance or
profitability is established. See [the evidence](docs/research/data-feasibility.md).

Python 3.12 or later is required. Install locally:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
mkdir -p reports
.venv/bin/python -m gamma_bot audit --dataset tests/fixtures/synthetic-prices.jsonl --output reports/synthetic-audit.json
```

That hand-authored fixture checks mechanics only. Its audit exits **2** and
lists missing GEX, CVD, VIX, calendars, mapping and margin; it is not market
evidence. The audit exits **0** when every requested input capability has a
common date, **2** for valid incomplete inputs, and **1** for invalid files or
arguments. A synthetic supported dataset still carries its synthetic label.
The command currently audits the full required rule set.

Imports accept the [normalized event format](docs/research/event-format.md),
not arbitrary vendor CSV. The ignored Portara sample has unverified historical
availability and reuse semantics. Do not invent arrival times to normalize it.
No command downloads data, opens a broker session, reads credentials, or submits
orders. Raw data, credentials, the virtual environment and generated reports
belong outside version control.

[config/research.toml](config/research.toml) records the approved defaults,
including USD 10,000 simulated capital, at most 1% planned risk per trade, 3%
daily shutdown, and MES tick/cost units. Load it through
`gamma_bot.config.load_config(Path(...))`. Filter removal requires a separately
named strategy experiment; it cannot be achieved by shortening `required_rules`.

For reproducibility, `gamma_bot.data.provenance.write_manifest(dataset_path,
config, code_revision, output)` records input SHA-256 hashes, source metadata,
configuration and limitations. Supply the actual code revision. A manifest is
evidence of the imported bytes and settings, not proof of their historical truth.

Run the explicitly reduced, synthetic mechanics demonstration and open its
self-contained HTML report:

```sh
.venv/bin/python -m gamma_bot replay --dataset tests/fixtures/synthetic_replay --config config/research.toml --variant opening_range_baseline --output reports/mes-demo
# Open reports/mes-demo/report.html locally; no external assets are required.
```

Replay preserves its six JSON artifacts (`trades`, `equity_curve`, `skips`,
`unresolved_exposure`, `coverage`, `manifest`) and adds `trades.csv`, `equity.csv`,
`skips.csv`, and `report.html`. The manifest adds scalar `metrics`,
`report_diagnostics`, metric definitions, a canonical `config_sha256`, and
unresolved exposure, while retaining input hashes, code revision, source/model/
expiry metadata, variant omissions, margin, and contract-roll evidence. CSV
exports contain derived execution evidence, not the raw input market dataset.

The demo earns **no historical performance claim**: it is synthetic, omits full
strategy filters, and uses scenario margin. Its hand-computable ledger is gross
1,375 cents less 300 cents of fees, net 1,075 cents; final simulated equity is
1,001,075 cents and marked drawdown is 400 cents. Unsupported full experiments
still export coverage diagnostics and exit **2**; invalid inputs/output paths
exit **1**, and a completed replay exits **0**. A completed replay may still
have unresolved exposure or no admissible sessions: read `performance_status`
and `report_diagnostics` before using its metrics.

Trade count, win rate, expectancy, profit factor, streaks, and risk overruns use
whole opportunities grouped by strategy, trigger, dated contract, and New York
signal session. `trades.csv` retains every exact FIFO slice, its planned risk,
realized net loss, and original `target_ticks`; `exit_trigger_target_ticks`
separately records the target order responsible for an exit. Equity rows retain
repeated timestamps and a sequence index, so transient marked losses survive.
All monetary metrics use cents and all ratios use fractions; undefined values
are JSON null and HTML “undefined.” Profit factor is undefined without losses.
Exposure measures actual holding intervals divided by observed time in admitted
sessions; it does not imply complete intraday data coverage.

For independent experiments, use fresh strategy instances with
`gamma_bot.replay.engine.run_replay`, then
`gamma_bot.research.metrics.compute_metrics(result)` and
`gamma_bot.research.report.write_report(result, Path(output_dir))`. The report
writer produces its six required HTML/CSV/manifest/coverage artifacts; the CLI
also writes the four other JSON ledgers. Direct callers should supply
`code_revision` in a replaced result manifest and protect input paths when
choosing output locations. Neither function mutates the replay result.
Unsupported coverage, all-excluded startup/warm-up sessions, missing equity,
unreconciled accounting, and unresolved positions/pending orders make completed
performance unavailable rather than inventing zero-return results. Exact expiry
metadata is absent: delivery-month exclusion is a conservative research
restriction, not an asserted exchange rule. The report prominently retains that
restriction and the replay's fill, liquidity, margin and roll limitations.
See [replay policies](docs/research/replay.md) for the execution model.
