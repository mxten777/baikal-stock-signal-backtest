# BAIKAL Stock Signal — M3-F Actual Trading-Day Validation (2026-10-06)

This record closes pending item 1 of
[DEVELOPMENT_SUMMARY_2026-10-03.md](./DEVELOPMENT_SUMMARY_2026-10-03.md):
validation of M3-F scheduled Naver automatic execution on an actual trading day.
It records observed facts only. This step changes documentation only.

## 1. Run Details

- Trade Date (basDd): `2026-10-06`.
- Observation Date: `2026-10-06`.
- Branch: `main`.
- HEAD / `origin/main`: `187e86fec03a91da9ad07e51ff95299750708860`.
- Working tree: clean.
- Run `source_commit`: `187e86fec03a91da9ad07e51ff95299750708860`.

## 2. Scheduler

- Windows Task Scheduler task: `BAIKAL Stock Expanded Scheduler` (Main).
- Main trigger fired automatically at **23:10:01**; no manual execution.
- `State=Running` was observed during execution.
- Action:
  - Executable: `python.exe` (project `.venv`).
  - Arguments: `-m scripts.expanded_operational_run --json --run-context scheduled`.
- Safety trigger executed automatically at **23:40**; `LastTaskResult = 0`.
- NextRunTime: `2026-10-07 23:10`.

## 3. Provider Selection

Verified in [expanded_operational_run.py](../scripts/expanded_operational_run.py):

- An explicit `--benchmark-provider` takes precedence.
- `run_context == scheduled` selects `PROVIDER_NAVER`.
- Any other context (manual/default) selects `PROVIDER_LEGACY`.
- The selected `benchmark_provider` is passed to the actual operational run.

## 4. Expanded Run Result

| Item | Value |
|---|---|
| basDd | `2026-10-06` |
| Universe | 574 |
| Attempted | 574 |
| READY | 574 |
| Market success | 574 |
| Investor success | 574 |
| signal_count | 42 |
| Quarantine | 0 |
| System failures | 0 |
| Status | **SUCCESS** |
| source_commit | `187e86fec03a91da9ad07e51ff95299750708860` |

## 5. Runtime Artifacts

Generated normally:

- `expanded_shadow_signal_ledger`.
- Manifest.
- Run registry.
- `latest`.
- `expanded_shadow_run.json`.
- `expanded_candidate_performance`.

## 6. Judgment

- Scheduled automatic execution: PASS.
- Scheduled context → Naver provider selection: PASS.
- Expanded run completeness (574/574, quarantine 0, system failures 0): PASS.
- Artifact generation: PASS.
- Safety task: PASS.
- **Final judgment: M3-F PASS / COMPLETE.**

## 7. Separate Operational Observation (Not an M3-F Failure)

- The **2026-10-06 18:30 Daily** run was blocked at `INPUT_GATE` with
  `MARKET_INVESTOR_MISALIGNED` (investor `2026-10-02` / market `2026-10-06`).
- At the **22:00 Probe**, market and investor data were both `2026-10-06`
  with `match=true`.
- This concerns the Daily pipeline input timing, not the Expanded scheduled
  path. It is tracked as a separate operational issue and does not affect the
  M3-F judgment. No fix was applied in this step.

## 8. Scope Guard

This step did not change code, Signal Engine, Scheduler, Task settings,
thresholds, Universe, or provider logic.

## 9. Follow-up

1. Track the Daily 18:30 `MARKET_INVESTOR_MISALIGNED` input-gate block as a
   separate operational issue (observe recurrence before any change).
2. Continue routine observation of the Expanded Main 23:10 / Safety 23:40 runs.
