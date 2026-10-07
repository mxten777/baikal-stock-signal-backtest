# STEP 35-D: Fixed-574 Market Regime Audit

Analysis only: STEP 35-C artifacts and A/B/C rules unchanged. No new strategy, fitting or external fetch.

## Predeclared regime rules
- direction: positive_20d_pct <40 BEAR; >60 BULL; otherwise NEUTRAL; missing UNKNOWN
- volatility: 1D cross-sectional std(ddof=1) > lagged expanding median HIGH; <= LOW; 60 past days required
- coverage: feature-specific finite tradable bars; denominator and percentage of fixed 574 reported
- primary: unchanged non-overheated STEP35C crossings; unchanged per-horizon evaluation flags
- bootstrap: 2000 paired 20-session moving blocks over all calendar dates; seed 35; date-weighted cell mean CI
- no_trade: descriptive comparison with zero gross raw return, not an implemented filter or executable portfolio

Feature-specific finite/tradable denominators are explicit; missing values are not counted as down stocks.
Labels use all available contemporaneous members, not only future-target survivors.

## Yearly market state
| year | sessions | return_1d_mean_pct_mean | return_1d_mean_pct_median | return_1d_median_pct_mean | return_1d_median_pct_median | return_5d_mean_pct_mean | return_5d_mean_pct_median | return_5d_median_pct_mean | return_5d_median_pct_median | return_20d_mean_pct_mean | return_20d_mean_pct_median | return_20d_median_pct_mean | return_20d_median_pct_median | positive_1d_pct_mean | positive_1d_pct_median | positive_5d_pct_mean | positive_5d_pct_median | positive_20d_pct_mean | positive_20d_pct_median | close_above_ma20_pct_mean | close_above_ma20_pct_median | ma5_above_ma20_pct_mean | ma5_above_ma20_pct_median | cross_sectional_volatility_1d_pct_mean | cross_sectional_volatility_1d_pct_median | volume_above_ma20_pct_mean | volume_above_ma20_pct_median | volume_ma5_above_ma20_pct_mean | volume_ma5_above_ma20_pct_median | mean_return20_coverage_pct | direction_BEAR_days | direction_BULL_days | direction_NEUTRAL_days | direction_UNKNOWN_days | volatility_regime_HIGH_days | volatility_regime_LOW_days | volatility_regime_UNKNOWN_days |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2024 | 244 | 0.001 | 0.091 | -0.273 | -0.200 | 0.019 | 0.143 | -0.804 | -0.813 | -0.093 | -0.080 | -2.454 | -2.440 | 44.445 | 42.830 | 44.615 | 42.884 | 41.760 | 39.499 | 42.357 | 41.144 | 42.668 | 40.338 | 3.290 | 3.243 | 31.180 | 29.771 | 38.379 | 37.548 | 85.184 | 116 | 20 | 88 | 20 | 77 | 106 | 61 |
| 2025 | 242 | 0.244 | 0.304 | -0.127 | -0.020 | 1.223 | 1.271 | 0.071 | 0.126 | 4.999 | 5.742 | 1.645 | 2.131 | 47.759 | 47.406 | 50.661 | 50.636 | 55.531 | 57.220 | 53.474 | 53.707 | 53.987 | 54.275 | 3.356 | 3.305 | 33.001 | 30.763 | 40.295 | 37.716 | 96.144 | 40 | 104 | 98 | 0 | 134 | 108 | 0 |
| 2026 | 186 | 0.251 | 0.514 | -0.321 | -0.093 | 1.220 | 1.897 | -0.588 | 0.000 | 4.296 | 5.140 | -0.504 | 0.263 | 47.130 | 47.648 | 48.692 | 49.604 | 50.613 | 50.789 | 48.944 | 49.032 | 48.595 | 48.055 | 4.738 | 4.677 | 33.978 | 31.157 | 39.955 | 37.082 | 98.934 | 64 | 66 | 56 | 0 | 184 | 2 | 0 |

## Non-overheated crossing comparisons
| dimension | regime | strategy | horizon | calendar_days | signal_count | evaluated_count | evaluated_dates | mean_return_pct | median_return_pct | win_rate_pct | date_weighted_mean_return_pct |
|---|---|---|---|---|---|---|---|---|---|---|---|
| direction | BEAR | A | 5 | 220 | 958 | 941 | 195 | 0.034 | -0.976 | 44.952 | 0.455 |
| direction | BEAR | A | 10 | 220 | 958 | 933 | 194 | 0.188 | -0.818 | 45.338 | 0.430 |
| direction | BEAR | A | 20 | 220 | 958 | 902 | 187 | 2.214 | -0.579 | 48.559 | 2.465 |
| direction | BULL | A | 5 | 190 | 2436 | 2379 | 182 | 1.720 | 0.000 | 49.433 | 1.342 |
| direction | BULL | A | 10 | 190 | 2436 | 2319 | 177 | 2.547 | 0.000 | 49.633 | 2.688 |
| direction | BULL | A | 20 | 190 | 2436 | 2291 | 175 | 4.961 | 0.737 | 51.811 | 4.810 |
| direction | NEUTRAL | A | 5 | 242 | 1915 | 1864 | 204 | 1.484 | 0.000 | 48.766 | 1.366 |
| direction | NEUTRAL | A | 10 | 242 | 1915 | 1806 | 195 | 3.040 | -0.102 | 49.391 | 2.432 |
| direction | NEUTRAL | A | 20 | 242 | 1915 | 1569 | 174 | 6.132 | 1.028 | 52.390 | 4.577 |
| direction | UNKNOWN | A | 5 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| direction | UNKNOWN | A | 10 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| direction | UNKNOWN | A | 20 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| volatility_regime | HIGH | A | 5 | 395 | 3726 | 3627 | 371 | 1.762 | -0.108 | 48.939 | 1.627 |
| volatility_regime | HIGH | A | 10 | 395 | 3726 | 3532 | 361 | 2.841 | 0.125 | 50.227 | 2.558 |
| volatility_regime | HIGH | A | 20 | 395 | 3726 | 3269 | 336 | 5.659 | 1.225 | 52.799 | 4.918 |
| volatility_regime | LOW | A | 5 | 216 | 1571 | 1545 | 209 | 0.308 | -0.352 | 46.990 | 0.029 |
| volatility_regime | LOW | A | 10 | 216 | 1571 | 1514 | 204 | 1.014 | -0.926 | 45.443 | 0.538 |
| volatility_regime | LOW | A | 20 | 216 | 1571 | 1481 | 199 | 3.034 | -0.234 | 48.548 | 2.249 |
| volatility_regime | UNKNOWN | A | 5 | 61 | 12 | 12 | 1 | 2.165 | 0.930 | 58.333 | 2.165 |
| volatility_regime | UNKNOWN | A | 10 | 61 | 12 | 12 | 1 | 0.126 | -2.138 | 33.333 | 0.126 |
| volatility_regime | UNKNOWN | A | 20 | 61 | 12 | 12 | 1 | -0.923 | -3.849 | 16.667 | -0.923 |
| direction | BEAR | B | 5 | 220 | 2531 | 2504 | 200 | -0.635 | -0.510 | 46.126 | -0.708 |
| direction | BEAR | B | 10 | 220 | 2531 | 2498 | 199 | -0.611 | -0.698 | 46.037 | -0.933 |
| direction | BEAR | B | 20 | 220 | 2531 | 2466 | 193 | 0.237 | -1.149 | 45.296 | -0.107 |
| direction | BULL | B | 5 | 190 | 2107 | 2012 | 179 | 1.669 | 0.000 | 49.155 | 1.168 |
| direction | BULL | B | 10 | 190 | 2107 | 1953 | 174 | 2.219 | 0.000 | 49.770 | 1.277 |
| direction | BULL | B | 20 | 190 | 2107 | 1916 | 172 | 4.072 | 0.373 | 51.305 | 2.687 |
| direction | NEUTRAL | B | 5 | 242 | 3294 | 3217 | 204 | 1.792 | 0.310 | 51.818 | 1.439 |
| direction | NEUTRAL | B | 10 | 242 | 3294 | 3108 | 195 | 3.709 | 0.761 | 53.411 | 3.069 |
| direction | NEUTRAL | B | 20 | 242 | 3294 | 2705 | 174 | 6.403 | 1.435 | 54.011 | 4.064 |
| direction | UNKNOWN | B | 5 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| direction | UNKNOWN | B | 10 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| direction | UNKNOWN | B | 20 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| volatility_regime | HIGH | B | 5 | 395 | 5216 | 5056 | 376 | 1.256 | 0.000 | 49.723 | 0.750 |
| volatility_regime | HIGH | B | 10 | 395 | 5216 | 4932 | 366 | 2.330 | 0.272 | 50.710 | 1.192 |
| volatility_regime | HIGH | B | 20 | 395 | 5216 | 4561 | 341 | 4.458 | 0.587 | 51.326 | 2.416 |
| volatility_regime | LOW | B | 5 | 216 | 2704 | 2665 | 206 | 0.444 | -0.109 | 48.518 | 0.384 |
| volatility_regime | LOW | B | 10 | 216 | 2704 | 2615 | 201 | 1.097 | -0.147 | 48.872 | 0.998 |
| volatility_regime | LOW | B | 20 | 216 | 2704 | 2514 | 197 | 2.144 | -0.383 | 48.290 | 1.654 |
| volatility_regime | UNKNOWN | B | 5 | 61 | 12 | 12 | 1 | 0.044 | -1.201 | 33.333 | 0.044 |
| volatility_regime | UNKNOWN | B | 10 | 61 | 12 | 12 | 1 | -2.034 | -3.819 | 25.000 | -2.034 |
| volatility_regime | UNKNOWN | B | 20 | 61 | 12 | 12 | 1 | -1.171 | 0.871 | 50.000 | -1.171 |
| direction | BEAR | C | 5 | 220 | 1487 | 1464 | 206 | -0.420 | -1.019 | 43.443 | -0.062 |
| direction | BEAR | C | 10 | 220 | 1487 | 1459 | 205 | -0.257 | -1.534 | 43.729 | -0.305 |
| direction | BEAR | C | 20 | 220 | 1487 | 1412 | 199 | 1.104 | -1.934 | 43.626 | 1.218 |
| direction | BULL | C | 5 | 190 | 2575 | 2521 | 182 | 1.618 | -0.364 | 47.441 | 1.209 |
| direction | BULL | C | 10 | 190 | 2575 | 2451 | 177 | 2.498 | -0.422 | 47.654 | 2.461 |
| direction | BULL | C | 20 | 190 | 2575 | 2414 | 175 | 4.824 | 0.410 | 51.118 | 4.440 |
| direction | NEUTRAL | C | 5 | 242 | 2601 | 2522 | 203 | 1.956 | 0.000 | 49.841 | 1.604 |
| direction | NEUTRAL | C | 10 | 242 | 2601 | 2440 | 194 | 3.744 | 0.608 | 52.131 | 3.318 |
| direction | NEUTRAL | C | 20 | 242 | 2601 | 2120 | 173 | 5.756 | 0.680 | 51.792 | 4.090 |
| direction | UNKNOWN | C | 5 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| direction | UNKNOWN | C | 10 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| direction | UNKNOWN | C | 20 | 20 | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| volatility_regime | HIGH | C | 5 | 395 | 4624 | 4511 | 380 | 1.675 | -0.150 | 48.770 | 1.168 |
| volatility_regime | HIGH | C | 10 | 395 | 4624 | 4401 | 370 | 2.919 | 0.000 | 49.739 | 2.200 |
| volatility_regime | HIGH | C | 20 | 395 | 4624 | 4056 | 346 | 5.164 | 0.399 | 50.863 | 3.616 |
| volatility_regime | LOW | C | 5 | 216 | 2030 | 1987 | 210 | 0.420 | -0.556 | 44.540 | 0.423 |
| volatility_regime | LOW | C | 10 | 216 | 2030 | 1940 | 205 | 1.057 | -0.853 | 45.722 | 0.996 |
| volatility_regime | LOW | C | 20 | 216 | 2030 | 1881 | 200 | 2.390 | -0.601 | 46.943 | 2.396 |
| volatility_regime | UNKNOWN | C | 5 | 61 | 9 | 9 | 1 | 0.633 | -0.974 | 44.444 | 0.633 |
| volatility_regime | UNKNOWN | C | 10 | 61 | 9 | 9 | 1 | -1.423 | -4.480 | 22.222 | -1.423 |
| volatility_regime | UNKNOWN | C | 20 | 61 | 9 | 9 | 1 | -3.442 | -5.506 | 22.222 | -3.442 |

## Outputs
- daily_regimes.csv: causal contemporaneous features, eligibility counts and fixed labels.
- regime_comparisons.csv: all/2024/2025/2026, direction/volatility/joint, heat separation.
- correlations.csv: date-mean Spearman diagnostics, not fitted coefficients or causal estimates.
- robustness.csv: fixed top5-date removal, chronological halves and leave-one-year-out diagnostics.
- basket_diagnostics.csv: same-date equal-weight Fixed-574 future-return difference; not benchmark excess or PIT correction.
- cell_bootstrap.csv: descriptive 95% date-weighted mean intervals; sparse cells flagged.

## Interpretation limits
- Frozen 2026-09-17 universe retains survivorship and selection bias.
- Historical PIT and adjusted-price status remain UNVERIFIED.
- Contemporary breadth includes the signal stock itself; shared market exposure creates association, not causation.
- Daily samples have overlapping forward horizons and nonstationary years; block CI is descriptive.
- Regime-specific zero return comparisons do not establish a deployable No-Trade rule.
- Gross raw returns omit opportunity cost, cash yield, fees and execution timing.
- Many correlated comparisons are exploratory; no significance search, threshold optimization or ML fitting.
- Strong gross returns can reflect the whole basket rather than stock-selection edge.
