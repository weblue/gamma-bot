# Free MES research data feasibility

Audit date: 2026-10-26. Budget: USD 0. Outcome: **`no_gex_history`**.

A real, freely downloadable MES contract sample was inspected. No usable common
historical interval containing GEX/profile, MES execution prices, underlying
mapping prices, intraday VIX and CVD inputs was established. No GEX source, model
or expiry universe is selected. This is an access/coverage finding for the
candidates below, not proof that free data cannot exist elsewhere. No performance
or historical IBKR executability is claimed.

## Source capability matrix

“Unverified” means no authenticated sample was obtained; documentation is not an
entitlement test. No brokerage credentials were supplied or assumed, no account
was created, and no trial/subscription was activated. No credential files were
searched. The example metadata keeps all sources disabled.

| Source / access class | Current primary evidence | Audit result and missing coverage |
| --- | --- | --- |
| GEXtool / free dashboard; paid export | [Dashboard, FAQ and methodology](https://gextool.com/) advertise delayed prices, 15-minute snapshots and USD 4.99/month plus VAT supporter CSV export. Model: Black–Scholes from quoted IV, calls positive/puts negative, prior-session OI; expiry filter and spot-dependent profile. | Public page and CSV link inspected; CSV link returned the dashboard, no historical file. No paid export attempted. Historical availability timestamps, expiry universe and free retention/automation rights remain unverified. Cadence fails the full wall-stability rule even if the dashboard is viewable. |
| Alpaca / account-included Basic API | [Historical options](https://docs.alpaca.markets/us/docs/historical-option-data) advertises history since February 2024 and distinguishes modified indicative quotes/delayed derivative trades from subscribed OPRA. [Plans/authentication](https://docs.alpaca.markets/us/docs/about-market-data-api) list Basic as free, latest-15-minute historical restriction, and required API keys. | No authenticated requests; actual historical OPRA entitlement and sample availability are unverified. A [provider article](https://alpaca.markets/learn/fetch-historical-data) says older-than-15-minute data is accessible on all feeds, which differs from the subscription language. Do not resolve that conflict by assumption. |
| Tradier / brokerage-account-included API; delayed sandbox | [Market data](https://docs.tradier.com/docs/market-data) limits real-time stocks/options to brokerage account holders; ORATS Greeks update hourly starting around 10:15; sandbox has no Greeks. [History](https://docs.tradier.com/docs/historical-data) excludes expired options and limits intraday retention. | No token/account tested. No historical as-of chain/OI/profile obtained. Hourly Greeks cannot establish three fresh snapshots with gaps at most five minutes. Sandbox delay is not historical GEX access. |
| IBKR / account and instrument entitlements | [Current streaming documentation](https://www.interactivebrokers.com/docs/tws-api/doc/md/streaming-data) describes subscriptions and time-based snapshots. [Unavailable history](https://ibkrcampus.com/docs/web-api/v1/endpoints/market-data/unavailable-historical-data) excludes expired options and futures beyond two years after expiry. | Existing username, permissions, subscriptions, TWS/Gateway session and free/delayed entitlement are all unverified. No API calls or historical MES ticks obtained. Delayed viewing is insufficient evidence of historical access or exchange aggressor flags. The [legacy time-and-sales schema](https://interactivebrokers.github.io/tws-api/historical_time_and_sales.html) is explicitly deprecated and does not establish current free access. |
| Portara / anonymous complimentary MES sample | [MES product/sample page](https://portaracqg.com/futures/int/mes/) identifies CQG provenance and publicly links `MES2026M.txt`. | Actual 1-minute dated-contract file downloaded and inspected; details below. No quotes, ticks, OI or GEX. A sample is not full-history access. |
| Historical Futures Data / anonymous bar sample, account/paid archives | [Documentation](https://www.historicalfuturesdata.com/docs) advertises latest-year 10-second Parquet samples without login, account-gated 1-minute/daily bars, paid tick/full history, UTC buckets, and separate per-contract/continuous data. | Documentation inspected only; no Parquet bytes, contract provenance, raw-price/roll mapping or license verified. Not a second validated sample. |
| Cboe / public daily VIX history | [Official history page](https://www.cboe.com/tradable-products/vix/vix-historical-data) links daily history, rather than the required one-minute observations. | CSV link was attempted in the web reader and rejected as unsupported `text/csv`; no VIX file retained or hash claimed. Daily observations cannot supply the intraday SMA10 or freshness gate. |
| Official calendars / public pages | BLS, Federal Reserve, ISM and NYSE pages inspected for June 2026 (below). | Sample-day schedule context established from currently published pages. No historical revision archive or machine-readable calendar file downloaded. |
| IBKR margin / public current page | [Futures margin URL](https://www.interactivebrokers.com/en/trading/margin-futures-fops.php) redirected to the general margin page; no MES row in the rendered response. | No dated 2026-06-15 broker margin evidence or authenticated account margin. Historical executability remains unavailable; future replay must label any margin amount as a scenario. |

Alpaca's [latest chain endpoint](https://docs.alpaca.markets/us/reference/optionchain)
provides latest Greeks/trade/quote snapshots; it is not an as-of historical chain.
Its [contract model](https://alpaca.markets/sdks/python/api_reference/trading/models.html)
has optional `open_interest` and `open_interest_date`. Neither establishes an OI
publication timestamp or historical OI series. Rebuilding a causal profile needs
those inputs plus model/IV/expiry/rates/dividends semantics; no local reconstruction
is enabled from this audit. Indicative quote modification and information delay
must be represented separately. Downloading old information today alone does not
imply it was unavailable at the historical decision time.

## Inspected MES bytes

- Download: [Portara complimentary MES2026M sample](https://portaradownloadersampledata.s3.amazonaws.com/1%20Minute/MES/MES2026M.txt).
- Local ignored path: `data/raw/portara/MES2026M.txt`.
- SHA-256: `7da69432bae92af786e6d8c450bb64e12e0e4964afbe086b9c19ec7816a45a29`.
- Size: 49,181 bytes; header plus 970 records. CSV fields:
  `Date,Time,Open,High,Low,Close,Volume`. Contract is identified by the linked
  filename/vendor page as MES June 2026; no per-row contract field is present.
- Observed labels: 2026-06-15 06:50 through 23:59 `America/Chicago`.
  UTC conversion: 2026-06-15T11:50:00Z through 2026-06-16T04:59:00Z.
  These are record labels, not verified arrival/publication times.
- Vendor page specifies Chicago-local sample times and start-of-bar as its
  default format (end-of-bar is also offered). The file itself has no timezone,
  bar-boundary or availability metadata. Earliest provisional completion is label
  plus one minute; the unknown dissemination latency prevents a verified causal
  full-strategy replay.
- All 970 timestamps are unique and strictly increasing. All OHLC values obey
  high/low ordering and the 0.25-point lattice; volumes are nonnegative integers.
  The only inter-record gap is 15:59 to 17:00 Chicago (60 missing minute labels).
  No explanation for that gap is inferred from prices alone.
- All 390 labels in 09:30 inclusive–16:00 exclusive ET exist, including the
  15-minute opening range and 45-minute entry window. This does not imply the
  file covers a complete Globex session: evening records belong to the next
  trading session and cannot be grouped by calendar date alone.

[CME contract specifications](https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.contractSpecs.html)
confirm USD 5 per point and 0.25 points per tick (USD 1.25). A single explicit
contract avoids a continuous-series adjustment in this sample; independent
exchange reconciliation was not performed. No roll is needed within the candidate
cash session. A multi-day study still needs a declared causal roll policy and
unadjusted contract prices. Bars lack spread/queue evidence: next-bar-open fills,
adverse slippage and conservative intrabar sequencing would be approximations.

## Candidate-day overlap and calendar checks

The candidate is **2026-06-15**, selected because it is the downloaded sample's
only calendar date, before examining any strategy returns. No returns were run.

| Required input | Evidence for the candidate cash session |
| --- | --- |
| MES execution prices | Dated contract OHLCV labels present throughout; quotes and actual availability absent. |
| GEX underlying prices / mapping | No contemporaneous SPY bid/ask or midpoint sample obtained. A fixed 10× conversion is ineligible. |
| GEX, OI, model and negative-region profile | No export/as-of history obtained; no selected model or 0–7-day expiry universe. |
| VIX | No ten completed one-minute values/availability times; daily history cannot substitute. |
| CVD | Bars contain aggregate volume only. Neither real aggressor direction nor ordered trade/quote classification inputs exist. OHLCV cannot establish CVD divergence. |
| Macro/session schedule | Official pages checked as detailed below; no listed required macro release that day. |
| Historical broker margin | Unavailable. A scenario must not be described as historical IBKR margin. |

The [BLS June calendar](https://www.bls.gov/schedule/2026/06_sched.htm) lists
Employment Situation June 5, CPI June 10 and PPI June 11, all 08:30 Eastern Time;
June 15 has no listed release. The page reports last modified June 10, 2026.
The [FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
lists June 16–17, not June 15. No decision/press-conference time is invented for a
nonmeeting day. The [ISM calendar](https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/)
lists Manufacturing June 1 and Services June 3, not June 15. Its generic time text
says “EST” even for summer dates; this ambiguity must be resolved against dated
release notices before using a release-day timestamp. The separate June 17
Supply Chain Planning Forecast is not one of the specified PMI events.

[NYSE hours/holidays](https://www.nyse.com/markets/hours-calendars) lists June 19
as the June holiday and no June 15 early close. June 15 therefore uses the posted
09:30–16:00 ET core session, with a 15:55 flatten deadline. This is the strategy's
equity-session gate, not a claim that MES only trades those hours. These current
calendar checks establish scheduled context, not a point-in-time archive of all
unscheduled announcements. Missing schedule provenance on other dates must block
full-strategy entries.

## Retention, automation and supported outcome

Only the expressly offered complimentary MES file was downloaded, for local
evaluation. It stays under ignored `data/raw/`; no licensed market bytes are
committed. Portara's [one-off purchase license](https://portaracqg.com/license/)
permits internal research and prohibits redistribution, but its application to
complimentary samples and any distinct sample retention period are unspecified.
No perpetual sample license is asserted. Limit retention to this local audit;
obtain explicit sample terms before redistribution, broader reuse or a recurring
collector. No bulk crawl/recorder is approved.

API documentation establishes technical automation interfaces, not the user's
account entitlements or permission to redistribute/store a feed indefinitely.
Alpaca/Tradier/IBKR remain account/terms/entitlement checks before forward capture.
GEXtool free dashboard access does not establish automated archive rights.
Historical Futures Data's documented download API does not verify upstream rights
or this audit's uninspected files. No trial credit is treated as a durable free
archive. No rate limits or access controls were bypassed.

`full` and `reduced` GEX experiments are unavailable on the verified evidence.
There is no nonempty verified common interval. Price-only plumbing could later
use this sample, subject to its terms and timestamp limitations, but would omit
GEX regime, walls, concentration/migration, negative-region boundaries, SPY/MES
midpoint mapping, intraday VIX filters and CVD divergence. It must also label
bar-fill and margin scenarios. Such an experiment cannot validate either GEX
hypothesis. Missing full-strategy inputs always mean ineligible setups, never
passed filters. Preserve the approved defaults; this finding changes data
eligibility, not the strategy rules.

## Reproduction and verification

The sample URL was extracted from the public product page's `data-url` and
Dataset `contentUrl`. A sandbox `curl` first failed DNS resolution (exit 6); the
same public-page fetch and then the explicit sample download succeeded with
approved network access. No account-gated endpoint was probed: missing credentials
are recorded as missing access, not failed provider entitlements.

```sh
curl -fSL 'https://portaradownloadersampledata.s3.amazonaws.com/1%20Minute/MES/MES2026M.txt' -o data/raw/portara/MES2026M.txt
shasum -a 256 data/raw/portara/MES2026M.txt
git check-ignore data/raw/portara/MES2026M.txt
git diff --check
```

Python standard-library `csv`, `datetime`, `zoneinfo`, `decimal`, `hashlib` and
`tomllib` were used to inspect actual records, timestamps, OHLC/tick/volume
invariants, sample hashes and metadata syntax. No source-text tests or synthetic
market evidence were added. The local raw file is deliberately absent from a
fresh clone; re-download and match the hash before treating it as this evidence.
