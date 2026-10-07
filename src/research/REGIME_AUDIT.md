# STEP 35-D: Market Regime Audit

Run with the existing environment from the repository root:

```powershell
& .\.venv\Scripts\python.exe -m src.research.regime_audit
& .\.venv\Scripts\python.exe -m pytest src\research\test_regime_audit.py -q
```

Only frozen STEP 35-C panel/events are analyzed. The existing 35-C outputs,
code and strategies are not modified or regenerated. New derived analysis
goes to `output/research/fixed574_regime_audit_35d/`.

Direction labels approved before examining results: less than 40% of eligible
stocks with positive 20-session returns = BEAR; greater than 60% = BULL;
inclusive 40..60% = NEUTRAL. Volatility HIGH/LOW compares the day's
cross-sectional sample standard deviation of 1D returns to the median of
available earlier daily values, requiring 60 earlier dates. No current/future
observation enters that baseline. Early labels remain UNKNOWN.

Features use feature-specific finite, tradable bars; missing returns are not
treated as negative. Denominators, fixed-universe coverage and early warm-up
are reported. MA and volume diffusion use eligible values only.

Signal labels join on the signal's own trading date. Future returns retain
35-C eligibility and year-boundary purges. Primary comparisons are unchanged
non-overheated crossings; all and overheated comparisons are separate.

Year-stratified cells, date weighting, continuous rank correlations, joint
labels, leave-one-year-out, chronological halves, removal of the best five
dates and busiest five dates diagnose instability. Best-date removal is
explicitly a retrospective sensitivity, never a feature or filter.

2,000 paired 20-session block resamples (seed 35) provide descriptive
date-weighted cell confidence intervals; sparse cells are flagged.
Fixed-574 same-date basket differences help distinguish market exposure from
stock selection, but are not official benchmark excess returns.

Negative-regime results support only a No-Trade hypothesis. No filter,
strategy, optimized threshold or fitted model is generated. Survival/PIT,
missing-session and execution limitations from STEP 35-C still apply.
