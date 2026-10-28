# MES research implementation status

The nine-task research plan is merged into local `main`.
Individual task reviews and final whole-branch acceptance are complete. All seven
final findings were fixed and independently re-reviewed; no open findings remain.
The user authorized local integration; the feature history was preserved by a fast-forward merge.
This is an offline research release. It does not connect to IBKR or submit orders.

## Delivered

- Validated normalized event imports, source provenance, hashes and coverage audit.
- Causal mapping, GEX/profile imports, VIX, opening range, CVD and confirmed pivots.
- Separate reversal, negative-region breakout, opening-range baseline and named ablations.
- MES integer-tick fills, whole-contract sizing, reservations, partial fills and FIFO accounting.
- At most 1% planned equity risk per trade, margin gates, daily shutdown, macro exits and pre-close liquidation attempts.
- Conservative gap/OHLC handling, explicit contract rolls and unresolved-exposure reporting.
- CSV/JSON ledgers, equity/skip records and self-contained HTML reports.
- Frozen chronological 70/30 studies and matched one-at-a-time sensitivity runs.
- Foreground recorder contract with deduplication, checkpoint recovery, quarantines and gap warnings.

## Evidence boundaries

| Area | Current evidence |
| --- | --- |
| Software mechanics | Behavioral tests and hand-computable, explicitly synthetic examples. |
| Historical inputs | Source audit outcome `no_gex_history`; no verified common historical GEX/confirmation dataset. |
| Real sample | Local ignored Portara MES sample; availability/reuse semantics remain unverified. It is not silently normalized into tradable history. |
| Strategy performance | Unvalidated. Synthetic arithmetic is never historical evidence or a profitability claim. |
| Forward access | `no_verified_source`; no production adapter is registered. The record CLI exits 2 and writes nothing. |
| Broker readiness | IBKR paper/live integration and operational acceptance are a separate unfinished subproject. |

The baseline fixture is partial and supports a pipeline demonstration only. Its
reference ledger has one opportunity, net +1,075 cents and ending simulated
equity 1,001,075 cents. The default study retains 56 attempts, including unsupported
and unresolved outcomes. These numbers verify mechanics, not an investment edge.

The 1% limit budgets planned stop risk including costs; gaps and failed exits can
exceed it. Close deadlines are attempts requiring fills. A run never invents an
end-of-file liquidation or labels outstanding exposure flat. See
[replay policy](replay.md), [evaluation policy](evaluation.md),
[event/recorder format](event-format.md) and [source audit](data-feasibility.md).

## Implementation decisions and their costs

These decisions resolve gaps in the approved plan. They are listed chronologically
so another implementation can reproduce or revise the experiment.

| Decision | Reason | Cost or limitation |
| --- | --- | --- |
| Work in the original checkout on a feature branch. | The initially created managed worktree rejected writes. | Changes would need relocation to use that managed worktree; main is untouched. |
| Treat execution ledger/briefs as disposable bookkeeping; keep durable decisions in tracked files. | Recovery and shared guidance must not depend on private memory. | Durable notes must be maintained separately from scratch reports. |
| Always use a local virtual environment and declared development dependencies. | Plan test commands require `.venv`. | Local setup/storage is required. |
| Accept configured risk fractions below 1%, never above. | Honor a lower risk budget. | An additive sizing keyword needs maintenance. |
| Make the mapping window configurable, retaining at least five pairs. | Required 5/10/15-minute sensitivity. | Additional configuration/API surface. |
| Add optional quote bid/ask sizes. | Model known partial-fill capacity. | Unknown size remains an explicitly labeled liquidity approximation. |
| Check historical mapping-pair freshness at its pair time, plus latest-pair freshness at decision. | A trailing median otherwise rejects its own older samples. | Mapped levels depend on this freshness interpretation. |
| Three wall snapshots is a minimum; inspect all intervening snapshots in the causal 5–15-minute window. | Support one-minute feeds without cherry-picking stable observations. | Setup counts differ from an exact-three-only experiment. |
| Reject ambiguous cross-source causal ties. | Source-name sorting does not prove precedence. | Fewer CVD-eligible setups than a permissive tie assumption. |
| Expose confirmed price pivots independently of CVD. | A CVD ablation cannot retain a divergence object that already requires CVD. | Small additional feature API/test surface. |
| Require prior-session roll evidence even for startup. | Today's sole observed contract is not a causal volume comparison. | Extra warm-up data and fewer eligible sessions. |
| Compare prior regular-equity-session volume and roll only one adjacent quarter forward. | Match cash-session research and avoid hindsight quarter jumps. | Can differ from full futures-session roll conventions. |
| Start with the nearest supported pre-delivery-month quarterly contract. | Deterministic startup was unspecified. | May differ from a provider's initial active contract. |
| Exclude delivery-month execution without verified expiry metadata. | Do not infer holiday/expiry validity from a symbol. | Otherwise-valid liquid pre-expiry sessions are lost; this is not an exchange rule. |
| Require current and immediate-next-quarter prior volume, with one comparison per session. | Missing challenger data cannot establish a volume winner. | Stricter coverage than permissively retaining the current contract. |
| Vary mapping, fees and slippage one at a time around a frozen reference. | Required scenarios do not mandate a factorial study. | Joint parameter interactions are unmeasured. |
| Pass optional RunConfig into mapping coverage audit. | Capability admission must match the experiment window/minimum pairs. | Additive audit API/compatibility tests. |
| Define complete sessions by event-time bars, not zero final-bar publication latency; freeze policy version 2. | Normal publication delay must not force a full feed into demonstration-only status. | Late-published bars can count toward coverage, while runtime decisions/rolls still require actual availability. |
| Recheck initial collateral after fees and executable marks before every fill. | Pending risk alone cannot establish available initial-margin capital. | Residual entries can be cancelled even when maintenance remains sufficient. |
| Validate and report one canonical actual GEX identity across the whole input experiment/study. | Rolls, exclusions, zero trades and study splits cannot change or mislabel the hypothesis. | Mixed-identity datasets must be separated; optional metadata must agree; existing frozen study output paths cannot be reused. |
| Share midpoint histories and mapping semantics between audit and runtime. | Explicit actual MES midpoints must work without executable quotes; source/clock mixing cannot establish capability. | Ambiguous mapping sources fail closed; an audit remains input potential. |
| Require margin capability for an individual stream and exact selected margin at runtime. | Adjacent-quarter roll bars need not have execution collateral. | Missing active margin suppresses completed performance, even with zero trades. |
| Accept only the canonical normalized lower-inclusive, upper-exclusive profile declaration. | Arbitrary metadata cannot silently change membership at a boundary. | Other source conventions need validated normalization; unknown semantics cannot certify absence of negative regions. |

## Reproduce

From the repository root after installing the project:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m gamma_bot replay --dataset tests/fixtures/synthetic_replay --config config/research.toml --variant opening_range_baseline --output reports/demo
.venv/bin/python -m gamma_bot evaluate --dataset tests/fixtures/synthetic_replay --config config/research.toml --output reports/study
```

Open `reports/demo/report.html` or `reports/study/comparison.html`. Use a new study
output directory when frozen data, parameters or policy change. Generated reports
and raw recordings remain ignored by Git. No command enables live trading.

## Final verification (2026-10-28)

The final product commit is `c6fd46645af7191563ff0e4d65f1e38772e41696`.
A fresh controller run of `.venv/bin/python -m pytest -q` passed **523 tests**
in 17.12 seconds, including 45 added final integration cases. Scoped re-review
confirmed all seven findings addressed: initial collateral after fees/marks,
run/study GEX identity, inactive-contract margin admission, actual midpoint
admission, canonical profile membership, zero-trade provenance and capital display.
No new Critical, Important or Minor finding remained.

Fresh CLI acceptance returned the expected exits: incomplete audit 2, synthetic
baseline replay 0, unsupported full reversal 2, demonstration evaluation 0, and
no-verified-source recording 2. Generated evidence is in the ignored
`reports/mvp-final-2026-10-28` directory. The baseline ledger reconciles to final
equity; the study retains 49 unsupported, five synthetic mechanics and two
unresolved attempts. Every attempt marks historical performance unavailable.
The record command creates neither event nor checkpoint files.

These checks establish the delivered software behavior under the exercised
contracts. They do not establish historical profitability or IBKR readiness.
