# BAIKAL Stock Signal — 2026-10-03 Development Summary

## 1. Final Baseline

Verified implementation baseline before this documentation-only commit:

- Branch: `main`.
- HEAD: `a8bc607`.
- `origin/main` synchronized with HEAD.
- Working tree clean.
- Official Python regression: **1243 passed / 0 failed / 14 warnings**.
- Warnings concern existing PyPDF2 deprecation and Korean PDF text extraction encoding support.
- Profile snapshot SHA256 unchanged:
  `AEC9ED5012BF047A8484C03A82B084988FB33445F91986B6123B4A98492BFC63`.

This record adds a documentation commit on top of `a8bc607`; the implementation
baseline above is not the eventual documentation commit's HEAD.

## 2. M3-F Scheduled Naver Transition

- Explicit CLI context: `--run-context scheduled`.
- Manual/no-context and global function defaults remain `legacy`.
- Scheduled CLI context defaults to `naver`.
- An explicit `--benchmark-provider` value takes precedence over context.
- Code transition completed in `94812f8`.
- Current Windows Task `BAIKAL Stock Expanded Scheduler` action verified:
  - Executable: `C:\baikal777\baikal-stock-signal-backtest\.venv\Scripts\python.exe`.
  - Arguments: `-m scripts.expanded_operational_run --json --run-context scheduled`.
  - Working directory: `C:\baikal777\baikal-stock-signal-backtest`.
- Main **23:10** / Safety **23:40** triggers remain enabled; current task state is `Ready`.
- Code/Task transition is complete. No other Task settings were changed as part
  of the reported transition; this closing step only inspected Task state.
- Actual trading-day scheduled live validation is **PENDING**. Current action
  configuration is not evidence of a successful scheduled live run.

Code reference: [expanded_operational_run.py](../scripts/expanded_operational_run.py).

## 3. STEP 20 Company Profile / Candidate Improvement

The company profile contract now has:

- `market_cap`: positive JS-safe integer in KRW, or null.
- `market_cap_date`: valid `YYYY-MM-DD`, or null.
- `market_cap_source`: source identifier, or null.
- `market_cap_status`: `AVAILABLE`, `UNAVAILABLE`, or `ERROR`.
- `market_cap_error`: error code, or null.

`AVAILABLE` requires a valid value, date, and source. `UNAVAILABLE` and `ERROR`
require null value/date; `ERROR` requires an error code. Invalid values are
rejected rather than coerced.

Backward compatibility is maintained: legacy JSON without the new fields loads
as `UNAVAILABLE`, preserving existing company information. All 574 existing
profiles were verified to load without changing the stored snapshot.

This is an explanation layer fully separated from Signal Engine decisions.
Industry is available for 574/574 profiles, main business for 570/574, and
generated descriptions for 574/574. Generated descriptions are not official
business-report narratives.

Code reference: [expanded_company_profiles.py](../src/expanded_company_profiles.py).

## 4. Candidate UI Improvement

The Candidate list now exposes:

- Industry.
- Main business, shortened to 60 characters when needed.
- Selection-evidence summary using existing API evidence only.

`AVAILABLE` can show the existing previous/current score, threshold-crossing
reason, and foreign status. `PARTIAL` and `UNAVAILABLE` clearly show their status
without asserting a confirmed crossing. Missing evidence does not create a new
score, threshold, or decision.

Null market cap displays **"확인 보류"**. No artificial value or zero is substituted.
Existing details, full business text, decision evidence, and performance remain.

Validation: Company Profile focused **45 passed**, UI focused **13 passed**,
frontend full **56 passed**, TypeScript and frontend build **PASS**.

## 5. Daily Report DOCX/PDF Improvement

- Each new Candidate has a compact industry/main-business/selection-summary block
  below the existing summary table.
- DOCX and PDF use shared presentation rules in
  [expanded_display.py](../dashboard/expanded_display.py).
- Existing Expanded Signal Details and full business descriptions remain.
- Null market cap displays **"확인 보류"** in both formats.
- Matching uses existing ticker, signal date, and stored `CANDIDATE` decision;
  unmatched evidence is displayed as unavailable.
- DOCX selection-summary rows have `w:cantSplit`, equivalent to disabling
  "Allow row to break across pages", only on those rows.
- No whole-table pagination, font, margin, or evidence logic change was needed.

Actual fixture DOCX/PDF files were generated outside the repository. Word
rendering initially exposed a split selection-summary row. After the focused
fix, all three evidence-state examples had their selection-summary row start
and end on the same page with `AllowBreakAcrossPages=0`. The Word-rendered
document remained four pages; the direct PDF was three pages. Korean rendering,
summary content, and existing details were checked.

Focused report tests: **28 passed**. Final official Python regression:
**1243 passed / 0 failed / 14 warnings**.

## 6. Market Cap Investigation

- Direct KRX `MDCSTAT01501` probe explicitly requested approved date `2026-10-02`.
- Request and same-date form-encoding retry returned **HTTP 400 / LOGOUT**.
  The direct path is on hold; no different date was substituted.
- Installed FinanceDataReader **0.9.202** `StockListing("KRX-MARCAP")` probe
  succeeded once.
- Returned rows: **2,873**.
- In-memory Expanded join: **574/574 matched / missing 0 / duplicate 0**.
- `Code` was string-typed and six-character formatted; `Marcap` was `int64`.
- No returned Date column or as-of metadata proved `2026-10-02`.
- The cache response did not provide explicit unit metadata sufficient to
  confirm the required KRW contract. Date/unit verification therefore failed.
- No market-cap values were applied to the profile snapshot; all 574 remain null.
- Real-data population remains on hold until date and unit are verified.

Successful retrieval and ticker coverage alone do not authorize snapshot updates.

## 7. Scope Guard

The work did not change:

- Signal Engine.
- Score / threshold.
- Universe 574 membership or its controlled CSV.
- Foreign-flow classification.
- `CANDIDATE` / `EXCLUDED` rules.
- Benchmark calculation logic; M3-F changes provider selection context only.
- Production 20 / DUAL.
- Operational snapshots.

STEP 20 did not change API/evidence builder behavior or operational Tasks.
The earlier M3-F Task action transition is recorded separately in section 2.
This closing step changes documentation only.

## 8. Git History

Major development commits on 2026-10-03:

| Commit | Subject / scope |
|---|---|
| `94812f8` | `feat: add scheduled benchmark context` |
| `d605196` | `feat: enhance expanded candidate profiles` — contract and Candidate UI |
| `a8bc607` | `feat: improve expanded candidate report summaries` — DOCX/PDF summary and row pagination |

Four initial full-suite failures before `d605196` were unstaged-diff repository
guards, not failed behavioral assertions. After the authorized local commit,
the unchanged full suite passed **1232 tests** before push. Report improvements
were likewise locally committed first, then validated with **1243 passing tests**
before normal push. No force push, rebase, or amend was used.

## 9. Pending Items

1. Validate M3-F scheduled Naver automatic execution on the next actual trading day.
2. Do not populate market-cap real data until as-of date and unit are verified.
3. In STEP 21, evaluate actual Candidate screen/report usefulness.
4. Implement follow-up features only when a demonstrated need is confirmed.

## 10. Next Starting Point

- Implementation starting baseline: **main / `a8bc607`**.
- The next checkout should include the documentation-only commit that records
  this summary and confirm HEAD matches `origin/main` with a clean working tree.
- **STEP 21 — Candidate 결과물 활용성 평가**.
- Evaluate actual outputs before developing additional functionality.
- Today's development ends after the documentation commit/push and final clean
  status verification. Scheduled live validation remains pending.
