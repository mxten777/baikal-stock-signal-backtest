# V03-LR-RANK-10D-v1 infrastructure

STEP 38-A implements infrastructure only. Nothing runs on import. No CLI,
network fetch, scheduler, Production integration or automatic retrospective
training is supplied. Actual 2024 training / 2025 validation / 2026 diagnostics
require STEP 38-B approval.

## Environment and tests

Install `requirements-research.txt` separately from Production requirements.
scikit-learn 1.9.1 retains `penalty="l2"`; its FutureWarning is recorded and
re-emitted, not hidden or treated as convergence failure.

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_ai_ranking.py -q
```

Tests use synthetic data. The local-artifact test checks hashes only, never
fits or evaluates real candidates. A read-only `load_inputs(root)` and
`build_candidates(inputs)` infrastructure probe is also safe without fitting.

## Contract and API

- `load_inputs`: verifies STEP35C hashes against the 35D manifest, frozen raw
  inputs/source hashes, recalculated causal daily regimes and 10D basket.
  Records exact loaded-input hashes and verifies operational scope.
- `build_candidates`: only non-overheated A/B/C crossing union; one row per
  date/ticker. Retains all three membership flags. Rejects duplicate strategy
  crossings, conflicting scores/heat, missing events and invalid Panel joins.
  Does not filter future target availability.
- `feature_matrix`: exactly return_20d_pct, rsi, volume_ratio_5d_20d,
  volatility_20d_pct, positive_20d_pct in that order; finite values required.
  Target/evaluation columns remain metadata, never model inputs.
- `attach_labels`: Z = raw 10D minus same-date Fixed-574 basket; y = 1[Z > 0].
  Checks exact calendar +10 exit, available-through maturity, same-year purge
  and existing target eligibility. Missing labels remain missing.
- `fit_train`: mature/purged 2024 Train only; Train-only StandardScaler and
  L2 LogisticRegression (C=1, lbfgs, tol=1e-6, max_iter=2000,
  class_weight=None). Weights are inverse labelled Train candidate count
  per date, normalized to mean 1. No tuning/refit on Validation.
- `predict` / `selections`: probabilities and A/B/C scores descending,
  ticker ascending for ties. ceil(20%); N < 5 means insufficient candidates.
  Selection occurs before target checks, with no missing-label replacements.
- `evaluate`: one retrospective year at a time; complete AI/Rule selected
  sets only. Primary delta_t = mean(Z_AI) - mean(Z_score_A), in pp.
  Date-equal mean; secondary median, relative win-rate difference and B/C
  comparisons. Reports selection label availability, mature pair availability,
  half-calendar stability, Regime cells (under 30 pairs: SPARSE), best-five
  difference removal and full-calendar paired 20-session moving-block
  bootstrap (2000 repeats, seed 37).
- `provenance`: contract, input/code/requirements hashes, exact features,
  splits, model/ranking/bootstrap parameters, versions, warnings and fitted
  scaler/coefficients when a model is supplied.
- `write_new_json`: only direct JSON children of
  output/research/V03-LR-RANK-10D-v1; existing files always rejected.
- `research_scope`: wrap future approved runs to hash protected operational
  files and all preexisting Research artifacts before/after, including on
  exceptions. Only new files directly inside the contract output are allowed.
  Unexpected shared-environment changes raise, never roll back other work.

The caller must pass only `label_ready` Train rows to `fit_train` and supply
the correct year-specific calendar to `evaluate`. Empty or invalid data
produces explicit errors or labelled insufficient-data diagnostics, not
success-shaped fallback predictions.

Coverage explicitly separates the last ten immature/year-boundary sessions
from mature decisions. The 95% label-availability gate uses selected rows on
mature decision dates; all withheld selections and missing-label dates are
also reported. Pair availability uses mature decision dates with N >=5.

## Interpretation and approval boundary

2025 is Validation; already-viewed 2026 is retrospective diagnostic, never
unseen Test. The 2024 model remains frozen. Future forward-test orchestration
is deliberately not implemented here.

STEP37 gates remain: Validation >=200 paired dates, diagnostic >=150,
mature pair availability >=90%, method label availability >=95%.
Validation improvement >=0.25pp with 95% CI lower bound >0; both halves
nonnegative and best-five removal positive. Diagnostic mean positive and
both halves nonnegative. Final unseen forward requires 252 entry sessions
plus 10-session label follow-up, >=200 paired dates, each direction >=30
pairs, the same effect/CI/stability gates, direction deltas >=-0.25pp and
nonnegative B/C comparisons. These are approval criteria, not automatically
issued AI superiority decisions in this infrastructure stage.

Same-date basket construction includes future-path eligibility and is only
a retrospective research reference, not official index excess. Historical
PIT/adjustments and survivorship remain unverified. All features require
end-of-day data; close-to-close labels are gross diagnostics, not executable
P&L. Missing selection labels and unmatched pair dates must be reported.
