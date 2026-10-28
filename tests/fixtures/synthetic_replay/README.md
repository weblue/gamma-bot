# Synthetic replay mechanics fixture

Hand-authored values only. These are not historical prices, a broker margin
quote, a real macro calendar, or evidence of strategy profitability.

- Two explicitly supplied US regular equity sessions: Friday June 12 and Monday
  June 15, 2026, 09:30–16:00 New York. Calendar availability is an assumption.
- Friday contains every regular-session one-minute bar for MESU6 (3,900 total
  synthetic volume) and MESZ6 (1,950). This is causal evidence for Monday's
  single U-to-Z comparison. Friday itself is excluded without Thursday evidence.
- MESU6 is selected initially as the nearest delivery strictly after June;
  MESZ6 does not exceed its prior-session volume. No delivery-month expiry is
  inferred. Symbols with one-digit years resolve relative to the session year.
- The complete, empty macro calendar is assumed published before either day.
- Both contracts use a labeled USD 6,000 per-contract initial/maintenance margin
  scenario published Friday morning. It is not historical broker evidence.
- Monday includes fifteen opening-range bars, a long crossing decision, and two
  sized-unknown quotes. The explicit unknown-size approximation fills one
  contract at 24,005 ticks, with a structural stop of 23,999 and 2R target of
  24,017. Strict bid trade-through at 24,018 fills the target at 24,016 after
  adverse slippage. Gross USD 13.75 less USD 3 fees = USD 10.75 net.
- Missing GEX and other inputs make full reversal/breakout unsupported. The
  declared `opening_range_baseline` is a reduced price-only experiment.

From the repository root:

```sh
.venv/bin/python -m gamma_bot replay --dataset tests/fixtures/synthetic_replay --config config/research.toml --variant opening_range_baseline --output /tmp/mes-demo-one
.venv/bin/python -m gamma_bot replay --dataset tests/fixtures/synthetic_replay --config config/research.toml --variant opening_range_baseline --output /tmp/mes-demo-two
diff -r /tmp/mes-demo-one /tmp/mes-demo-two
```

All six JSON artifacts match byte for byte when code revision and inputs match.
There is no wall-clock generation field. Artifacts include closed trade slices,
marked equity points, skips, unresolved exposure, coverage, and a manifest with
config, hashes, source metadata, code revision, roll decisions and used margins.
