# STEP 36 through STEP 38-A Research closeout - 2026-10-08

Starting point: main at `12c3e8fcc8c1c96631d0ece29f515754fc73533a`.
Contract: `V03-LR-RANK-10D-v1`.
This closeout freezes infrastructure, not an AI performance finding.
No real 2024 Train -> 2025 Validation -> 2026 diagnostic fit, prediction,
performance evaluation or output generation has been executed.

## STEP 36: AI feasibility - CONDITIONAL GO

The [STEP35 completion](STEP_35_RESEARCH_COMPLETION.md), source and local
artifacts establish 574 fixed stocks, 672 sessions (2024-01-02..2026-10-07),
385,728 panel rows, 372,087 observed bars and 334,121 feature-ready rows.
Non-overheated A/B/C crossing counts are 5,309 / 7,932 / 6,663;
their deduplicated date/ticker union contains 14,446 candidates.

Signal Ranking was prioritized over Strategy Selection and Dynamic Strategy
Weighting: it uses actual stock/date candidates, can compare equal daily
selection counts and does not change the existing Signal thresholds.
Strategy selection has few effective date/regime samples and unstable annual
winners; dynamic weights add portfolio, overlap and tuning degrees of freedom.

L2 Logistic Regression was selected before Random Forest or boosting because
it is interpretable and limits complexity/tuning given the small effective
sample. The hundreds of thousands of panel rows are not independent:
672 dates provide only about 33 non-overlapping 20-session blocks.

Historical point-in-time prices/adjustments are UNVERIFIED, the universe was
selected on 2026-09-17, missing paths can bias labels and 2026 results were
already inspected. Thus CONDITIONAL GO authorized small isolated research,
not AI superiority, deployment or changes to Production/v0.2.
Expanded Foreign/Institution history is not a complete 574-stock long panel;
it and financial/news inputs are excluded from the first contract.

## STEP 37: frozen experiment contract

Research question: does L2 Logistic Regression rank A/B/C non-overheated
crossings better than fixed Rule-Based ranking for future 10D relative returns?

### Candidates, features and target

- Union of unchanged A/B/C non-overheated crossing events.
- Exactly one row per `(trading_date, ticker)`; retain all membership flags.
- Panel joins must be one-to-one; duplicate strategy events, score/heat
  conflicts and missing Panel rows are errors.
- Do not remove candidates using future target availability.

Exactly five features, available only after the current session's close:

| Feature | Purpose |
|---|---|
| return_20d_pct | Medium-term price direction |
| rsi | Momentum strength/saturation |
| volume_ratio_5d_20d | Sustained relative volume |
| volatility_20d_pct | Stock-level past volatility |
| positive_20d_pct | Contemporaneous fixed-universe breadth |

No future/target/evaluation-status input, Foreign/Institution, financial/news,
ticker/date/year ID, additional Score or duplicate Regime category is used.
Causal calculations do not certify the underlying historical data as PIT.

Target:

```text
raw_10d = 100 * (close[t+10] / close[t] - 1)
Z = raw_10d - same-date Fixed-574 Basket 10D Return
y = 1[Z > 0]  # zero is class 0
```

Basket is the equal-weight mean of the frozen Panel's evaluation-ready 10D
rows for that date, not the candidate-only basket and not official index
excess. Its future-path eligibility remains a retrospective selection-bias
limitation. Gross close-to-close returns are not executable trading P&L.

### Model, splits and selection

- Train-only StandardScaler and LogisticRegression:
  L2, C=1.0, lbfgs, tol=1e-6, max_iter=2000, class_weight=None.
- No tuning. Train sample weights are inverse labelled candidate count per
  date, normalized to overall mean 1; date totals are equal.
- 2024 Train; 2025 Validation; 2026 already-viewed retrospective diagnostic.
- Future unseen forward period is required for final AI superiority.
- Exit must be exactly ten frozen-calendar sessions after entry, no later
  than the available-through session and within the same entry year.
  Boundary/unmatured labels remain missing; past feature warm-up is allowed.
- No random row split or Validation/2026 scaler fitting/refitting.
- The first 2024-trained model remains frozen.
- Primary comparator: score_A; fixed secondary comparators: score_B/C.
- Same date/candidate pool/count, descending probability or Rule score,
  ticker ascending tie-break, no probability rounding.
- Select `ceil(0.20 * N)` equally weighted candidates; N <5 is
  INSUFFICIENT_CANDIDATES. Never replace missing selected labels.

### Metrics and approval criteria

The primary metric is exactly:

```text
delta_t = mean(Z of AI selections) - mean(Z of score_A selections)
primary = equal-date mean(delta_t), in percentage points (pp)
```

Only dates with complete AI and corresponding Rule selected labels are paired.
Secondary metrics are relative win-rate difference, median daily delta and
AI-B/C mean differences. Report all missing-label and excluded dates.
Bootstrap is paired full-calendar 20-session moving blocks, 2,000 repeats,
seed 37, 95% percentile CI. Also report separate-year results, calendar
halves, direction/volatility Regime cells and best-five-delta-date removal.
Regime cells below 30 paired dates are SPARSE.

| Gate | Frozen criterion |
|---|---|
| Validation coverage | >=200 paired dates |
| 2026 diagnostic coverage | >=150 paired dates |
| Mature pair availability | >=90% of mature decision dates with N >=5 |
| Selection label availability | >=95% per method on mature decision dates |
| Validation effect/uncertainty | delta >=+0.25pp and 95% CI lower bound >0 |
| Validation stability | Both halves >=0; best-five removal >0 |
| Diagnostic stability | Mean >0; both halves >=0 |
| Final unseen duration | 252 entry sessions plus 10-session label follow-up |
| Final unseen coverage | >=200 pairs; each BEAR/NEUTRAL/BULL >=30 pairs |
| Final unseen effect/stability | Same effect/CI/half/best-five gates |
| Final unseen additional checks | Each direction delta >=-0.25pp; AI-B/C means >=0 |

Adequate coverage with failed performance gates means this contract FAILS.
Insufficient coverage is INCONCLUSIVE / CONDITIONAL GO, not success.
Leakage or integrity failure is INVALID / STOP.
Only the future unseen test can support final research GO; Production
authorization is separate. Changing the contract after viewing 2026 makes
those revised results exploratory. These gates are not an infrastructure-stage
automatic AI superiority decision.

## STEP 38-A1: minimal Research dependency - GO

[requirements-research.txt](../requirements-research.txt) declares only
`scikit-learn==1.9.1`, separate from Production requirements.
Official [PyPI metadata](https://pypi.org/pypi/scikit-learn/1.9.1/json)
requires Python >=3.11 and NumPy >=1.24.1 and provides a non-yanked CPython
3.11 Windows x64 wheel. Dry-run found no core dependency replacement.

Installed in the existing ignored .venv:

| Package | Version |
|---|---|
| Python | 3.11.9, unchanged |
| NumPy | 2.4.6, unchanged |
| pandas | 3.0.5, unchanged |
| pytest | 9.1.1, unchanged |
| scikit-learn | 1.9.1 |
| SciPy | 1.17.1 |
| joblib | 1.6.0 |
| threadpoolctl | 3.7.0 |
| cloudpickle | 3.1.2, required by joblib |
| narwhals | 2.24.0, preexisting |

Imports and one minimal synthetic fit/predict_proba passed; pip check passed.
The `penalty="l2"` FutureWarning is recorded/re-emitted, not suppressed,
not a research failure, and not grounds to silently change the contract.

## STEP 38-A: infrastructure - GO

Implementation and usage:
[ai_ranking.py](../src/research/ai_ranking.py),
[tests](../tests/test_ai_ranking.py),
[API and limitations](../src/research/AI_RANKING.md).

Implemented frozen loader/provenance, candidate membership and joins,
allowlisted feature matrix, targets/purges, Train-only model pipeline,
rankings, paired metrics/coverage/bootstrap/stability/Regime diagnostics,
isolated new-only JSON output and protected-scope guard.
No automatic training, operational integration or retrospective-run CLI exists.

Read-only infrastructure probes verified the actual 385,728-row Panel,
672-date calendar and 14,446 unique candidates, including exact frozen
input/source/universe hashes and recalculated Regime/Basket consistency.
They did not call fit, predict, evaluate or write real output.

Focused synthetic/unit and existing behavior regression suite: **140 PASS**
(45 new ranking tests, 95 existing tests). This is not a full repository suite.

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_ai_ranking.py tests\test_fixed574_research.py src\research\test_regime_audit.py tests\test_signal_engine.py tests\test_indicators.py tests\test_expanded_shadow_signal.py -q -p no:cacheprovider
```

Coverage includes duplicate/join rejection, strict Feature allowlist,
Train-only scaler, target-boundary/maturity purge, prefix/future perturbation,
date weights, Top20%, ticker ties, missing-label nonreplacement, delta sign,
bootstrap reproducibility, provenance serialization, overwrite rejection,
artifact hash checks and operational scope guard.

## Closeout preservation and next starting point

Only these five files are in the closeout commit:

- `src/research/ai_ranking.py`
- `tests/test_ai_ranking.py`
- `src/research/AI_RANKING.md`
- `requirements-research.txt`
- `docs/STEP_38_A_RESEARCH_COMPLETION.md`

The closeout reruns the focused 140-test suite and checks STEP35C's nine
artifact hashes against the retained 35D manifest, tracked-file scope,
operational file hashes and Git whitespace before committing.
STEP38-A already verified 14,185 preexisting protected files unchanged.
No Production/v0.2, Engine, threshold, scheduler, dashboard, existing
Research artifact or data modification is authorized.

**Next: STEP 38-B - V03-LR-RANK-10D-v1 Retrospective Run.**
This closeout does not execute STEP38-B. Future execution requires explicit
approval and must keep 2026 diagnostic separate from future unseen validation.
