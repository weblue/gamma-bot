# Chronological MES experiments

```sh
.venv/bin/python -m gamma_bot evaluate \
  --dataset tests/fixtures/synthetic_replay \
  --config config/research.toml \
  --output /tmp/mes-evaluation
```

This command is offline. The checked-in fixture produces a **pipeline
demonstration**, not strategy validation: it contains preceding-session roll
volume and only a partial observed trading session. No verified historical GEX
intersection has been established, and this command does not fabricate one.

Study preflight validates one canonical actual GEX source/model/underlying/expiry
universe/provenance across all supplied inputs before any split or report is
written. Contradictory metadata or a change between development and holdout is
invalid. The study, comparison and every per-part run retain the same
`gex_identity`, including a part before the first snapshot or an unsupported
attempt; this is experiment provenance and never future feature availability.
No snapshots means an explicit `unselected` identity. Separate identities require
separate datasets/studies. Existing frozen studies need a new output path because
the experiment manifest now includes this validated identity.

The command writes `split.json` and `experiments.json` **before** running any
replay. The split sorts unique complete session dates, assigns the first
floor(70%) to development and the remainder to evaluation, and requires at least
one date in each part. The study requires USD 10,000 independent starting capital
and the 70/30 setting. Fewer than two complete dates freezes empty development
and evaluation sets; a separately labeled demonstration uses eligible observed
sessions, which may be partial. A demonstration never serves as validation.

Completeness is determined without signals, P&L, or GEX filter availability:

- The regular-session schedule and explicit macro-date coverage must be available
  by the open, and selected-contract margin must be available by the open.
- The existing forward-only roll selector must select a contract at the open
  using complete, causal preceding-session current/adjacent quarterly volume.
- The selected contract needs exactly one one-minute bar for every regular-session
  event-time minute from one source. Early closes use their supplied schedule.
  Duplicates and gaps fail this rule. Publication may follow the close: a present
  16:00 bar published at 16:00:01 still completes the event-time session. Actual
  publication controls replay admission and next-session roll evidence; an
  unpublished preceding-session bar cannot support the next open's roll.

The same calendar applies to full reversal, full breakout, their supported
single-filter removals and the opening-range baseline. Missing feature inputs
make the corresponding experiment unavailable; they do not change the common
calendar. Reversal removes VIX, CVD, wall migration or opening range separately;
breakout removes opening range only. Essential GEX, mapping and negative-profile
rules cannot be silently removed to manufacture a supported full hypothesis.

For every variant and part the frozen manifest records a reference configuration
plus one-at-a-time mapping windows of 300/600/900 trailing seconds, fees of
200/300/500 USD cents per round trip, and adverse slippage of 1/2/4 ticks per fill.
Reference duplicates are run once (seven scenarios per default variant). Mapping
requires at least five valid pairs and retains a higher configured minimum.
Capability audit uses the requested mapping window and minimum, including a
pair exactly at the trailing window's lower bound, as runtime mapping does.
Per-decision causality and freshness remain runtime gates. These scenarios do **not** measure
interactions between simultaneous parameter changes. There is no tuning,
winner selection, holdout optimization or funding recommendation.

Every run uses a fresh strategy and account, actual replay risk sizing, and its
own costs. Quantities are never copied from a cheaper run. Preceding sessions and
bars remain available for causal roll warm-up, but warm-up decisions, trades,
equity marks and time cannot enter the requested part. The first evaluation
session retains its preceding-session roll evidence. Later availability cannot
change development results.

A frozen artifact cannot be silently rewritten. Later dates, changed parameters,
changed input-byte hashes or changed logical Dataset contents require a new
study output path. Completeness policy version 2 uses event-time coverage;
`completeness_policy_version: 2` is frozen in the split and experiment manifests
and retained in comparisons. Studies frozen under the previous by-close
publication policy require a new study output path, even if their dates match.
The normalized Dataset SHA-256 also binds direct API calls
without importer byte hashes; no raw events are exported into study artifacts.

`comparison.json` and `comparison.html` retain every attempted part/variant/scenario,
including unavailable and zero-trade runs, requested versus admitted session
counts, opportunity counts, missing rules, economic metrics, code revision,
configuration hashes, source provenance and caveats. Baseline P&L differences
are descriptive comparisons on the same calendar/configuration/accounting, not
an inferred GEX edge. Each run has the ten existing replay JSON/CSV/HTML files
under `PART/VARIANT/SCENARIO/`. Report security allowlists and HTML escaping apply.
All target paths are checked against dataset/config inputs before any CLI output.

Session/opportunity counts provide sample context. Dependent opportunities, small
samples, synthetic/indicative/unknown inputs, scenario margin, unresolved
exposure, approximate fills and unverified expiry metadata limit inference.
No confidence intervals, significance, profitability or broker executability
claims are made. Existing metric availability gates remain authoritative; null
performance must not be treated as zero return. CLI exit 0 means the study was
written (including honest unavailable attempts); exit 1 indicates invalid input
or output. The `replay` and `audit` exit codes are unchanged.

## Python APIs

```python
from gamma_bot.research.experiments import (
    complete_session_dates, freeze_split, default_variants, evaluate,
)
from gamma_bot.research.report import write_comparison

# Optional standalone split; callers supply complete dates.
development, evaluation = freeze_split(complete_session_dates(dataset), split_path)
results = evaluate(dataset, config, default_variants(), split_path)
```

`freeze_split(Sequence[date], Path)` returns a pair of immutable date tuples and
persists the current completeness-policy version. `complete_session_dates` counts
complete event-time bars present in the Dataset without a by-close publication
cutoff; actual availability still gates causal roll selection and replay.
`evaluate(Dataset, RunConfig, Sequence[Variant], Path)` returns an immutable
mapping of `part/variant/scenario` to the existing `ReplayResult`. It freezes
`experiments.json` beside the split file and returns every actual replay result;
it does not write individual reports. Direct callers own input-path protection.
`experiment_plan` builds the preflight plan and `sensitivity_configs` exposes
actual frozen candidates. `write_comparison(results, experiment_manifest,
output_dir)` writes the two comparison artifacts; `write_report` is unchanged.

Replay adds the optional keyword `admitted_session_dates: Sequence[date] | None`.
`None` preserves existing replay behavior. An explicit tuple retains other
sessions solely as roll-volume warm-up, audits coverage on admitted dates, and
excludes later dates from the account. An empty tuple admits no results.
`audit_dataset` accepts optional keyword `config: RunConfig | None`; only the
mapping capability window/minimum are configured. Without it the established
default mapping window/minimum apply; the shared causal pairing and selected-contract runtime margin gates still apply.
