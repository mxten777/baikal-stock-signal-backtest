# STEP 35 Research closeout - 2026-10-08

Approved implementation starting point: main at
`bfc16621c9a758e1cdfaba0ee512b11ea75320b2`.
This closeout documents existing results without rerunning analysis, changing
rules, fitting models, or optimizing thresholds.

## STEP 35-A: input feasibility and evidence boundary

The retained 35-B/C evidence establishes that the fixed Expanded universe has
574 members (KOSPI 267 / KOSDAQ 307), selected as of 2026-09-17, and that the
existing 2026-10-07 OHLCV snapshot can support retrospective research.
All 574 frozen ticker files were available in the actual 35-C run.

A separately labeled STEP 35-A report is not present in the retained task
record. No additional 35-A execution results are asserted here. The input
facts above are verified by the retained 35-C manifest, rather than attributed
to an unavailable earlier report.

## STEP 35-B: approved design

- Research-only ticker x trading_date panel; no operational integration.
- Fixed 2026-10-07 local OHLCV inputs, immutable 574-member universe.
- Current/past-only returns, MAs/slopes, volume ratios, volatility, RSI/MACD.
- A: existing v0.2 OHLCV score / 65; B: Momentum / nominal 20;
  C: (Volume + Momentum) / nominal 40; each converted to 100 points.
- Existing 75-point crossing and overheat labeling retained; no fitting.
- 60 contiguous-session warm-up and previous-ready crossing requirement.
- Calendar-session 5D/10D/20D raw targets; no delayed exits or price filling.
- 2024 development / 2025 validation / 2026 retrospective holdout, with
  year-boundary target purge.
- Date weighting, 20-session cooldown, common20/120-session sensitivity,
  equal-count ranking and paired date-block bootstrap.

See [implementation guide](FIXED574_OHLCV_RESEARCH.md).

## STEP 35-C: frozen actual results

| Count | Actual |
|---|---:|
| Observed calendar sessions | 672 |
| Calendar-grid panel rows | 385,728 |
| Original OHLCV observations | 372,087 |
| Feature-ready rows | 334,121 |
| Crossing-ready rows | 333,500 |
| 5D evaluable rows after year purge | 325,564 |
| 10D evaluable rows after year purge | 317,028 |
| 20D/common20 evaluable rows after year purge | 300,030 |

| Strategy | All signals | Non-overheated | Overheated |
|---|---:|---:|---:|
| A | 5,473 | 5,309 | 164 |
| B | 9,057 | 7,932 | 1,125 |
| C | 9,967 | 6,663 | 3,304 |

Primary non-overheated crossing results, percentages:

| Strategy | Horizon | Evaluated | Mean | Median | Win rate | Date-weighted mean |
|---|---|---:|---:|---:|---:|---:|
| A | 5D | 5,184 | 1.329 | -0.183 | 48.38 | 1.053 |
| B | 5D | 7,733 | 0.974 | 0.000 | 49.28 | 0.619 |
| C | 5D | 6,507 | 1.291 | -0.329 | 47.47 | 0.902 |
| A | 10D | 5,058 | 2.288 | -0.215 | 48.75 | 1.826 |
| B | 10D | 7,559 | 1.896 | 0.085 | 50.03 | 1.118 |
| C | 10D | 6,350 | 2.344 | -0.287 | 48.47 | 1.765 |
| A | 20D | 4,762 | 4.826 | 0.652 | 51.39 | 3.916 |
| B | 20D | 7,087 | 3.628 | 0.139 | 50.25 | 2.131 |
| C | 20D | 5,946 | 4.273 | 0.000 | 49.58 | 3.157 |

A had the strongest aggregate 5D/20D and date-weighted means; C narrowly led
the 10D signal-weighted mean. All paired strategy-difference bootstrap
intervals included zero. There was no certified winner.

Annual 20D mean returns:

| Strategy | 2024 | 2025 | 2026 |
|---|---:|---:|---:|
| A | 0.224 | 5.219 | 8.194 |
| B | -0.988 | 4.866 | 6.248 |
| C | -1.229 | 5.513 | 7.367 |

2024 was weak, C led 2025 means and A led 2026 means. Direction/ranking did
not persist uniformly. Cooldown 20D means were A 4.560%, B 3.497%, C 3.792%.
Non-overheated overlap: A/B 1,192; A/C 2,877; B/C 2,256; all three 867.
All original comparisons, including negative/overheated results, are retained.

## STEP 35-D: frozen regime findings

Approved pre-result labels: 20D positive breadth <40% BEAR, >60% BULL,
inclusive 40..60% NEUTRAL. HIGH volatility compares current 1D cross-sectional
standard deviation with the lagged expanding median, requiring 60 past days.
These labels do not modify any strategy or filter.

| Daily average feature (%) | 2024 | 2025 | 2026 |
|---|---:|---:|---:|
| Mean 20D return | -0.093 | 4.999 | 4.296 |
| Median 20D return | -2.454 | 1.645 | -0.504 |
| Positive 20D breadth | 41.760 | 55.531 | 50.613 |
| Cross-sectional 1D standard deviation | 3.290 | 3.356 | 4.738 |

2024 was weak/narrow; 2025 broader; 2026 more dispersed. Pooled HIGH-volatility
performance was better, but 184/186 sessions in 2026 were HIGH, and no LOW
20D events in that year were evaluable. Year and volatility effects are
confounded; A/C LOW-volatility 20D means exceeded HIGH in 2025.

B in BEAR had negative short-horizon means: 5D -0.635%, 10D -0.611%;
date-weighted means -0.708%/-0.933%. Their 95% block intervals included zero.
B BEAR 20D annual means were -1.302%, +6.293%, -2.680%: the effect reversed.
This supports only a future No-Trade hypothesis, not a deployed filter.

BULL 20D means were positive across all three years, but 2024 had only 16
evaluable BULL signal dates. Largest-date event shares were about 1.1%-3.0%;
busiest-five-date shares about 4.6%-11.1%. Year dependence was more concerning
than single-date concentration. No ML fitting was authorized or performed.

See [audit guide](../src/research/REGIME_AUDIT.md) and the retained
[findings](../output/research/fixed574_regime_audit_35d/findings.md).

## Test and invariance record

Previously executed during implementation, not rerun for this closeout:

- STEP 35-C focused Research tests: 31 passed.
- STEP 35-C Research + Engine/indicators/Expanded adapter regression: 90 passed.
- STEP 35-D focused causal/boundary/output-isolation tests: 5 passed.
- New Research modules: no reported Pylance errors.
- Actual C and D reruns matched generated artifacts byte-for-byte.
- STEP 35-D verified all nine STEP 35-C artifact files unchanged.
- Both actual pipelines verified 14,144 protected operational files unchanged.
- Full repository regression was not run.

Closeout performs Git whitespace/scope checks, documentation, normal commit
and normal push only. No analysis is rerun and no results are reoptimized.

## Mandatory limits

- Survivorship/selection: fixed 2026-09-17 members omit historical delisted and
  excluded stocks; this is not an unbiased historical market universe.
- PIT/adjustments: retrospective 2026-10-07 prices are not certified as the
  prices known on each historical date; adjustment status remains UNVERIFIED.
- Missing sessions: observed-date union is not an official exchange calendar;
  suspension vs source gap cannot be resolved, and path exclusion can bias.
- Raw close-to-close targets omit costs, dividends, slippage and executable
  entry timing. Gross returns do not establish selection edge.
- Regime/year dependence, overlapping horizons, sparse cells and multiple
  correlated comparisons prevent causal or stable predictive claims.
- 672 dates, about 33 non-overlapping 20-session blocks, are not 385,728
  independent observations. 2026 is retrospective, not Forward Validation.

## Preservation

Per the approved closeout choice, reports, comparison tables, manifests,
confidence intervals and audit diagnostics under these exact directories are
included in Git without regenerating their contents:

- `output/research/fixed574_2026-10-07/`
- `output/research/fixed574_regime_audit_35d/`

Large `panel.csv.gz` and `events.csv.gz` remain local and ignored, as do the
original OHLCV inputs. Their frozen hashes are retained in the committed
35-C artifact manifest:

| Local large artifact | SHA256 |
|---|---|
| panel.csv.gz | 2b0ef27a111b1e4dddbe9d0f3fb57a53b6cf3c509d2cf05bcb9095073bcbaa88 |
| events.csv.gz | 010d261d847679f73d83705b8e646f23c278c3ddcd1a79703bba6c2c7a9c1333 |

A repository checkout alone therefore does not contain the large panel/events
or raw-input snapshots. Keep their current local files for later audit.
No ignore policy or operational files are changed to publish these reports.
Research-output-only Git attributes retain LF line endings on checkout so
the frozen artifact byte hashes are not changed by Windows CRLF conversion.
