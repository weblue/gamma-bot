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
