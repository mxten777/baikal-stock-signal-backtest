# STEP 41 - AI ranking efficacy and personal-investing decision

Run date: 2026-10-09. Research inputs are the frozen STEP 35 snapshot through
2026-10-07 and the unchanged `V03-LR-RANK-10D-v1` contract. No data was
collected, no model was tuned, and no Expanded 574 operating behavior changed.

## Existing implementation and execution

STEP 38-A supplied the isolated ranking infrastructure and synthetic tests, but
explicitly did not run the real model. STEP 41 ran that frozen experiment:

- 14,446 non-overheated A/B/C crossing-union candidates.
- Train: 2024 only; 3,680 mature/purged labelled candidate rows over 173 dates.
- Validation: 2025; 2026 is a retrospective diagnostic, not an unseen test.
- Train-only scaler and fixed L2 Logistic Regression; no validation refit or
  tuning. Features, 10-session target, purges, Top-20% selection and 2,000
  paired 20-session block-bootstrap repetitions follow STEP 37.
- The model warning about the frozen `penalty="l2"` parameter was emitted and
  recorded as specified by the contract.

## AI ranking results

Primary effect is the equal-date mean of (AI selected mean return relative to
the Fixed-574 basket minus Strategy A score-selected mean), in percentage
points. Intervals are paired 95% moving-block bootstrap intervals.

| Period | Paired dates | AI minus A | 95% CI | First / second half | After best-five removal | Pair availability |
|---|---:|---:|---:|---:|---:|---:|
| 2025 Validation | 228 | +0.115 pp | [-1.302, +1.545] pp | +0.867 / -0.692 pp | -0.365 pp | 100.0% |
| 2026 retrospective diagnostic | 159 | -2.661 pp | [-5.398, +0.224] pp | -4.819 / -0.052 pp | -3.431 pp | 97.0% |

Mature selected-label availability was 100.0% in 2025. In 2026 it was
99.88% for AI; pair availability was 97.0%. The final immature/boundary
selections were not substituted or scored.

The 2025 result fails the frozen Validation gates: effect is below +0.25 pp,
the confidence interval includes zero, calendar halves disagree, and the
best-five-removed effect is negative. The retrospective 2026 effect is
negative and does not meet the diagnostic stability gate. The AI-ranking
contract therefore does not support AI superiority.

## Investment simulation requested in STEP 41

The requested 10,000,000 KRW cash-account simulation cannot be reproduced
from the existing STEP 35/38 assets without adding an unapproved simulation:

- AI Ranking is contractually a 10-session ranking test; it is not a
  20-session portfolio simulator.
- Existing 20-session Strategy A results are per-signal gross close-to-close
  event statistics, not a daily cash-constrained portfolio. They do not model
  entry execution, concurrent positions, available-cash allocation, or
  transaction costs.
- The saved fixed-universe inputs do not contain a frozen KOSPI index series.
  The existing benchmark provider fetches an external series; fetching new
  data was outside STEP 41's allowed scope.
- Consequently, comparable net return, portfolio excess return, account MDD,
  and annual account performance for AI/Strategy A/KOSPI are unavailable.

For context only, the already-computed STEP 35 gross event-level Strategy A
20-session mean was +4.826% (date-weighted +3.916%). Annual event-level means
were 2024 +0.224%, 2025 +5.219%, and 2026 +8.194%. These are not account
returns and must not be interpreted as net investable performance.

## Decision

**NO-GO for personal investment use of AI ranking at this stage.** The
predeclared Validation superiority gates failed, and there is no comparable
costed, cash-constrained 20-session account simulation or verified KOSPI
benchmark result. The result is research-only; do not use it as evidence of
expected personal-investment returns.

Independence is limited by the frozen 2026-selected universe
(survivorship/selection bias), unverified historical point-in-time price and
adjustment status, retrospective inspection of 2026, and the Fixed-574
future-path-eligible basket. The 10D ranking result is reproducible under
those inputs, but is not an independent market-wide or executable validation.

Machine-readable results and provenance are in
`../output/research/V03-LR-RANK-10D-v1/`. No preexisting Research artifact or
operational file was modified.
