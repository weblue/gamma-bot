# Normalized research inputs, version 1

The importer is offline and validates normalized inputs. It does not infer
timestamp semantics, source rights, option models or contract rolls from raw
vendor files. All supplied synthetic fixtures are hand-authored mechanics tests.
The current source audit remains `no_gex_history`; this schema selects no feed.

## Files and public interfaces

`gamma_bot.data.io.load_dataset(path: Path, *, as_of: datetime | None = None)`
accepts either a directory or one normalized event-only JSONL file. A directory
contains required `events.jsonl` and optional `metadata.json`, `sessions.jsonl`,
`macro_releases.jsonl`, and `margin_points.jsonl`. Each JSONL line is one JSON
object. Blank lines are ignored. A single file may have a sidecar with its suffix
replaced by `.metadata.json`, for example `prices.metadata.json` beside
`prices.jsonl`. Without metadata its provenance is unknown. Event-only files
have empty calendar and margin tables; parsing alone cannot imply full coverage.

Every record is validated before `as_of` filtering, including future records.
The cutoff retains events whose `available_at <= as_of`; calendar/margin rows
use their schedule availability/effective availability. A naive cutoff is invalid.
Event ordering is `(available_at, event_at, source_id, sequence)`. Sequence numbers
are nonnegative and unique across event kinds within one source. Exact repeats
with the same source/sequence and typed content are deduplicated; conflicting
repeats fail. Sessions are unique by date, macro releases by event ID, and margin
points by contract/availability; conflicting duplicates also fail.

Shared frozen dataclasses live in `gamma_bot.data.models`. Collections are
tuples/frozensets or recursively immutable mappings. `Dataset` contains `events`,
`sessions`, `macro_releases`, `margin_points`, `source_metadata`, `coverage`,
`limitations`, `input_hashes`, `macro_coverage_dates`, and optional aware
`macro_schedule_available_at`. Constructors validate
market/schema fields; callers creating datasets directly must supply typed
records and truthful metadata.

`InvalidDatasetError` identifies invalid data/configuration.
`CapabilityError` identifies an unknown or unavailable capability. Neither means
a valid zero-trade outcome. `gamma_bot.cli.main(argv=None)` returns an integer;
`python -m gamma_bot audit --dataset PATH --output PATH` writes readable JSON and
prints diagnostics. Exit 0 means input capabilities overlap, 2 means incomplete
coverage, and 1 means invalid input/arguments or an output error. Outputs cannot
overwrite any of the recognized input files, including aliases/hard links.

## Common event fields and units

Each event has a `type` discriminator and these required fields:

| Field | JSON encoding | Meaning |
| --- | --- | --- |
| `source_id` | nonempty string | Links source metadata; no credentials |
| `sequence` | nonnegative integer | Source-wide identity and tie ordering |
| `event_at` | aware ISO-8601 timestamp | Exchange event time or completed observation/bar end |
| `available_at` | aware ISO-8601 timestamp | Earliest actionable historical availability, at/after event time |

Timestamps require an explicit offset, are converted to UTC, and serialize with
`Z`. Naive timestamps fail. `ingested_at` may separately record retrieval in
source metadata; it never substitutes for historical availability. Decimal fields
encode as **strings**, for example `"600.25"`. Floats are used only for finite
model inputs such as gamma/IV/rates. Non-finite numbers, booleans in integer
fields, unknown fields/types, missing required fields, and duplicate JSON object
keys fail. Prices must be positive; quantities/concentrations are nonnegative
except actual trade sizes and option multipliers, which must be positive.

MES prices are **integer ticks**: 1 tick = 0.25 points = USD 1.25, and 1 point =
USD 5. Currency fields are integer USD cents. MES execution records require
explicit dated quarterly contracts, e.g. `MESM6`, `MESM26`, or `MES2026M`.
Unspecified `MES`, continuous symbols, ES and options execution contracts fail.
No normalization applies a back adjustment or invents a roll policy.

## Event records

All fields below are required except quote sizes, which default to `null`.

| `type` / dataclass | Additional fields |
| --- | --- |
| `mes_bar` / `MesBar` | `contract`, `start_at`, `open_ticks`, `high_ticks`, `low_ticks`, `close_ticks`, `volume` |
| `quote` / `Quote` | `contract`, `bid_ticks`, `ask_ticks`, optional `bid_size`, `ask_size` |
| `trade` / `Trade` | `contract`, `price_ticks`, positive integer `size`, `aggressor`: `buy`, `sell`, or `unknown` |
| `price_point` / `PricePoint` | `symbol`, Decimal `value`, `price_kind`: `mid`, `index`, or `indicative` |
| `gex_snapshot` / `GexSnapshot` | `underlying`, Decimal `spot`/`total_gex`, `walls`, `negative_regions`, boolean `profile_available`, `model_id`, integer array `expiry_days`, `oi_published_at`, `provenance`: `vendor`, `modeled`, or `indicative` |
| `option_snapshot` / `OptionSnapshot` | `underlying`, Decimal `spot`, `rows`, finite number or null `annual_rate`/`dividend_yield`, `rate_provenance`, `dividend_provenance` |

Bars require `start_at < event_at <= available_at` and consistent OHLC ordering.
Bar duration is explicit; only completed one-minute bars support the opening
range. Quotes require `bid_ticks <= ask_ticks`; known sizes are nonnegative whole
contracts. A null size is **unknown liquidity**, never zero or unlimited liquidity.

VIX records use symbol `VIX`, `price_kind="index"`, and a completed one-minute
observation end as `event_at`. Daily values cannot substitute. Underlying mapping
uses its actual symbol and `price_kind="mid"`; `index` and `indicative` values do
not qualify as actual contemporaneous midpoints. An adjusted close requires a
separately named approximation and cannot silently qualify.

`Wall` objects contain `wall_id`, `kind` (`support`/`resistance`), Decimal `strike`
and absolute Decimal `concentration`. `NegativeRegion` objects contain Decimal or
null `lower`/`upper` and `region_id`; null is an unbounded side. Finite bounds must
increase. The only supported normalized `profile_boundary_convention` is the
exact string `lower inclusive, upper exclusive`. Both audit and replay require
that declaration for negative-profile capability. Missing, opposite or unknown
conventions cannot certify an all-positive profile or a negative region. The
importer preserves the original endpoints; it never converts opposite membership
by merely changing a label. A provider whose exact zero/sign-change endpoints do
not satisfy this convention needs an explicitly validated normalization before
full breakout research. Under the canonical convention a mapped lower endpoint
belongs to the region and an upper endpoint does not; no epsilon is added.
Source metadata must also document model semantics and units.
An available all-positive profile has `profile_available=true` and no negative
regions. An unavailable profile has `profile_available=false` and no regions;
it does not establish absence of negative gamma. IDs within a snapshot are unique.
The expiry universe is distinct nonnegative day offsets; the chosen experiment
must explicitly select its intended universe. No universe/model is selected by
the current feasibility audit. OI must be published by snapshot availability.

Every supplied experiment must contain a single actual GEX identity: source ID,
model ID, underlying, expiry-day universe (sorted for canonical identity), and
vendor/modeled/indicative provenance. Import validates this before an `as_of`
cutoff; audit, direct replay and study preflight also validate it. Optional source
metadata fields `model_id`, `expiry_days`, `underlying`, `provenance` and
`source_id` must agree with actual snapshots. A run with no snapshots records
`gex_identity: {"status": "unselected"}`; source labels alone cannot select one.
Otherwise run and study artifacts record `status: "selected"` with the five
actual identity fields, including zero-trade and unsupported attempts. This
provenance validation does not admit future snapshots into feature decisions.

`OptionRow` contains `kind` (`call`/`put`), Decimal `strike`, `expires_at`, integer
`multiplier`/`oi`, `oi_published_at`, and finite nonnegative `gamma`/`iv` or null.
OI availability is checked against snapshot availability; expired rows fail.
Rates and dividends carry explicit provenance even if unavailable. These are
model inputs, never observed dealer inventories or an automatic GEX capability.

Example synthetic bar:

```json
{"type":"mes_bar","source_id":"synthetic_demo","sequence":1,"contract":"MESM6","start_at":"2026-06-15T13:30:00Z","event_at":"2026-06-15T13:31:00Z","available_at":"2026-06-15T13:31:01Z","open_ticks":24000,"high_ticks":24004,"low_ticks":23996,"close_ticks":24002,"volume":10}
```

## Calendar and margin tables

These tables have no `type` or market event common fields:

| Table / dataclass | Required fields |
| --- | --- |
| `sessions.jsonl` / `Session` | ISO date `session_date`, aware `open_at`, `close_at`, `schedule_available_at` |
| `macro_releases.jsonl` / `MacroRelease` | `event_id`, `category`, `scheduled_at`, `schedule_available_at` |
| `margin_points.jsonl` / `MarginPoint` | dated MES `contract`, `available_at`, positive `initial_cents`, `maintenance_cents`, `provenance`: `historical` or `scenario` |

Session open precedes close, both share the stated New York date, and the schedule
is available by open. Include actual early closes. Macro categories are `CPI`,
`PPI`, `EmploymentSituation`, `FOMCDecision`, `FOMCPressConference`,
`ISMManufacturing`, and `ISMServices`. Schedules must be available by release.
Explicit `macro_coverage_dates` in metadata establish calendar completeness on
dates with no scheduled release. The accompanying `macro_schedule_available_at`
must establish that the complete calendar was published by session open; missing
publication or a cutoff before publication leaves macro coverage unavailable.
Absence of rows alone is not evidence of an empty schedule. Calendar provenance is the supplier's explicit attestation, not
a schedule fetched or verified by the importer. Maintenance cannot exceed initial
margin. Scenario margin supports a labeled experiment and never historical
broker executability.

## Metadata, coverage and manifests

`metadata.json` permits `sources` (array), `limitations` (string array), and
`macro_coverage_dates` (ISO date array), and optional aware ISO timestamp
`macro_schedule_available_at`. Each source requires unique `source_id`
and may declare `classification` (`historical`, `synthetic`, `indicative`,
`unknown`), boolean `availability_verified`, positive integer `interval_seconds`,
string-array `limitations`, integer-array `expiry_days`, and these text fields:
`timestamp_semantics`, `model_semantics`, `roll_policy`, `documentation_url`,
`homepage`, `reuse_status`, `model_id`, `profile_boundary_convention`, `gex_units`,
and aware `ingested_at`. Historical availability is unverified by default.
URL fields allow only public HTTP(S) URLs without user info, queries or fragments.
Unknown metadata fields are rejected to keep credentials out; never put secrets
inside any free-text field. Provider entitlement/config files are not imported.

```json
{"sources":[{"source_id":"synthetic_demo","classification":"synthetic","limitations":["Mechanics only"],"interval_seconds":60}],"limitations":["Hand-authored, no market evidence"]}
```

`audit_dataset(dataset, required_rules: frozenset[str]) -> CoverageReport` supports
rule IDs `mes_prices`, `gex`, `negative_regions`, `mapping`, `cvd`, `vix`,
`wall_migration`, `opening_range`, `macro`, and `margin`. Unknown IDs fail closed.
`supported_rules` records input potential; `missing_rules` maps required missing
inputs to readable reasons. `intersection_dates` are common New York dates with
all requested input capabilities, not a promise of complete intraday overlap.
Mapping admission uses the same midpoint history and causal mapper as runtime.
Actual dated-MES `price_point` midpoints and quote-derived midpoints both qualify;
sources/contracts cannot pool minutes, indicative prices do not qualify, and
same event-minute pairing, availability, latest-pair freshness and configured
minimum/window rules all apply. An ambiguous source stream fails closed.

Margin capability requires a causally covered individual execution stream, not
margin for every adjacent-contract bar used only as roll evidence. Replay still
requires exact selected-contract margin at the actual clock; missing active
margin makes performance unavailable, including otherwise flat zero-trade runs.

The audit checks minimal warm-up and cadence for mapping, VIX, opening range and
wall observations. Every later decision must still apply causal joins, exact
freshness, filter semantics and execution gates. Coverage alone never passes a
strategy filter. Macro coverage uses a conservative whole-session requirement:
the complete schedule must be known by session open. A later runtime entry gate
evaluates schedule publication against the actual decision time and must still
reject any future-known schedule; an audit is not permission to bypass that gate.

`source_freshness` records first/last event and availability times plus maximum
latency. `source_gaps` records event-time intervals exceeding declared cadence
(60 seconds if unspecified) separately for each event kind/instrument; these are
observed gaps, not a claim that every source should publish once per minute.
`real`, `synthetic`, `indicative`, `unknown` preserve evidence labels.
`margin_provenance` distinguishes scenarios from history. `classification="full"`
requires requested inputs on a common date, historical source metadata with
verified availability, no indicative inputs and no scenario margins. Synthetic
and indicative datasets retain those classifications even when mechanically
supported. Unknown provenance stays incomplete. Limitations travel with reports.

`write_manifest(dataset_path, config: RunConfig, code_revision: str, output)`
hashes the original recognized files with SHA-256 and records source IDs,
metadata, exact config, coverage and limitations. Importing or writing a manifest
does not read environment variables, credentials, arbitrary files or the network.
`input_hashes` use relative file names. `to_jsonable` serializes shared dataclasses,
Decimals and times for reports; event JSONL additionally requires its `type` tag.

`gamma_bot.config.load_config(path: Path) -> RunConfig` reads a flat TOML file.
Field names and units are shown in `config/research.toml`; omitted fields retain
the accepted defaults. Integer cents/ticks reject floats and booleans. Risk may
be reduced but cannot exceed 1%, daily loss shutdown cannot exceed 3%, and
`required_rules` cannot silently drop filters. Named strategy variants belong to
the strategy layer. No fee or margin scenario is a provider quote.

## Forward recorder contract and current availability

`gamma_bot.data.adapters.SourceAdapter` is a protocol with
`events() -> Iterator[Event]` and `capabilities -> CoverageReport`.
`gamma_bot.data.recorder.record(adapter, output: Path, checkpoint: Path, *,
stop_at: datetime) -> CoverageReport` runs synchronously in the foreground.
It opens no source itself, starts no service, and reads no credentials. The
source audit established **no verified free permitted production adapter**.
Test fixture streams are synthetic mechanics tests, never enabled feeds.

`python -m gamma_bot record --output events.jsonl --checkpoint checkpoint.json
--stop-at 2026-10-28T20:00:00Z` validates an aware stop timestamp, prints
`no_verified_source`, returns exit 2, and writes nothing. Existing audit/replay/
evaluate commands retain their behavior. No provider selection, private endpoint,
trial, broker connection or credential lookup is available in this release.

An adapter must emit typed, credential-free events, use stable source-wide
sequence identities across connections, bound its blocking reads to honor the
caller's deadline, and release resources when its iterator is closed. A normal
iterator exhaustion ends this bounded sample. Only disconnects/timeouts and
`RateLimited` are retried: at most three reconnects total, with 0.1/0.2/0.4-second
backoff capped by remaining time. Adapter failures are replaced by fixed safe
messages without raw exception context. Credentials must never be embedded in
source IDs, event text or capabilities; the generic recorder neither obtains
nor guesses secret values. Capabilities do not supply missing observations or
certify historical provenance.

The caller must give one process exclusive ownership of the output JSONL,
its `.metadata.json` sidecar, and checkpoint. These three paths must differ,
including hard-link aliases. The event file is restart truth: source/sequence
identities and SHA-256 content hashes are rebuilt from its validated records.
A redelivery with unchanged source content is ignored, even if its availability
was changed by receipt timing; a conflicting sequence fails. Other event fields
including actual event time remain intact. Every new event receives
`available_at = max(adapter.available_at, recorder receipt time)`. Thus reconnect
backlogs cannot become actionable before they were observed. Future availability
beyond the requested stop is skipped with an explicit warning.

Each event append is flushed/fsynced before an atomic checkpoint replacement.
The checkpoint records the durable byte offset, prefix SHA-256 and structured
gaps; it contains no provider state or credentials. Restart deduplication does
not depend on a current checkpoint. A stale valid prefix checkpoint is usable;
a malformed/mismatched checkpoint is copied to a recoverable, fsynced
`.quarantine-*` file before replacement. A malformed final event line also causes
a complete original-file quarantine before prefix repair. Interior corruption
fails without modifying the event file. Complete unterminated final records are
preserved and separated with a newline before appending. Quarantines retain
original bytes and must be treated as raw input artifacts, not public reports.

Disconnect, retry exhaustion, skipped-sequence, recovery and restart warnings
persist in metadata limitations. Structured recording gaps are retained in the
checkpoint and unioned with cadence gaps in the returned coverage report. A
subsequent `load_dataset`/audit preserves the prominent metadata warnings but
**does not reconstruct the checkpoint's structured gaps**. Event-time cadence
gaps remain independently auditable. Neither successful parsing, reconnect nor
sequence continuity certifies an uninterrupted usable feed, historical data,
full strategy coverage, or 1–5-minute wall freshness. No production sample or
measured provider cadence is claimed.
