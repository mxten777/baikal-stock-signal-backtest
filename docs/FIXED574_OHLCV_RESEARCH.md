# STEP 35-C: Fixed-574 OHLCV Research

This is isolated retrospective research, not Production, Expanded candidate
selection, Discovery performance, or Forward Validation. No operational entry
point imports the Research namespace.

## Run

From the repository root, using the existing environment:

```powershell
& .\.venv\Scripts\python.exe -m scripts.fixed574_research
```

Only existing local inputs are read:

- `data/expanded_shadow/universe/expanded_universe_574.csv`
- `data/expanded_shadow/market/2026-10-07/{ticker}.csv`

No external fetch, fitting, threshold search, scheduler, API, or dashboard
integration is performed. Missing files remain explicit; no older snapshot
fallback or Production data is used.

Outputs are confined to `output/research/fixed574_2026-10-07/`. The input freeze
manifest pins file hashes, code hashes, runtime, universe, calendar, and rules.
Existing artifacts must match byte-for-byte on rerun; a mismatch is an error,
not an overwrite. Changes to the rules require a separately approved experiment
and output identity. The baseline commit is the approved starting point, not a
claim that newly added Research code has been committed.

## Panel and features

One row per fixed ticker and observed market session, including missing bars.
`observed_rows` counts actual input bars; `panel_rows` counts the full calendar
grid. The session calendar is the union of dates present in the frozen OHLCV.
Whole-market missing sessions cannot be certified without an official calendar.

Only current/past contiguous valid, positive-volume bars enter features.
Missing, invalid, or zero-volume bars restart the indicator run and warm-up.
No price or volume imputation is used.

- 1/5/10/20-session returns: `100 * (close / lagged_close - 1)`.
- MA5/20/60 and MA relationships/distances.
- MA20 1/5-session percentage slope.
- Current volume / MA20, and volume MA5 / MA20.
- 20-session sample standard deviation of 1D percentage returns (`ddof=1`).
- Existing RSI14 and MACD12/26/9 implementations, including their seeds.
- Existing Trend, Volume, Momentum and v0.2 penalty functions, unchanged.

Features require 60 contiguous valid sessions. Crossing also requires the
previous session to be feature-ready; the first ready row cannot signal.
120-session warm-up is a predeclared sensitivity, not an optimized replacement.

## Fixed strategies

| Strategy | Score |
|---|---|
| A | Existing v0.2 penalty-adjusted raw / 65 * 100 |
| B | Existing Momentum / nominal 20 * 100 |
| C | Existing (Volume + Momentum) / nominal 40 * 100 |

Scores round to one decimal using the existing rounding convention. The actual
Momentum maximum is 17; its denominator remains 20, not rescaled.

All use score >= 75 and previous score < 75. B/C have no added v0.2 penalties.
Existing overheat rules (`RSI > 75`, `5D > 20%`, `volume ratio > 4`) only label
the event. Primary comparisons use non-overheated crossings. Overheated/all
events are separately reported; overheat does not reset crossing state.

Cooldown retains the first non-overheated event per ticker/strategy, suppresses
the following 20 market sessions, and does not consult future target availability.
Daily top/bottom 10% ranking uses all feature-ready tickers, `ceil(n * 0.10)`,
and ticker ascending for tied scores, before any target exclusions. The `all`
heat cohort preserves equal selection counts; heat-specific subsets need not.

## Targets and splits

5/10/20-session target: `100 * (close[t+h] / close[t] - 1)`.
Every session on the entry-to-exit path must be a valid positive-volume bar.
Missing exits are never replaced by the next available observation. Last
unmatured sessions remain `IMMATURE`, not zero returns.

2024 = development; 2025 = validation; 2026 = retrospective holdout.
Targets crossing a year boundary are purged from that year's evaluation.
Past OHLCV is permitted for later-period warm-up. No random row split is used.
Global maturity counts and post-purge evaluation counts are separately reported.
`common20` uses the same evaluable 5D/10D/20D cohort.

## Comparisons

`comparisons.csv` contains all/year/market/month scopes, all/non-overheated/
overheated cohorts, horizon-specific means, medians, win rates (`return > 0`),
P10/P25/P75/P90 and date-weighted means/win rates. Empty months remain missing.
Signal counts, target attrition, overlap/Jaccard, score quantiles with ties
unbroken, monthly stability and date concentration are in `summary.json`.

`bootstrap.json` uses 2,000 paired moving-block samples of 20 consecutive
sessions, seed 35. All stocks/strategies on a date stay together; zero-signal
dates remain in the sampling calendar. It reports 95% percentile intervals
for mean and date-weighted mean plus paired differences of strategy means.
This is conditional, descriptive uncertainty, not independent-stock inference
or bias correction. Sparse effective samples are flagged.

## Protection and validation

Input data, non-Research output, operational source/scripts, and dashboard
sources are hashed before and after execution. Cache/dependency/build trees
are excluded. Any protected-file addition, removal, or content change fails.
This detects concurrent operational changes but does not roll them back.

Focused tests:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_fixed574_research.py -q
```

Tests cover future perturbation, truncated prefixes, existing component parity,
missing-session horizons, warm-up/crossing/overheat, year purge, cooldown,
ranking ties, overlap, date weighting, bootstrap pairing, network prohibition,
operational immutability, frozen-input mismatch and deterministic reruns.

## Mandatory interpretation limits

- The universe was selected on 2026-09-17: survivorship/selection bias remains.
- Historical point-in-time availability and price adjustment status are
  UNVERIFIED in the 2026-10-07 snapshot.
- Missing rows cannot distinguish suspensions from source gaps; removing
  missing-path targets may itself introduce selection bias.
- Market labels are universe-snapshot attributes, not historical transfer data.
- Raw close-to-close returns are not executable trading P&L; they omit costs,
  slippage, dividends and benchmark returns.
- 2026 is not prospective Forward Validation.

Results must be reported unchanged, including negative or unstable outcomes.

## Frozen completion record

Actual STEP 35-C/D counts, comparisons, limitations and previously executed
tests are recorded in [the STEP 35 closeout](STEP_35_RESEARCH_COMPLETION.md).
Reports/manifests/comparison tables are preserved in Git; the large compressed
panel/events and raw inputs remain local, with their hashes recorded.
