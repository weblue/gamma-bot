# Deterministic offline MES replay

```sh
.venv/bin/python -m gamma_bot replay --dataset tests/fixtures/synthetic_replay --config config/research.toml --variant opening_range_baseline --output /tmp/mes-demo-one
```

CLI variants are `reversal`, `breakout`, and the explicitly reduced
`opening_range_baseline`. Python consumers can supply the explicit Task 5
ablation variants and their corresponding strategy instances to
`gamma_bot.replay.engine.run_replay(dataset, config, variant, strategies=[...])`.
Do not reuse a strategy instance between independent runs: it owns trigger state.

The result contains `trades`, `equity_curve`, `skips`, `unresolved_exposure`,
`coverage`, and `manifest`. The CLI writes each field to its named JSON file;
its manifest additionally records the code revision. Exit 0 means replay
completed, 2 means requested input coverage is unsupported, and 1 means invalid
input/configuration or an output error. Input coverage is potential availability,
not a passed strategy filter or full-session executability. `experiment_scope`
records full versus reduced declarations; `excluded_sessions` records roll
exclusions. A valid zero-trade replay is distinct from unsupported coverage.

The checked-in fixture's README describes its synthetic calendar, margin and
volume assumptions, hand-computable fills, and two-run comparison command.
These mechanics results are not historical returns or evidence of profitability.

## Admission, decisions and orders

Market records are admitted in `(available_at, event_at, source_id, sequence)`
order, exactly matching the importer. Timers for known session deadlines, macro
publication/deadlines and margin publication advance the same clock, only through
the last input's availability. Future margin and unpublished macro information
cannot pass current decisions. Admission never preloads features from future
records. Only the selected dated contract enters features; other contract bars
are still ingested for subsequent roll evidence. An inactive dated MES midpoint
cannot reset active-contract features. A source switch in feature midpoint inputs
fails closed: select one compatible feed per experiment rather than implicitly
combining sources. Source-name sorting supplies no cross-source causal evidence.

Existing-position marks and account reconciliation precede exits; features and
completed-bar proposals follow; pending entries can then fill only on a later
eligible market observation. A new signal cannot fill at the signal's availability
clock, even if another source sorts later. Delayed quotes with market time before
the decision cannot execute it. Older market observations cannot replace newer
marks. No midpoint, indicative price, trade-classification record or adjusted
continuous series supplies executable liquidity. Entries use new quote ask/bid,
with adverse slippage; bar-only market orders use the next eligible bar open,
reported at that bar's availability. An open before the signal is unusable.

Whole-contract sizing uses current marked equity, current matching-contract
margin, structural stop distance, per-contract fees and slippage. The absolute
structural stop is preserved. Risk and collateral are reserved for one pending
entry, with partial fills transferring collateral to actual Account lots. Entry
prices are re-estimated before execution; higher costs reduce permissible size.
A changed initial-margin requirement cancels the remaining order rather than
silently changing its collateral. A gap can cancel an order without a fill.
Post-fill marked risk is checked again; a breach keeps actual fills in the ledger,
cancels pending size and requests liquidation at the next executable event.
Remaining pending risk is cancelled if filled risk plus its reservation exceeds
the current budget. Callbacks occur only on the first actual entry fill.

## Exits, accounting and unresolved exposure

Each actual entry lot keeps its own integer tick price, structural stop and 2R
target computed from its actual entry distance. Nonintegral configured target
distances round away from entry to a whole tick. TradeRecord is a completed
FIFO lot slice, so one economic order may produce several records; group by
strategy/trigger/contract when reporting economic trades. No weighted-average
price substitutes for an executable price. `planned_risk_cents` is that slice's
executable-entry stop risk plus both fee halves and remaining exit slippage.

Stops fill at the worse of the executable price and stop, then adverse slippage.
If stop and target both occur in one OHLC bar, stop wins. A bar entry can hit
protection within that same entry bar after its opening fill; its fill timestamps
remain the bar's availability. An existing quote entry cannot use a bar's
pre-entry OHLC. A target merely touched does not fill; strict bid/ask or OHLC
trade-through is required. Favorable gaps past targets receive the target price,
then adverse slippage, with no gap improvement. Unknown quote size uses an
explicit requested-quantity approximation, not measured queue liquidity. Known
size caps the entire observation across all exiting lots. A triggered partial
stop remains a market liquidation request even if the next price recovers.

Round-trip fees allocate floor(half) per contract to entry and the remainder to
exit, including odd cents. Fill fees are total execution fees across actual fill
quantity. Slippage is already in each fill price and is never deducted again.
The real Account owns FIFO cash/P&L/collateral. Quote marks use bid for longs and
ask for shorts; bar marks use close. Equity points retain observed marked losses
and realized fills, without reconstructing an unknowable intrabar price path.

Calendar/macro flatten deadlines and daily shutdown cancel pending entries and
liquidate exposure using available executions. Maintenance checks use marked
equity first; a deficit is recorded and liquidation waits for a later executable
observation, including any unavoidable gap. The engine cannot invent liquidity
before a deadline or at end of file. Open lots, remaining entry quantity, and
outstanding liquidation requests remain explicit in `unresolved_exposure`.
These records must be included in downstream reports, never treated as completed
flat performance. A new entry waits for a completed bar starting after an actual
exit. Session baselines use Account.start_session only after exposure is flat.

## Causal quarterly contract selection

Selection happens once at each supplied regular-equity-session open, using only
bars admitted by that time from the immediately preceding supplied completed
regular session. A complete volume observation requires one and only one
one-minute bar for every regular-session minute, with a single source per
contract. Overlapping or partitioned multiple feeds never sum into market
volume. Current/candidate ambiguity excludes the affected session.

At startup, candidates come from available complete prior-session volume,
ordered by dated delivery month; choose the nearest delivery strictly after the
session month. Resolve short symbol years to the nearest year relative to the
session year, with earlier year breaking a tie. Four-digit years are literal;
vendor and exchange symbols are supported. Multiple aliases for one comparison
quarter fail closed. After initial selection, retain the current contract unless
its immediate next quarterly contract has strictly greater prior-session volume.
Ties retain current. There is one comparison per session, never selection of the
largest arbitrary future quarter and never a backward roll.

Both current and immediate next quarter require complete prior-session evidence.
Missing either excludes the entire trading session, even for single-contract
startup. The excluded session's bars still build evidence for the next session.
A roll resets all feature history and requires fresh warm-up. Schema v1 has no
verified exchange expiry date; delivery-month and older contracts are excluded
rather than inferring an expiry holiday rule. This conservative research policy
loses some valid pre-expiry trading sessions and is not broker-ready expiry
validation. Roll history, exclusions and assumptions remain in every manifest.
