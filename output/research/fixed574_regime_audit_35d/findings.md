# STEP 35-D: Findings and decision

## A. Causal regime features

Inputs are the frozen STEP 35-C panel and crossing events. Features on date t
use only date t/past features: cross-sectional mean/median 1D, 5D, 20D returns,
positive-return breadth, close above MA20 breadth, MA5 above MA20 breadth,
sample standard deviation of 1D returns, and volume diffusion (volume/MA20 > 1
and volume MA5/MA20 > 1). Each has its own available-stock denominator.

Approved before results: BEAR = positive 20D breadth < 40%; BULL = > 60%;
NEUTRAL = inclusive 40..60%. HIGH volatility is above the lagged expanding
median of daily 1D cross-sectional standard deviation, with 60 past values
required; LOW is at/below. These are analysis labels, not trading filters.

## B. Annual market-state differences

| Metric (daily average, %) | 2024 | 2025 | 2026 |
|---|---:|---:|---:|
| Cross-sectional mean 20D return | -0.093 | 4.999 | 4.296 |
| Cross-sectional median 20D return | -2.454 | 1.645 | -0.504 |
| Positive 20D breadth | 41.760 | 55.531 | 50.613 |
| Close above MA20 breadth | 42.357 | 53.474 | 48.944 |
| Cross-sectional 1D standard deviation | 3.290 | 3.356 | 4.738 |
| Volume above MA20 breadth | 31.180 | 33.001 | 33.978 |
| 20D feature coverage of fixed universe | 85.184 | 96.144 | 98.934 |

| Session count | 2024 | 2025 | 2026 |
|---|---:|---:|---:|
| BEAR / NEUTRAL / BULL / UNKNOWN | 116 / 88 / 20 / 20 | 40 / 98 / 104 / 0 | 64 / 56 / 66 / 0 |
| HIGH / LOW / UNKNOWN volatility | 77 / 106 / 61 | 134 / 108 / 0 | 184 / 2 / 0 |

2024 was weak/narrow; 2025 had broader gains. In 2026 high average returns
coexisted with negative average cross-sectional median 20D returns and much
higher dispersion. This is consistent with uneven winners rather than a
uniformly rising fixed universe. Early-2024 coverage/warm-up is not comparable
to later full-coverage periods.

## C. Regime x strategy

Unchanged non-overheated crossings, horizon-specific evaluation and year
purges. Values below are signal-weighted mean raw returns (%).

| Regime | Strategy | 5D | 10D | 20D |
|---|---|---:|---:|---:|
| BEAR | A | 0.034 | 0.188 | 2.214 |
| BEAR | B | -0.635 | -0.611 | 0.237 |
| BEAR | C | -0.420 | -0.257 | 1.104 |
| NEUTRAL | A | 1.484 | 3.040 | 6.132 |
| NEUTRAL | B | 1.792 | 3.709 | 6.403 |
| NEUTRAL | C | 1.956 | 3.744 | 5.756 |
| BULL | A | 1.720 | 2.547 | 4.961 |
| BULL | B | 1.669 | 2.219 | 4.072 |
| BULL | C | 1.618 | 2.498 | 4.824 |
| LOW volatility | A | 0.308 | 1.014 | 3.034 |
| LOW volatility | B | 0.444 | 1.097 | 2.144 |
| LOW volatility | C | 0.420 | 1.057 | 2.390 |
| HIGH volatility | A | 1.762 | 2.841 | 5.659 |
| HIGH volatility | B | 1.256 | 2.330 | 4.458 |
| HIGH volatility | C | 1.675 | 2.919 | 5.164 |

Full sample counts, medians, win rates, date weighting, yearly and joint-regime
cells are in [regime_comparisons.csv](regime_comparisons.csv).

## D. Favorable/unfavorable states

A is relatively resilient in BEAR and stronger in BULL/NEUTRAL, especially
20D. B is weakest in BEAR; its better observations are NEUTRAL. C is weak in
short-horizon BEAR, stronger in NEUTRAL/BULL.

All strategies have higher pooled means in HIGH volatility, but this is not
an independent or repeatable volatility premium. 184/186 sessions in 2026
are HIGH, and no 2026 LOW-volatility 20D events are evaluable. A/C actually
have higher LOW than HIGH 20D means in 2025.

Date-weighted 20D differences versus the same-date Fixed-574 eligible basket:
A BEAR/BULL/NEUTRAL = +0.390/+1.548/+1.059 percentage points;
B = -1.448/-0.505/+0.581; C = -0.197/+1.179/+0.510.
Gross positive returns do not by themselves establish stock-selection edge.
This basket is a research diagnostic, not official benchmark excess.

## E. No-Trade hypothesis

The most concrete candidate is B in BEAR at 5D/10D: pooled date-weighted
means -0.708%/-0.933%, with medians -0.510%/-0.698% and win rates
46.126%/46.037%. B's BEAR 5D date-weighted mean remains negative after
removing each year in turn and after removing the busiest/best five dates.

However, 95% block intervals are [-1.835%, +0.581%] (5D) and
[-2.972%, +1.497%] (10D). Both include zero. BEAR 10D/20D is profitable for
B in 2025. C BEAR short-horizon results are also weak but less stable.

Thus this supports a hypothesis for future predeclared validation, NOT a
universal No-Trade regime, deployed gate, or post-hoc rule modification.

## F. Repetition and concentration

Direction effects are not stationary. BEAR B 20D means by year:
-1.302%, +6.293%, -2.680%. NEUTRAL 20D date-weighted means change from
negative in 2024 to strongly positive in 2026 for all strategies.

BULL 20D means are positive for A/B/C in all three years, but 2024 has only
16 evaluable BULL signal dates. Leave-one-year-out and best-five-date removal
keep pooled BULL means positive; this is a promising association, not proof.

Across non-UNKNOWN direction/volatility 20D cells, the largest single date
accounts for about 1.1%-3.0% of events, and busiest five dates 4.6%-11.1%.
Removing the best five dates leaves all pooled BULL/NEUTRAL/HIGH/LOW
date-weighted 20D means positive. Regime-year dependence is more concerning
than a single-date explanation. Some weak BEAR cells change sign.

20D date-level Spearman relationship with dispersion flips: A/B/C in 2026
= -0.231/-0.329/-0.325, despite a pooled HIGH-volatility advantage.
Volume diffusion correlations are positive in 2025/2026 but weak/mixed in
2024. These are correlated descriptive checks, not fitted predictors.

## G. Is ML worthwhile next?

There is value in testing regime features as context in a future locked
walk-forward research protocol, but no basis to train/deploy ML now.
There are 672 dates, not 385,728 independent observations; only about 33
non-overlapping 20-session blocks across three changing yearly regimes.
2026 LOW volatility is almost absent, and historical PIT/survivorship bias
remains.

Before ML, predeclare the target and chronological validation, test simple
fixed-regime hypotheses without tuning, and obtain genuinely unseen
Research-only observations. Any later ML experiment must compare against
the frozen A/B/C baseline and simple non-ML controls, with date-grouped
purged walk-forward splits. This audit does not authorize ML fitting.

## Verification

- 5 focused causal/boundary/output-isolation tests passed.
- Actual audit rerun matched all generated artifacts byte-for-byte.
- All 9 STEP 35-C artifact files were unchanged.
- 14,144 protected operational files were unchanged.
- No Engine, Forward Validation, scheduler, API, dashboard, thresholds,
  universe, existing Research strategies or ledger changes.
- No external fetch, fitting, commit or push.
