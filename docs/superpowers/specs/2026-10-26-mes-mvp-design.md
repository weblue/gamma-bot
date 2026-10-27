# Gamma Bot: MES research MVP

Date: 2026-10-26
Status: Approved by the user on 2026-10-26; product implementation has not started.

## Intent

Test whether gamma-exposure levels and the proposed confirmation filters support a useful intraday MES trading strategy. Produce reproducible evidence for a possible funding decision, including failures and data limitations. Automatic execution through Interactive Brokers remains the intended destination.

MES futures replace the original SPY options execution scope. Positive-gamma wall reversals and negative-gamma breakouts are directional futures hypotheses. Their results will not establish an edge from selling option premium or buying options.

## User requirements

- Python implementation; MES futures are the only traded instrument.
- Interactive Brokers is the intended broker and API provider. Automatic execution is in scope; manual approval of each trade is not the intended workflow.
- Historical research starts with USD 10,000 of simulated capital. This does not change the actual account's funding or authorize depositing money.
- Planned risk is at most 1% of current account equity per trade, rather than an aggregate 1% portfolio limit.
- No overnight positions. Close positions before the regular US equity session closes, including early closes, even though MES has a longer trading session.
- Data budget is USD 0. No paid subscription, purchase, or presumed future funding is a dependency.
- Investigate positive-gamma mean reversion and negative-gamma directional breakouts.
- Mean-reversion filters: VIX below its 10-period moving average and below 22, with stable or falling volatility; CVD divergence at a wall; wall concentration stable or increasing and not migrating away; opening-range alignment after the first 15 minutes.
- No new positions or holdings within 30 minutes before or after CPI, PPI, FOMC, jobs reports, and ISM releases.
- Backtest the rules, rather than assume that their descriptions establish a trading edge.

## Goals and exclusions

The first release establishes data feasibility, provides a deterministic replay engine, tests the rules supported by actual inputs, and exports an inspectable trade ledger and research report. A forward recorder builds a dataset where free, permitted access exists. IBKR paper execution follows when suitable data and account access are available; live execution is a later milestone using the same strategy and risk logic.

Excluded: SPY trading, options orders, premium-selling P&L, ES execution, multi-user fund management, external investor solicitation, a web dashboard, and performance optimization without measurement. A command-line workflow and local reports are sufficient initially.

Anti-goals: fabricated results, treating synthetic fixtures as market evidence, hiding failed experiments, selecting favorable dates after seeing results, claiming intraday dealer inventory from daily OI alone, or changing stops solely to force a contract into the risk budget.

## Approaches considered and selected

1. **Python throughout — selected by the user.** One strategy/risk core supports replay and eventual execution. Python has an official IBKR TWS API implementation and suitable time-series tools. The tradeoff is less user familiarity than Node.
2. **TypeScript/Node throughout.** Greater user familiarity and a shared runtime were plausible advantages. Numerical tooling and IBKR integration would require different choices. Not selected.
3. **Rust as the primary implementation.** Considered from the initial request and deferred: the current uncertainties concern data and strategy validity, with no measured execution-speed bottleneck. A targeted optimized component remains possible later.

## Proposed defaults, not prior user decisions

The following are research defaults proposed by the author. Approving this document accepts them as the initial experimental specification; they are not claims of profitable parameter choices.

| Choice | Initial proposal |
| --- | --- |
| Interface | Local CLI, configuration files, trade CSV/Parquet, and an HTML research report |
| Decision interval | Completed 1-minute bars; no decisions based on unfinished bars |
| Opening range | 09:30–09:45 America/New_York, excluding the 09:45 bar |
| Entry window | 09:45 inclusive to 10:30 exclusive, following the originally described morning window |
| Session flatten | Begin 5 minutes before the scheduled regular equity close; block entries and keep reconciling until flat |
| Concurrent positions | One bot-owned MES position; no pyramiding, averaging down, or simultaneous opposing positions |
| Daily loss control | Stop entries and attempt to flatten at a 3% decline from session-start equity, including unrealized P&L and costs |
| VIX moving average | Simple average of the latest 10 completed 1-minute observations |
| Stable/falling VIX | Latest completed VIX value no greater than the preceding completed value |
| Level tolerance | 1 MES index point for touches, wall stability, and opening-range alignment |
| Wall observation window | Three distinct snapshots spanning at least 5 and at most 15 minutes; no gap between snapshots over 5 minutes |
| CVD divergence | Causal confirmed pivots, with 2 completed bars on either side; compare successive price pivots and CVD at those pivot times |
| Profit exit | Fixed 2R target; no trailing stop in the first experiment |
| Slippage baseline | 1 adverse MES tick per entry/exit fill, plus explicit commissions and fees |
| Initial cost scenario | USD 3 per contract round trip as a research assumption, not a quoted IBKR rate |
| Cost sensitivity | Round-trip costs of USD 2, 3, and 5; slippage of 1, 2, and 4 ticks per fill |
| Warm-up | Require complete input histories; otherwise skip entries |
| Freshness | GEX/profile snapshot at most 5 minutes old; latest completed VIX observation at most 90 seconds old; mapping prices at most 60 seconds old |

MES tick size is 0.25 index points and its multiplier is USD 5 per index point. Prices and order quantities must respect these contract units. Currency accounting uses integer minor units or explicitly rounded Decimal arithmetic. Statistical features may use floating point with finite-value and tolerance checks.

## Data feasibility is the first deliverable

No complete free historical dataset has been verified. The first task is a read-only/provider-access audit and a small sample, not a promise of a full historical GEX backtest. Account creation, credentials, and subscriptions already included in an account must be distinguished from anonymously downloadable data. Do not spend money.

The audit must identify a common date range, actual access/retention limits, automation permissions, timestamp semantics, and missing fields for:

| Input | Required evidence |
| --- | --- |
| MES prices | Explicit dated contracts, unadjusted execution prices, tick size/multiplier, session calendar, and roll policy |
| Fills and costs | Quotes where available; otherwise documented bar-fill approximations and cost sensitivity |
| Options/GEX | Historical as-of chain or levels, strike, expiration, underlying, OI publication date, gamma/model provenance, units, and availability time; negative-region boundaries or sufficient inputs to calculate a price-dependent gamma profile |
| Cross-market mapping | Contemporaneously available prices for the GEX underlying and MES, if they differ |
| CVD | Trades with aggressor direction, or sufficiently ordered trades/quotes for a declared classification algorithm |
| VIX | Intraday observations and their availability times; daily closes cannot substitute for the proposed intraday MA |
| Macro schedule | Event names, scheduled timestamps, timezone, and provenance; no use of release outcomes |
| Margin | Dated historical broker margin where available; otherwise an explicitly labeled scenario, not a claim of historical IBKR executability |

Candidates identified in research:

- GEXtool: free delayed SPY dashboard; advertised CSV export is paid. No free automated historical export has been established. Manual viewing does not establish a usable bot feed.
- Tradier: US equity/options data included for brokerage account holders; supplied Greeks are hourly and expired-option history is unavailable. Potential forward input; insufficient by itself for 1–5-minute migration history.
- Alpaca: free indicative options feed, modified quotes and delayed trades; history is advertised from February 2024. Documentation differs on older OPRA access. Test actual entitlements and historical OI coverage before choosing it.
- IBKR: evaluate already-included/delayed data and permissions. Historical tick requests require a market-data subscription; expired-option data is unavailable. Do not assume the USD 0 constraint provides futures ticks.
- Public MES historical samples may be evaluated, but verify their actual files, provenance, contract rolls, timestamps, and permitted use before relying on them. Discovery of a sample is not validation of its quality.

Free delayed inputs may support a deliberately delayed strategy or an offline timestamped experiment. Historical download latency alone is not disqualifying; the replay must use when the underlying information was available. Indicative/modified prices are a separate limitation and cannot silently represent executable quotes.

### Audit outcomes

1. **Full overlap found:** run the complete specification on the supported period.
2. **Partial overlap found:** run separately named reduced experiments, listing every omitted filter. Never label a reduced experiment as the full strategy.
3. **No usable GEX overlap:** deliver the coverage report and recorder/replay tooling; historical GEX performance remains unavailable. Price-only experiments can validate plumbing but are not evidence for a GEX edge.

Missing required inputs make a setup ineligible in the full strategy. They are never interpreted as a passed filter. Forward collection starts only for a source whose free access and permitted automation are verified. Otherwise document the missing access; do not invent a source or bypass access controls.

## GEX definition and mapping

Every run uses one explicit GEX source/model and expiry universe; results from different models are separate experiments. The audit selects a viable source before strategy implementation is finalized. The initial candidate is a SPY-options-derived proxy, because free SPY chain/dashboard candidates were identified. SPX or options-on-ES data are alternatives only if access and coverage are established. None has been selected or validated yet.

For a locally calculated proxy, propose all available expiries from 0 through 7 calendar days, recorded explicitly, with last published per-contract OI. Per-contract dollar gamma per 1% underlying move is:

`signed_gex = sign_assumption × gamma × open_interest × option_multiplier × underlying_price² × 0.01`

The initial sign convention is calls positive, puts negative. This is an assumption, not observed dealer positioning. Gamma changes with spot, volatility, and time even if published OI is unchanged. Repeated snapshots do not establish fresh intraday OI or distinguish opening from closing trades.

If calculating gamma rather than obtaining it, the builder must specify and validate the pricing approximation, rates/dividend inputs, expiration times, and numerical handling near expiry. A European approximation for SPY must be labeled as such. The audit may reject local reconstruction if essential inputs are unavailable for free.

Aggregate signed GEX by strike. Define a lower support candidate as the strike below spot with greatest absolute put contribution, and an upper resistance candidate as the strike above spot with greatest call contribution. The regime is the sign of total signed GEX across the documented universe. These definitions describe a reproducible proxy and do not prove the walls predict prices. Vendor levels need a documented semantic mapping; do not assume identically named metrics are equivalent.

For the negative-gamma entry hypothesis, a negative region means an interval of underlying prices where the source's modeled aggregate dealer gamma is negative. Require a documented price-dependent gamma profile or equivalent sign-tagged boundaries. If reconstructing it locally, re-evaluate gamma across hypothetical underlying prices using only the same as-of OI and a declared fixed-per-contract IV assumption, and find sign-change boundaries. Negative GEX at one strike is not sufficient to infer that the entire price region is negative. If the source supplies only net GEX at current spot and call/put walls, a wall-crossing experiment can be reported separately as a reduced proxy, but cannot stand in for the full negative-region strategy.

For SPY-derived levels, the proposed mapping is a causal trailing 5-minute median of the contemporaneous MES-midpoint/SPY-midpoint ratio. Multiply the SPY strike by that ratio and round to a MES tick. No fixed 10× conversion, future observations, or back-adjusted futures prices. Require at least 5 valid paired minute observations and reject either price if more than 60 seconds old. Treat mapped GEX as a cross-market proxy; report mapping sensitivity.

If using another source underlying, define its mapping in the audit before enabling it. Do not pool expiry universes or map an options-on-futures level to the wrong delivery contract.

## Mechanical strategy hypotheses

All timestamps below are event availability times, not just chart labels. A signal is actionable only after all confirmations are available. All entry filters apply to the same decision time, and the first execution opportunity is after that time.

### Positive-gamma wall reversal

1. Require positive total GEX and a valid wall below spot for a long, or above spot for a short.
2. Require `VIX < SMA10(VIX)`, `VIX < 22`, and non-increasing VIX.
3. The mapped lower/upper wall must be within 1 MES point of the opening-range low/high respectively.
4. In the three-snapshot window, the wall must remain at the same source strike; its absolute concentration must be non-decreasing and mapped movement must stay within 1 MES point. A changed strike, a concentration decline, or movement away invalidates the setup. A source updating only every 15 minutes cannot pass this full filter.
5. Long: two confirmed price lows no more than 15 minutes apart; the second is at least 1 tick lower, is within 1 MES point of the mapped support wall, and CVD at the second low is higher. Short: the symmetric higher-high/lower-CVD pattern at resistance. A pivot requires its 2 right-hand confirmation bars, preventing hindsight entries. Require a completed close back above support for a long or below resistance for a short.
6. Enter at the next available executable event after confirmation, subject to risk, liquidity, freshness, calendar, and account checks.
7. Structural stop is 1 tick beyond the second pivot; reject zero/negative stop distances. Set the target at 2 times the actual filled-entry-to-stop distance.

### Negative-gamma breakout

1. Require a valid modeled negative-gamma region with a sign-tagged boundary, plus negative modeled total GEX at the new decision-time spot. Model semantics must satisfy the definition above; otherwise this strategy is unavailable.
2. Use a boundary with negative gamma on its upper side for long breakouts or its lower side for short breakouts. Require the mapped boundary to align within 1 MES point of the corresponding opening-range high/low.
3. Freeze the mapped boundary and region at the preceding completed bar's decision time. Long: the prior close is outside the negative region at/below the boundary and the new completed close is inside it at least 1 tick above; short is symmetric. The newest valid profile must also classify the new close as negative. This requires a price crossing and prevents profile migration alone from manufacturing entry.
4. Do not apply the mean-reversion VIX ceiling or divergence requirement to breakouts: those would require a separately specified continuation hypothesis. Require valid negative-regime data and the common risk/session/macro gates.
5. Stop is 1 tick beyond the opposite extreme of the breakout signal bar; enter at the next executable event and use a 2R target.

These are proposed initial definitions for terms the user supplied qualitatively. They are configurable experiments, not parameters to tune on the held-out evaluation set. No automatic immediate reversal: close and reconcile an existing trade before considering a new signal on a later completed bar. One entry per strategy/direction/trigger per session prevents repeated entries on the same setup.

## Risk, execution, and session behavior

At order decision, use simulated current marked equity (later IBKR NetLiquidation) and a 1% budget. Let `d` be the structural stop distance from the conservative entry estimate in MES points. For each contract:

`planned_risk = d × USD 5 + round_trip_fees + entry_and_exit_slippage_allowance`

`quantity = floor((0.01 × equity) / planned_risk)`

Also cap quantity by available cash/margin and whole contracts. Skip quantities below one; never tighten a structural stop to manufacture affordability. Reserve risk and margin for working entries. Recheck before submission and after fills; if an adverse fill breaches planned risk, reduce/exit the position and record the breach rather than relabeling it compliant.

The 1% rule is planned stop risk, not a guaranteed maximum realized loss. Stops can slip or fail to fill; a futures position does not have a contractually capped loss. Report both planned and realized risk, with overruns visible. No product claim of a guaranteed 1% maximum.

Replay entries fill at the next event's ask for buys or bid for sells, with adverse slippage. Bar-only replay uses the next bar open plus adverse slippage and is labeled approximate. Stops fill at the worse of stop price or the first available executable price after a gap, plus slippage. Targets require evidence of a fill; bar-only targets require price to trade through by at least 1 tick. If stop and target could both occur in the same bar and order is unknown, assign the stop first. No favorable hindsight sequencing.

Macro windows are inclusive from release minus 30 minutes through release plus 30 minutes. Start flattening 5 minutes before the window begins; do not enter if that liquidation deadline has arrived. Jobs initially means the BLS Employment Situation; ISM means both Manufacturing and Services PMIs. FOMC includes both the decision and scheduled press conference as distinct events. Union overlapping windows. Missing or unverified session schedules block full-strategy entries. Exit orders remain permitted during every blackout.

Session-close and macro flattening are attempts that require confirmed fills. Cancel entries, reconcile, close residual quantities, cancel unnecessary exits after fills, and verify positions/orders. A halt or disconnection can prevent the deadline: record/alert the unresolved exposure rather than claim it is flat. Never cancel protective exits before replacement protection or liquidation is confirmed.

The bot manages its own positions in a dedicated research/paper account. Unexpected or pre-existing positions block new entries and require reconciliation; do not automatically liquidate unrelated user holdings. Simulated futures P&L, margin reservations, trading sessions, and daily equity snapshots must be consistent; margin collateral is not a loss or fee.

Futures history uses explicit contracts with a documented roll rule fixed before evaluation. Proposed initial rule: select the next quarterly contract when its prior completed session volume exceeds the current contract, then never roll backward. Warm-up, feature alignment, and opening ranges reset across a switch; no continuous back-adjustment in fills. Later execution uses explicit broker contract IDs.

## Software shape

Keep one Python package and one process initially, with boundaries for:

- Data adapters, immutable raw storage, normalized events, provenance, and coverage validation.
- Causal features: GEX/mapping, opening range, VIX, CVD, and event calendar.
- Pure strategy decisions and common pre-trade risk checks.
- Clock/event replay and a simulated broker with explicit fills, costs, and margin.
- A future IBKR broker adapter with connection state, order IDs, execution reconciliation, and the same decision/risk core.
- Trade ledger, experiment manifest, diagnostic skip reasons, and research reports.

Use UTC internally and America/New_York for session rules. Preserve exchange timestamps, availability timestamps, and ingestion timestamps separately. Deterministic ordering and an input hash/configuration/code revision manifest make runs reproducible. Reject future-as-of joins, duplicate/conflicting events, stale inputs, non-finite values, and impossible prices. Preserve raw data and model versions so derived indicators can be rebuilt.

Storage/library details are left to the builder: pandas/NumPy are candidates for research and Parquet for immutable event tables; SQLite is a candidate for experiment metadata and later order state. Do not add a service framework or UI simply to expose CLI operations. Credentials belong outside version control.

## Research outputs and acceptance evidence

Every report identifies dataset range, contract selection, source/model/expiry universe, exact parameters, delays, missing rules, fill assumptions, cost and margin scenarios, and sample sizes. Include gross/net P&L, return, maximum mark-to-market drawdown, expectancy, win rate, profit factor with undefined cases handled, average win/loss, exposure, losing streaks, and planned-risk overruns. Zero trades is a valid result with skip reasons.

Compare the positive- and negative-gamma strategies separately. Compare each with an otherwise matched price/opening-range baseline and remove each filter in turn where inputs permit. Establish a chronological development/evaluation split before tuning (proposed first 70%/last 30% of complete sessions), preserve the evaluation period, and report uncertainty rather than imply a small sample establishes profitability. A short sample may justify only a pipeline demonstration. Include cost and mapping sensitivity and a record of all experiment configurations.

Required behavioral verification includes causal joins and pivot timing; risk sizing at tick/fee/margin boundaries; no entries on stale/missing inputs or calendar blackouts; early-close handling; conservative ambiguous-bar/gap fills; and deterministic replay with hand-checkable economic P&L. Synthetic fixtures test mechanics only. Paper execution later requires recovery checks for duplicate events, partial fills, disconnects, restarts, and residual positions.

Stage 1 succeeds with a reproducible coverage report and an honest go/no-go for full historical research, even if data are insufficient. Stage 2 succeeds with verified replay mechanics and clearly labeled real-data results where available. Neither result alone means the strategy works or is ready to trade real money.

## Proposed task sequence

1. **Data audit and sample:** validate free access, provenance, common coverage, calendars, and the GEX source/mapping choice; record decisions and blockers. No paid fallback.
2. **Replay foundation:** normalized events, clock/session calendar, cash/P&L/margin, fills, risk sizing, experiment manifest, and a simple baseline.
3. **Causal features:** implement only validated inputs; import precomputed GEX or calculate an explicitly documented proxy; add VIX, opening range, CVD, and macro windows where available.
4. **Strategy experiments:** implement the two specified hypotheses and reduced variants with explicit missing-rule labels.
5. **Evaluation/reporting:** development versus held-out results, ablations, cost sensitivity, and inspectable trade/skip records.
6. **Forward recorder:** collect from verified free sources and monitor gaps; if no source is usable, report the exact access limitation.
7. **IBKR paper adapter:** when account and data access permit, validate broker contracts, order lifecycle, stops, reconnect/restart reconciliation, and pre-close flattening. A separate operational acceptance record precedes live enablement.

After design approval, expand this sequence into an implementation plan. Do not scaffold or install dependencies before that approval. Live execution requires concrete account-specific data/permissions/margin validation and an explicit enablement configuration; backtest capital is never treated as real capital.

## Sources checked during design

- IBKR TWS API and supported languages: https://www.interactivebrokers.com/docs/tws-api/doc/introduction
- Python decimal arithmetic: https://docs.python.org/3/library/decimal.html
- pandas time-series support: https://pandas.pydata.org/docs/user_guide/timeseries.html
- CME MES contract: https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.html
- Dealer positioning versus volume/model estimates: https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/
- OI is reported after end-of-day clearing: https://www.optionseducation.org/referencelibrary/faq/general-information
- GEXtool free dashboard and paid export: https://gextool.com/
- Tradier data, Greek cadence, and expired-option limitation: https://docs.tradier.com/docs/market-data
- Alpaca option history and indicative feed: https://docs.alpaca.markets/us/docs/historical-option-data
- Alpaca historical-access discussion requiring entitlement verification: https://alpaca.markets/learn/fetch-historical-data
- IBKR historical tick access: https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-ticks
- IBKR unavailable historical options: https://ibkrcampus.com/docs/web-api/v1/endpoints/market-data/unavailable-historical-data
- IBKR stop execution limitations: https://www.interactivebrokers.com/en/trading/ordertypes.php?menu=B
