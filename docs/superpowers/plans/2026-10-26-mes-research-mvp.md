# MES Research MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible MES research workflow that establishes free-data feasibility, replays supported strategy variants, and produces inspectable evidence without inventing missing inputs.

**Architecture:** One local Python package shares causal feature, strategy, risk, and accounting logic across historical replay and later execution. External data enters through validated, timestamped files; provider-specific adapters are added only after the data audit establishes free access and semantics. The IBKR paper adapter is a separate operational subproject after the research interfaces and account prerequisites are established.

**Tech Stack:** Python 3.12+ (local interpreter observed: 3.14.5), standard-library dataclasses/Decimal/zoneinfo/argparse/tomllib/json/csv/hashlib/sqlite3, pytest for behavioral tests, JSONL raw/normalized events, CSV ledgers, self-contained HTML reports. Add pandas/NumPy/Parquet dependencies only if a validated source requires them; do not install broker or web-service frameworks in this slice.

**Spec:** [Approved MES design](../specs/2026-10-26-mes-mvp-design.md). Read both documents. User approved the design and its proposed defaults on 2026-10-26.

**Implementation status (2026-10-28):** All nine research tasks implemented and individually reviewed. Final whole-branch acceptance is in progress. The source audit remains `no_gex_history` / `no_verified_source`; no historical strategy validation or broker execution is claimed. See [research status](../../research/implementation-status.md).

## Global Constraints

- Python implementation; MES futures are the only traded instrument.
- Historical research starts with USD 10,000 of simulated capital.
- Planned risk is at most 1% of current account equity per trade, rather than an aggregate 1% portfolio limit.
- No overnight positions. Close positions before the regular US equity session closes, including early closes, even though MES has a longer trading session.
- Data budget is USD 0. No paid subscription, purchase, or presumed future funding is a dependency.
- All timestamps below are event availability times, not just chart labels. A signal is actionable only after all confirmations are available.
- Missing required inputs make a setup ineligible in the full strategy. They are never interpreted as a passed filter.
- Prices and order quantities must respect MES units: 0.25 points per tick, USD 5 per point, USD 1.25 per tick; whole contracts only.
- Preserve the accepted defaults table in the spec. In particular: entries 09:45 inclusive–10:30 exclusive ET; one position; flatten 5 minutes before close; 3% daily equity shutdown; 10 completed 1-minute VIX values; 2R exits; 1 tick slippage per fill; USD 3 round-trip fee scenario.
- No paid data, options orders, ES orders, live order submission, web dashboard, or claim of proven profitability in this research release.
- Preserve existing work. Check repository instructions/status before changes. Use an isolated worktree at execution time under the applicable skill; do not create it during planning.

## Review Focus

1. A dataset downloaded today can contain values unavailable at the simulated decision: reject future-as-of information, including OI publication and completed-bar/pivot timing. Tests belong to Tasks 2, 4, and 5.
2. Missing, stale, delayed, or indicative inputs must not silently become passed filters or executable quotes. Tests belong to Tasks 2, 4, and 7.
3. A macro blackout, DST transition, early close, missing session schedule, or missing contract roll boundary must produce correct entry/exit decisions or an explicit skip. Tests belong to Tasks 3 and 6.
4. A gap, ambiguous OHLC bar, partial session, adverse fill, or quantity rounding can overstate attainable performance. Tests belong to Tasks 3, 6, and 7.
5. A disconnected or restarted forward recorder can duplicate or corrupt observations and leak credentials into artifacts. Tests belong to Task 9; broker-order recovery is reserved for the separate paper-adapter plan.

## Delivery stages and data gates

Tasks 1–3 deliver a useful data-audit CLI and verified mechanics independently of a profitable strategy or a free GEX archive. Tasks 4–8 add the research workflow; their mechanical tests may use clearly labeled fixtures, but market results require real data. Task 9 records forward observations only for validated free sources. Keep source/account-dependent tasks conditional rather than substituting an unapproved feed.

Task 1 must finish before committing to any provider-specific runtime dependency. No usable historical GEX is an acceptable audit finding, not permission to fabricate backtest results. Continue with reusable replay/import/report tooling, explicitly report missing performance evidence, and identify the exact access needed. If a source choice requires materially different strategy semantics, update the design before enabling that source; routine format/library choices are implementation decisions.

## File and interface map

| Files | Responsibility |
| --- | --- |
| `docs/research/data-feasibility.md`, `data/sources.example.toml` | Evidence, source access, capabilities, permitted use, and overlapping sample range |
| `pyproject.toml`, `.gitignore`, `README.md`, `src/gamma_bot/__init__.py`, `__main__.py`, `cli.py`, `config.py` | Package/CLI and validated research configuration; no setup-only task |
| `src/gamma_bot/data/models.py`, `io.py`, `coverage.py`, `provenance.py` | Typed normalized records, streaming import, coverage and manifests |
| `src/gamma_bot/replay/calendar.py`, `risk.py`, `account.py` | Session/macro rules, sizing, equity, reservations, daily shutdown |
| `src/gamma_bot/features/gex.py`, `mapping.py`, `order_flow.py`, `indicators.py`, `state.py` | Causal GEX/profile construction/import, mapping, CVD/pivots, VIX/opening range |
| `src/gamma_bot/strategies/models.py`, `reversal.py`, `breakout.py`, `baseline.py` | Pure signals and explicit variant/filter requirements |
| `src/gamma_bot/replay/fills.py`, `engine.py` | Deterministic clock, orders, fills, exits, and position lifecycle |
| `src/gamma_bot/research/experiments.py`, `metrics.py`, `report.py` | Frozen development/evaluation splits, experiment comparisons, ledgers/reports |
| `src/gamma_bot/data/recorder.py`, `adapters/` | Verified free-source recording; provider modules are named after the source selected in Task 1 |
| `tests/data/`, `tests/replay/`, `tests/features/`, `tests/strategies/`, `tests/research/`, `tests/fixtures/` | Behavioral tests and explicitly synthetic examples |

### Shared types (owned by Task 2 unless noted)

Define immutable dataclasses and a documented JSONL representation in `docs/research/event-format.md`. Every market event has `source_id: str`, `sequence: int`, `event_at: datetime`, and `available_at: datetime`, all times aware UTC. Decimal values serialize as strings; prices for MES orders/fills serialize as integer ticks. Reject non-finite numbers and malformed/unknown required fields.

- `MesBar`: common fields plus `contract: str`, `start_at: datetime`, `open_ticks/high_ticks/low_ticks/close_ticks: int`, `volume: int`; `event_at` is the bar end.
- `Quote`: common fields plus `contract: str`, `bid_ticks/ask_ticks: int`.
- `Trade`: common fields plus `contract: str`, `price_ticks: int`, `size: int`, `aggressor: Literal['buy','sell','unknown']`.
- `PricePoint`: common fields plus `symbol: str`, `value: Decimal`, `price_kind: Literal['mid','index','indicative']`; used for VIX index observations and the GEX underlying's actual midpoint where available. Full mapping accepts actual midpoints, not index/indicative substitutions. Never substitute an adjusted equity close for an actual contemporaneous midpoint without a named approximation.
- `Wall`: `wall_id: str`, `kind: Literal['support','resistance']`, `strike: Decimal`, `concentration: Decimal` (absolute contribution).
- `NegativeRegion`: `lower: Decimal | None`, `upper: Decimal | None`, `region_id: str`; boundaries and membership convention are recorded with the model.
- `GexSnapshot`: common fields plus `underlying: str`, `spot/total_gex: Decimal`, `walls: tuple[Wall,...]`, `negative_regions: tuple[NegativeRegion,...]`, `profile_available: bool`, `model_id: str`, `expiry_days: tuple[int,...]`, `oi_published_at: datetime`, `provenance: Literal['vendor','modeled','indicative']`. An available all-positive profile has no negative regions; that is different from an unavailable profile.
- `OptionRow`: `kind: Literal['call','put']`, `strike: Decimal`, `expires_at: datetime`, `multiplier/oi: int`, `oi_published_at: datetime`, `gamma/iv: float | None`; `OptionSnapshot` has common fields plus `underlying: str`, `spot: Decimal`, `rows: tuple[OptionRow,...]`, `annual_rate/dividend_yield: float | None`, and rate/dividend provenance. These are model inputs, not observed dealer positions.
- `Event = MesBar | Quote | Trade | PricePoint | GexSnapshot | OptionSnapshot`.
- `Session`: `session_date: date`, `open_at/close_at/schedule_available_at: datetime`.
- `MacroRelease`: `event_id/category: str`, `scheduled_at/schedule_available_at: datetime`; categories are CPI, PPI, EmploymentSituation, FOMCDecision, FOMCPressConference, ISMManufacturing, ISMServices.
- `MarginPoint`: `contract: str`, `available_at: datetime`, `initial_cents/maintenance_cents: int`, `provenance: Literal['historical','scenario']`.
- `Dataset`: immutable events, sessions, macro releases, margin points, source metadata, and `CoverageReport`; no embedded credentials.
- `CoverageReport`: `supported_rules: frozenset[str]`, `missing_rules: Mapping[str,str]`, intersection dates, per-source freshness/gaps, real/synthetic/indicative flags, and margin provenance. Rule IDs are `mes_prices`, `gex`, `negative_regions`, `mapping`, `cvd`, `vix`, `wall_migration`, `opening_range`, `macro`, and `margin`; each strategy declares its own required subset.
- `RunConfig`: accepted defaults from the spec with `initial_equity_cents=1_000_000`, `risk_fraction=Decimal('0.01')`, `daily_loss_fraction=Decimal('0.03')`; configuration validates units and forbids implicit filter removal.
- `InvalidDatasetError` identifies corrupt/invalid input; `CapabilityError` identifies unavailable input/model capabilities. CLI commands distinguish these from a valid zero-trade outcome.

Types introduced later are owned by their task, and import the shared types above rather than duplicating them. Runtime imports must not submit orders or fetch network data as a side effect.

### Task 1: Establish free-data feasibility and a real sample

**Files:** Create `docs/research/data-feasibility.md`, `data/sources.example.toml`. Store downloaded raw samples under ignored `data/raw/` only when permitted; put URLs/checksums/schema notes in the report. Do not commit licensed market files, credentials, or a fabricated sample.

**Interfaces:** Produces a source capability matrix and source configuration with `id`, `homepage`, `documentation_url`, `access_status`, `cost_usd`, `automation_status`, `kinds`, `timestamp_semantics`, `coverage_start/end`, `oi_semantics`, `model_semantics`, `sample_path`, `sample_sha256`, and `limitations`. Missing sample fields are explicitly unavailable, not invented. Subsequent tasks consume these records.

- [x] **Step 1:** Inspect the sources in the design and current primary documentation. Check GEXtool exports, Alpaca actual historical/OPRA/OI entitlements, Tradier included-account access and Greek cadence, IBKR existing entitlements, and downloadable MES samples. Separate free dashboard viewing, free trials, account-included APIs, and freely downloadable archives. Do not enroll in paid access or assume credentials exist.
- [x] **Step 2:** Inspect accessible sample bytes and timestamps. For a candidate historical day, try to establish overlapping MES execution prices, source-underlying prices, GEX/profile, VIX, CVD inputs, macro schedule, and margin evidence. Record exactly which checks were actually made; inaccessible account-gated endpoints remain unverified. Check official exchange/BLS/Federal Reserve/ISM calendars for the same sample dates rather than inventing release times.
- [x] **Step 3:** Write the matrix and choose `full`, `reduced`, or `no_gex_history` as the evidence-backed outcome. Name the viable GEX source/model/expiry universe, or record why no source is viable. For any reduced variant, list omitted rules explicitly. Define retention/automation permissions and whether ticks support real aggressor flags or only inferred CVD.
- [x] **Step 4:** Verify every claimed sample exists, hashes to its recorded SHA-256, and has a real date/contract. If none is accessible, the report must list exact failed/missing access and contain no claimed sample or performance. Review `git diff --check` and the report against the evidence; this documentation task does not need source-text tests.
- [x] **Step 5:** Commit only the report and example metadata: `git add docs/research/data-feasibility.md data/sources.example.toml` then `git commit -m "docs: establish free MES research data coverage"`.

### Task 2: Build validated import and the data-audit CLI

**Files:** Create package/CLI/config files and `data/{models,io,coverage,provenance}.py` from the map; `docs/research/event-format.md`; `tests/data/test_io.py`, `test_coverage.py`, `test_provenance.py`, `tests/test_cli.py`; labeled small fixtures under `tests/fixtures/`. Modify `README.md` and `.gitignore` as part of this deliverable.

**Interfaces:** `load_config(path: Path) -> RunConfig`; `load_dataset(path: Path, *, as_of: datetime | None = None) -> Dataset`; `audit_dataset(dataset: Dataset, required_rules: frozenset[str]) -> CoverageReport`; `write_manifest(dataset_path: Path, config: RunConfig, code_revision: str, output: Path) -> None`; `main(argv: Sequence[str] | None = None) -> int`. First command: `python -m gamma_bot audit --dataset PATH --output PATH`. Exit 0 for supported coverage, 2 for valid but incomplete coverage, 1 for invalid input; always produce a readable diagnostic.

`load_dataset` accepts a dataset directory containing metadata and normalized tables, or a normalized event-only JSONL file. The latter has empty calendar/margin tables and unknown provenance unless sidecar metadata establishes it; it cannot claim full coverage or real historical evidence merely because parsing succeeded.

- [x] **Step 1:** Write failing tests for malformed decimals/NaN, negative quantities, crossed quotes, invalid OHLC, non-MES execution contracts, naive timestamps, OI published after snapshot availability, bars available before completion, conflicting duplicates, and credential-free manifests. Assert an otherwise valid price-only dataset has `missing_rules` containing GEX/CVD/VIX, is not classified full, and the CLI returns 2 with explicit missing inputs. Exact duplicate events may be deduplicated by source/sequence/hash; conflicting duplicates fail.

```python
def test_price_only_input_does_not_claim_full_coverage(prices_only_dataset):
    report = audit_dataset(prices_only_dataset, frozenset({'gex', 'cvd', 'vix'}))
    assert {'gex', 'cvd', 'vix'} <= report.missing_rules.keys()

def test_rejects_oi_unavailable_at_snapshot(future_oi_dataset_path):
    with pytest.raises(InvalidDatasetError):
        load_dataset(future_oi_dataset_path)
```

Define named fixture builders in the owning test file or `tests/conftest.py`; they construct actual schema records, not a mocked copy of production validation. Later tasks can extend the same builders.

- [x] **Step 2:** Run `python3 -m pytest tests/data tests/test_cli.py -q`; verify failure is caused by absent implementation. If pytest is absent, create `.venv`, declare the development extra, and install the local package with that extra; do not call a missing test runner a red test.
- [x] **Step 3:** Implement the interfaces and typed schema. Use a src-layout package and `python -m gamma_bot`; ignore `.venv/`, raw data, credentials, and generated reports. Persist source IDs, content hashes, code revision, config, and limitations. No network download or broker connection during import/audit.
- [x] **Step 4:** Run `.venv/bin/python -m pytest tests/data tests/test_cli.py -q` and audit the real sample if Task 1 found one. Expected: tests pass; real CLI outcome agrees with actual coverage, including exit 2 when incomplete. Without a real sample, only synthetic mechanics are verified and the report says so.
- [x] **Step 5:** Review the diff, run `git diff --check`, stage the exact files listed above, and commit `feat: validate research data and report coverage`.

### Task 3: Implement session gates, account accounting, and risk sizing

**Files:** Create `replay/calendar.py`, `risk.py`, `account.py`, `tests/replay/test_calendar.py`, `test_risk.py`, `test_account.py`.

**Interfaces:** `entry_gate(now: datetime, session: Session, releases: Sequence[MacroRelease], *, schedules_verified: bool) -> tuple[bool, tuple[str,...]]`; `flatten_deadline(session: Session, releases: Sequence[MacroRelease], now: datetime) -> datetime`; `size_trade(*, equity_cents: int, free_margin_cents: int, initial_margin_cents: int, stop_ticks: int, round_trip_fee_cents: int, slippage_ticks_per_fill: int) -> int`; `Account(initial_equity_cents: int)`, `Account.mark(price_ticks: int) -> int`, `Account.reserve(order_id: str, quantity: int, margin_cents: int) -> None`, `Account.apply_fill(fill: Fill) -> None`, `Account.daily_shutdown: bool`. Own `Fill(order_id, contract, side, at, quantity, price_ticks, fee_cents)` with buy/sell side. Marked equity and realized/unrealized P&L remain separate; initial margin is reserved collateral, not an expense.

- [x] **Step 1:** Write failing tests with exact economics: at USD 10,000, 40 stop ticks cost USD 50 per MES plus USD 3 fees and USD 2.50 two-fill slippage; risk-only sizing allows 1 contract, while insufficient margin forces 0. At 76 stop ticks the USD 100.50 planned loss forces 0. Assert unchanged stops when sizing rejects. Assert a one-contract 8-tick profit is USD 10 gross, USD 7 net under USD 3 total fees, with margin release not counted as a profit.

```python
def test_costs_and_whole_contracts_limit_size():
    assert size_trade(equity_cents=1_000_000, free_margin_cents=1_000_000,
                      initial_margin_cents=100_000, stop_ticks=40,
                      round_trip_fee_cents=300, slippage_ticks_per_fill=1) == 1
    assert size_trade(equity_cents=1_000_000, free_margin_cents=1_000_000,
                      initial_margin_cents=100_000, stop_ticks=76,
                      round_trip_fee_cents=300, slippage_ticks_per_fill=1) == 0
```

- [x] **Step 2:** Add calendar/shutdown tests: 09:44:59 rejects, 09:45 accepts, 10:30 rejects; a 13:00 early close flattens at 12:55 local through DST conversion; a 10:00 release requires flattening from 09:25, blocks through 10:30 inclusive, and never blocks exits. Union overlapping releases. Missing schedule and missing margin reject entries. A USD 300 session-equity decline activates shutdown for the remaining session even after a later recovery.

```python
def test_early_close_uses_local_session_time(early_close_session):
    deadline = flatten_deadline(early_close_session, (), early_close_session.open_at)
    assert deadline.astimezone(ZoneInfo('America/New_York')).strftime('%H:%M') == '12:55'
```

- [x] **Step 3:** Run `.venv/bin/python -m pytest tests/replay/test_calendar.py tests/replay/test_risk.py tests/replay/test_account.py -q` and verify the expected failures, then implement these interfaces. Risk sizing returns `max(0, min(risk_capacity, margin_capacity))` with explicit integer rounding; freeze session-start equity and reset only at the next session.
- [x] **Step 4:** Rerun those tests. Confirm no test assumes stops guarantee maximum realized loss, account balances cannot borrow hypothetical capital, and unknown/unrelated positions block future entries.
- [x] **Step 5:** Review/stage the six files and commit `feat: enforce MES risk and session limits`.

### Task 4: Build causal features and the GEX import/model boundary

**Files:** Create feature modules from the map and `tests/features/test_indicators.py`, `test_order_flow.py`, `test_mapping.py`, `test_gex.py`, `test_state.py`.

**Interfaces:** `FeatureEngine.update(event: Event) -> FeatureState`; `map_levels(snapshot: GexSnapshot, paired_prices: Sequence[tuple[PricePoint, PricePoint]], as_of: datetime) -> MappedGex | None`; `gex_from_options(snapshot: OptionSnapshot) -> GexSnapshot`; `FeatureState` owns `as_of`, `contract`, `bar`, `previous_bar`, `opening_range`, `vix`, `current_gex`, `previous_gex`, `wall_history`, `divergences`, and `unavailable_rules`. Define `OpeningRange(high_ticks, low_ticks)`, `VixState(current, previous, sma10, available_at)`, `MappedGex(snapshot, ratio, walls, negative_regions, available_at)`, `MappedWall(wall_id, kind, level_ticks, concentration)`, `MappedRegion(region_id, lower_ticks, upper_ticks)`, and `Divergence(direction, first_at, second_at, confirmed_at, second_pivot_ticks, cvd_first, cvd_second)` here. Preserve original source levels/model metadata as well as mapped ticks.

- [x] **Step 1:** Write failing tests showing that opening range excludes the bar starting at 09:45; 9 VIX observations yield no SMA10; strict equality to SMA10 or 22 fails eligibility later; only events available by the decision can affect features. Assert the second low cannot produce a divergence until its 2 following bars have completed. Unknown aggressor trades do not silently contribute zero as valid CVD; use a declared quote/tick classifier where possible or mark CVD unavailable. Reset CVD at the equity session open and reset state at a contract switch.

```python
def test_divergence_waits_for_two_completed_confirmation_bars(divergence_events):
    engine = FeatureEngine()
    for event in divergence_events[:-1]:
        assert engine.update(event).divergences == ()
    state = engine.update(divergence_events[-1])
    assert state.divergences[0].confirmed_at == divergence_events[-1].available_at
```

- [x] **Step 2:** Add mapping/wall tests: 5 paired observations with ratios `[10,10,11,10,10]` map a SPY 600 strike to MES 6000; a future or over-60-second-old pair is ineligible; a fixed 10x substitute is never used. Three snapshots at 09:40/09:43/09:45 can establish stability; 15-minute-only snapshots cannot. A wall changing strike or decreasing concentration fails, and a wall moving alone cannot manufacture a price crossing.

```python
def test_mapping_uses_causal_median(spy_600_snapshot, paired_midpoints, decision_time):
    mapped = map_levels(spy_600_snapshot, paired_midpoints, decision_time)
    assert mapped.ratio == Decimal('10')
    assert mapped.walls[0].level_ticks == 24_000
```

- [x] **Step 3:** Run `.venv/bin/python -m pytest tests/features -q` to verify failures. Implement as-of joins with deterministic event ordering, freshness limits from the spec, confirmed pivot detection, and separate provider-import versus modeled GEX provenance.
- [x] **Step 4:** If Task 1 supports local reconstruction, implement `gex_from_options`: use as-of gamma or a documented European Black–Scholes gamma approximation with annualized inputs and actual seconds to expiry/365-day year; require positive finite spot/strike/IV and reject expired contracts. Use the spec's signed GEX formula, 0–7 calendar-day expiries, latest already-published OI, and recorded rate/dividend inputs. A negative-region profile requires re-evaluation with fixed per-contract IV, not current gamma alone. Record the sampling/refinement algorithm and boundary precision with the model ID; require boundary convergence finer than one mapped MES tick and verify sign on either side. With unavailable inputs, return a precise capability failure rather than zero GEX or invented IV/rates.
- [x] **Step 5:** Add hand-checkable formula/sign tests and a boundary-convergence test if the local model is enabled; prove strike-level negative GEX alone does not enable negative-region signals. Run all feature tests and verify any real-data transformation against its input timestamps/provenance. Synthetic option/profile fixtures remain labeled test-only.
- [x] **Step 6:** Review/stage the feature modules/tests and commit `feat: derive causal gamma and confirmation features`.

### Task 5: Implement the two strategy hypotheses and a matched baseline

**Files:** Create strategy modules from the map and `tests/strategies/test_reversal.py`, `test_breakout.py`, `test_baseline.py`.

**Interfaces:** Own `Signal(strategy, contract, direction, decision_at, reference_ticks, stop_ticks, trigger_id)`; direction is long/short. Define `Strategy` as a protocol with `on_state(state: FeatureState) -> Signal | None`, `on_entry_filled(signal: Signal) -> None`, and `reset_session(session_date: date) -> None`. `ReversalStrategy`, `BreakoutStrategy`, and `OpeningRangeBaseline` implement it. Own `Variant(name: str, enabled_filters: frozenset[str], omitted_rules: tuple[str,...])`. Full strategies require all specified inputs; a separately named reduced variant can omit only explicitly configured filters. Signals are proposals; Task 6 applies session/account/risk/fill gates.

- [x] **Step 1:** Write failing reversal tests for both directions with a stable aligned wall, strict VIX checks, confirmed CVD divergence, close back across the wall, and structural stop 1 tick beyond the second pivot. Remove each required input in turn and assert no full-strategy signal. A signal's `decision_at` must be at/after pivot confirmation, never the earlier pivot time.

```python
def test_reversal_uses_confirmed_structural_stop(valid_long_reversal_state):
    signal = ReversalStrategy().on_state(valid_long_reversal_state)
    divergence = valid_long_reversal_state.divergences[-1]
    assert signal.stop_ticks == divergence.second_pivot_ticks - 1
    assert signal.decision_at >= divergence.confirmed_at
```

- [x] **Step 2:** Write failing breakout tests for both directions: prior-close-frozen boundary, new price close at least 1 tick into the negative region, newest profile also negative, and ORH/ORL alignment within 1 point. A moving boundary with unchanged price produces no signal. VIX above 22 does not independently reject a breakout, while missing region semantics does. Stop is 1 tick beyond the signal bar's opposite extreme.

```python
def test_profile_migration_is_not_a_price_breakout(migrating_boundary_state):
    assert BreakoutStrategy().on_state(migrating_boundary_state) is None
```

- [x] **Step 3:** Run `.venv/bin/python -m pytest tests/strategies -q` and verify failures; implement the interfaces and the exact rules from the design. Trigger IDs use source/model, session, strategy, direction, and source wall/region identity. Once an entry fills, permit only one entry per strategy/direction/trigger per session; rejected/unfilled proposals do not consume the trigger. No immediate same-bar reversal.
- [x] **Step 4:** Implement the baseline with the same opening range, session, account, fill, stop, and target machinery, but without GEX/confirmation filters. Reduced variants list omissions in every output; they never inherit the full-strategy name. Rerun strategy tests.
- [x] **Step 5:** Review/stage the strategy modules/tests and commit `feat: define MES reversal and breakout experiments`.

### Task 6: Connect deterministic replay, conservative fills, and exits

**Files:** Create `replay/fills.py`, `engine.py`, `tests/replay/test_fills.py`, `test_engine.py`; modify `cli.py` for `replay`.

**Interfaces:** `run_replay(dataset: Dataset, config: RunConfig, variant: Variant, *, strategies: Sequence[Strategy]) -> ReplayResult`; invoke the Task 5 protocol for decisions, successful entries, and session resets. Own `TradeRecord` with strategy/trigger/contract, signal/entry/exit times, side/quantity, `entry_price_ticks/exit_price_ticks`, structural stop/target, `planned_risk_cents`, `gross_pnl_cents/net_pnl_cents/fees_cents`, exit reason and risk breach; own `EquityPoint(at, equity_cents)` and `ReplayResult(trades, equity_curve, skips, unresolved_exposure, coverage, manifest)`. Fill helpers consume `Signal`, `Quote | MesBar`, `Account`, and cost settings, and emit Task 3's `Fill`; no duplicate fill type. Add `gamma_bot replay --dataset PATH --config PATH --variant NAME --output DIR`.

- [x] **Step 1:** Write failing tests for next-event ask/bid entries, bar-only next-open approximations, adverse entry/exit slippage, whole-contract rejection, one position/pending entry, post-fill risk recheck, partial exits/reservations, protective exits and 2R target computed from actual entry. An order cannot fill on the observation that generated it. Replay a hand-computable long and short and assert fills, gross/net P&L, marked drawdown, and released margin.
- [x] **Step 2:** Add fill edge cases: a gap beyond a stop fills at the worse executable price; stop and target both inside one bar resolve stop first; a target merely touched without fill evidence is not assumed filled. Test a gap past the target using an explicit conservative fill rule and document it. If data end before liquidation, keep exposure unresolved and do not invent an end-of-file close.

```python
def test_gap_does_not_fill_at_unavailable_stop(gap_dataset, config, full_variant, strategies):
    # Long stop at 23_996; first executable bid is 23_990; adverse slip is 1 tick.
    result = run_replay(gap_dataset, config, full_variant, strategies=strategies)
    assert result.trades[0].exit_price_ticks == 23_989
```

- [x] **Step 3:** Add session/macro/daily-loss exits, early close, no future-margin lookup, maintenance-margin deficit liquidation, explicit contract roll based on prior-session volume, no roll backward, no adjusted-price fills, and state warm-up after roll. Mark the position before checking maintenance margin; record a deficit and attempt liquidation at the next executable event, without pretending a gap can be avoided. Missing roll evidence excludes the affected session rather than selecting today's most active contract retroactively.
- [x] **Step 4:** Run `.venv/bin/python -m pytest tests/replay -q` and verify failures. Implement chronological execution: exits and account reconciliation first, feature update, new signal evaluation, then eligible later-event fills. Stable-sort by availability time and source sequence; exact tie behavior is documented and cannot fill a newly generated order on the same replay event. Reserve risk/margin for pending entries. Allocate the per-contract round-trip fee scenario half to entry and half to exit, assigning any odd cent to exit; slippage is already reflected in fill prices and is not subtracted a second time from realized P&L.
- [x] **Step 5:** Rerun replay tests and run the CLI twice on the same fixture. Expected: identical ledger/equity results and manifests except explicitly excluded wall-clock generation metadata; zero trades is valid. A valid incomplete dataset can produce an explicit reduced run, but a requested unsupported full run returns coverage diagnostics, not fabricated performance.
- [x] **Step 6:** Review/stage replay/CLI/tests and commit `feat: replay MES strategies with realistic risk and fills`.

### Task 7: Produce an honest research report and reproducible artifacts

**Files:** Create `research/metrics.py`, `report.py`, `tests/research/test_metrics.py`, `test_report.py`; modify `cli.py`/`README.md`.

**Interfaces:** `compute_metrics(result: ReplayResult) -> Mapping[str, int | float | str | None]`; `write_report(result: ReplayResult, output_dir: Path) -> None`. Outputs: `manifest.json`, `coverage.json`, `trades.csv`, `equity.csv`, `skips.csv`, `report.html`. Full-coverage claims require verified data and all required rules; unresolved exposure and approximations appear prominently. Undefined metrics serialize as null/“undefined,” never Infinity/NaN.

- [x] **Step 1:** Write failing tests with known P&L/equity sequences for return, marked max drawdown, expectancy, win rate, profit factor, average wins/losses, exposure and losing streak; include no trades, only winners, only losers, and risk overruns. Assert indicative prices, synthetic fixtures, omitted rules, scenario margin, and unresolved exposure appear in both machine-readable and HTML outputs.

```python
def test_no_trades_does_not_invent_statistics(empty_result):
    metrics = compute_metrics(empty_result)
    assert metrics['trade_count'] == 0
    assert metrics['expectancy_cents'] is None
    assert metrics['profit_factor'] is None
```

- [x] **Step 2:** Run `.venv/bin/python -m pytest tests/research/test_metrics.py tests/research/test_report.py -q` and verify failures, then implement the interfaces. Escape provider text in HTML, avoid external assets/network dependencies, and preserve source/model/expiry/roll/config hashes and planned-versus-realized risk per trade.
- [x] **Step 3:** Rerun those tests; generate and inspect the HTML/CSV from the hand-computable replay. Reconcile ledger sums to final equity; an incomplete/missing dataset produces a coverage report with performance unavailable, not a zero-return claim. Verify no credentials or full licensed raw datasets are embedded in artifacts.
- [x] **Step 4:** Review/stage report/CLI/docs/tests and commit `feat: export reproducible MES research evidence`.

### Task 8: Add chronological evaluation, ablations, and cost sensitivity

**Files:** Create `research/experiments.py`, `tests/research/test_experiments.py`; modify CLI for `evaluate` and reporting for comparisons.

**Interfaces:** `freeze_split(session_dates: Sequence[date], output: Path) -> tuple[tuple[date,...], tuple[date,...]]`; `evaluate(dataset: Dataset, config: RunConfig, variants: Sequence[Variant], split_path: Path) -> Mapping[str, ReplayResult]`. Persist a first-70%/last-30% complete-session split before tuning, rounded down for development and requiring at least one session in each part. Cost scenarios and parameter candidates belong to a frozen experiment manifest; holdout data cannot choose parameters.

- [x] **Step 1:** Write failing tests asserting 10 complete sessions split 7/3 with no shared dates, incomplete sessions do not alter the frozen split, later data cannot silently rewrite it, and insufficient data yields “pipeline demonstration” rather than a validation claim. Assert baseline and each removable-filter experiment share comparable periods and accounting; data-unavailable ablations are reported unavailable rather than silently dropped.

```python
def test_split_is_chronological_and_disjoint(ten_session_dates, tmp_path):
    development, evaluation = freeze_split(ten_session_dates, tmp_path / 'split.json')
    assert development == tuple(ten_session_dates[:7])
    assert evaluation == tuple(ten_session_dates[7:])
    assert set(development).isdisjoint(evaluation)
```

- [x] **Step 2:** Run `.venv/bin/python -m pytest tests/research/test_experiments.py -q` and verify failures; implement separate reversal/breakout evaluations, matched baseline, one-filter-at-a-time removals, mapping-window sensitivity at 5/10/15 trailing minutes (still requiring at least 5 valid pairs), round-trip fee scenarios USD 2/3/5, and 1/2/4 adverse ticks per fill. These are frozen sensitivity scenarios, not holdout-driven parameter selection. Rerun the risk sizing under each cost scenario; do not reuse quantities sized for cheaper costs.
- [x] **Step 3:** Rerun the tests and evaluate real overlap only if Task 1/coverage permits. Report session/trade counts and uncertainty with limitations; no funding recommendation or claim of profitability from test fixtures/small samples. Keep every attempted experiment configuration, including zero-trade results.
- [x] **Step 4:** Review/stage experiment/CLI/report/tests and commit `feat: compare MES strategy hypotheses out of sample`.

### Task 9: Record forward data through a verified free adapter

**Files:** Create `data/recorder.py`, `data/adapters/__init__.py`, `tests/data/test_recorder.py`, and the provider adapter/tests only if Task 1 established free access and allowed automation. Modify CLI and source example metadata.

**Interfaces:** `SourceAdapter.events() -> Iterator[Event]`; `record(adapter: SourceAdapter, output: Path, checkpoint: Path, *, stop_at: datetime) -> CoverageReport`; `SourceAdapter.capabilities -> CoverageReport`. Production credentials come from environment/keychain configuration and never enter returned events, checkpoint files, logs, or manifests. The builder selects the provider-specific authentication/retry implementation from Task 1's verified documentation.

- [x] **Step 1:** Write failing tests using an adapter fixture for duplicate sequence delivery, disconnect/reconnect, restart from checkpoint, out-of-order availability, rate limiting, corrupt trailing writes, credential redaction, and skipped observations. Assert reconnect does not backdate `available_at` or turn a gap into a continuous usable stream.

```python
def test_reconnect_preserves_unique_observations(reconnecting_adapter, tmp_path, stop_at):
    output, checkpoint = tmp_path / 'events.jsonl', tmp_path / 'checkpoint.json'
    record(reconnecting_adapter, output, checkpoint, stop_at=stop_at)
    # Fixture delivers sequence 1, disconnects, then redelivers 1 followed by 2.
    assert [event.sequence for event in load_dataset(output).events] == [1, 2]
```

- [x] **Step 2:** Run `.venv/bin/python -m pytest tests/data/test_recorder.py -q` and verify failures; implement append-only raw recording, atomic checkpoint updates, bounded backoff, source sequence/hash deduplication, and explicit gaps. Maintain single-process behavior; do not start a background service automatically.
- [x] **Step 3:** If a free permitted source exists and credentials are available, record a bounded sample, audit it with Task 2, and report measured cadence versus required freshness. Hourly/15-minute data must not claim 1–5-minute wall coverage. Otherwise ship the recorder contract/tests with `no_verified_source` diagnostics; do not add guessed private endpoints, paid trials, or a synthetic production feed.
- [x] **Step 4:** Rerun data/CLI tests, review raw/checkpoint/log redaction and the real sample's provenance if present, then commit `feat: record verified free research observations` with exact staged paths.

## Later operational subproject: IBKR paper execution

This is an explicit continuation of the approved roadmap, not an implemented or validated research deliverable. Write its own task-level plan once the source audit and research interfaces establish what data can be delivered for USD 0, and once a dedicated paper account is available. Do not install/connect a broker adapter or submit even paper orders as a side effect of research commands.

The plan must reuse `FeatureEngine`, strategy signals, `size_trade`, and calendar/account contracts above. It must specify broker contract resolution and current permissions/margin, stable order/client IDs, persistent order/execution state, duplicate/partial-fill reconciliation, protection during reconnect/restart, unexpected-position handling, market-closure failures, macro/pre-close flattening and residual-order checks. Test disconnection, restart, cancellation/fill races, and protective-order replacement before claiming paper readiness. Later live enablement additionally requires real account equity/data validation and explicit configuration; the simulated USD 10,000 is never used to size a real account.

## Self-review and final acceptance

- [ ] Match each approved requirement to its owner: data/provenance (1–2), risk/calendar/capital/roll (3, 6), GEX and confirmations (4–5), fills/exits (6), reporting and honest claims (7), evaluation (8), recording (9), IBKR lifecycle (separate operational plan above).
- [ ] Check interfaces and type names against earlier tasks; do not create second versions of `Fill`, `Signal`, `GexSnapshot`, or account arithmetic.
- [ ] Confirm all five Review Focus items have behavioral checks in their owning tasks, rather than configuration/source-text assertions.
- [ ] Run `.venv/bin/python -m pytest -q`, the real-data audit where available, and one end-to-end fixture demonstration. Inspect the final diff and `git diff --check`. Broaden tests only for new failures or unresolved risks.
- [ ] Report separate outcomes for software mechanics, actual data coverage, historical strategy evidence, forward source access, and broker readiness. Do not mark unsupported later milestones complete.
- [ ] Follow the execution skill's review/branch-finishing procedure. Never merge without user authorization.

## Execution handoff

Recommended method: **Native** implementation in this session, followed by an independent whole-branch review. These tasks share data/feature/replay interfaces and begin with a source-dependent audit; parallel implementation would add coordination before data semantics are established. A subagent-driven implementation with a fresh implementer/reviewer per task is also available at greater context cost.

Review this plan and choose an execution method before implementation. This document plans product work; writing it has not installed dependencies, created the package, downloaded market data, or submitted orders.
