# BAIKAL Stock Signal Backtest

## 목적
BAIKAL Signal Score v0.1의 유효성을 과거 3년 주가 데이터로 검증한다.
투자 권유 목적이 아닌 알고리즘 검증용 프로젝트다.

## 설치
```bash
pip install -r requirements.txt
```

## 데이터 위치
`data/raw/` 폴더에 종목별 CSV 파일을 저장한다.

파일명 형식: `{ticker}.csv`
예: `005930.csv`, `000660.csv`, `080220.csv`

필수 컬럼: `date, open, high, low, close, volume`

## 실행
```bash
python -m src.main
```

## Score 구조
| 구분 | 최대점 | 내용 |
|------|--------|------|
| A. Trend | 25점 | MA5/MA20/MA60 정배열 |
| B. Volume | 20점 | 거래량 급증 |
| C. Momentum | 20점 | RSI, MACD, 5일수익률 |
| **합계** | **65점** | → 100점 환산 |

## Signal 판정
| 점수 | 판정 |
|------|------|
| 0~49 | RISK |
| 50~64 | WATCH |
| 65~74 | WAIT |
| 75~84 | BUY_WATCH |
| 85~100 | STRONG_WATCH |

## 결과 파일
- `output/signals.csv` — 전체 Signal 상세
- `output/summary.csv` — summary

## DART disclosure collection (STEP 6-A, manual only)

```powershell
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_collect
# Optional: --end-date 2026-10-20 --lookback-days 30
```

- Reuses the existing `DART_API_KEY` environment variable and OpenDartReader's
  corporate-code downloader. No credentials, authenticated URLs, remote error
  messages or exception bodies are printed or saved. No alternate authentication
  or cached-data bypass is attempted when the key is missing.
- Reads the validated 574-company controlled universe without modifying it.
  Exact `stock_code` matches are required; names are diagnostic only (renames
  do not change identity). Missing mappings are `UNMAPPED`; ambiguous or invalid
  mappings are `ERROR`. A missing/unavailable corporate-code source leaves all
  mappings unverified, not falsely unmapped.
- Collects five KOSPI stocks (005930, 000660, 005380, 035420, 068270) and five
  KOSDAQ stocks (196170, 086520, 247540, 036930, 277810). The default window is
  the last 30 calendar days, including the current Korean date; at most 31 days
  can be requested.
- Maximum: one corporate-code download plus three pages per company, at most
  31 API requests, with no retries. The reused corporate-code downloader retains
  OpenDartReader's network behavior (no explicit timeout); disclosure requests
  have connect/read timeouts. The reader constructor is deliberately not used
  because it deletes shared cache files.
- A small direct OpenDART list adapter is necessary: the existing financial
  Provider suppresses list failures and OpenDartReader's list method can return
  an empty frame on API errors. Only API status `013` yields `NO_DATA`.
  HTTP, JSON, other API statuses, malformed records, changing pagination and
  page-limit overruns yield `ERROR`, never empty-success results. A failed
  multi-page collection publishes no partial records for that company.
- Each disclosure records receipt number, stock/corporate code, title, date,
  original DART viewer URL and correction tag. Receipt time is `null` with
  `NOT_PROVIDED_BY_LIST_API`: the list endpoint does not provide it. Time is
  never inferred from the receipt number or collection timestamp; no additional
  scraping is performed. Correction tags such as `[기재정정]` and `[첨부정정]`
  are identified; original-to-correction relationships are not guessed.
- Exact duplicate receipt numbers are removed within a run. Conflicting
  duplicates are errors; distinct original/corrected receipt numbers are both
  retained. Runs are separate immutable observations, not an incremental ledger.
- Writes a new JSON report under `output/dart_disclosures/` containing all 574
  mapping results, ten collection results, disclosures, source hash and counts.
  Nothing is written to Expanded data/output, and no signal, score, performance,
  dashboard or scheduler integration is added.
- Exit codes: `0` PASS (all mappings resolved; each sample SUCCESS or NO_DATA),
  `1` FAIL (mapping/collection verification failed), `2` BLOCKED (missing key,
  unavailable live service/dependency or local input/output failure).
  Missing-key reports explicitly retain 574 unverified mapping rows and ten
  `ERROR` rows, rather than claiming a mapping failure count or no disclosures.

Offline verification: `python -m pytest tests/test_dart_disclosures.py`.

### Full 574-company collection (STEP 6-B)

Run **only in the VS Code PowerShell terminal that already has `DART_API_KEY`**
set. No key assignment, discovery, or alternate credential loading is performed:

```powershell
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_collect --scope all
# Reproducible three-calendar-day window, inclusive:
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_collect --scope all --end-date 2026-10-20 --lookback-days 3
```

Execute one of these commands, not both. `--scope sample` (the default) preserves
STEP 6-A's ten-company/30-day behavior. `--scope all` defaults to the current
Korean date and the preceding two calendar days (weekends included); it accepts
only 1-3 days. Future end dates are rejected. A run ending today is an observation
as of collection time, not a promise that today's later filings are included.

| Method | API calls, including corporate-code download | Completeness risks |
|---|---|---|
| Per-company, 574 tickers | At least 575; up to 1,723 with the existing three-page cap | Many separate failure points; each company's pagination must finish |
| All-market list, then filter (selected) | `1 + total_page`, or 2 for an empty market; hard maximum 101 | Every page, including non-target/unlisted filers, must finish before any company is considered collected |

The existing list adapter requests an empty corporate code, all disclosure
types and `last_reprt_at=N`. It reuses the existing mapping, normalization,
correction detection and receipt-number deduplication. The all-market endpoint
includes non-target and unlisted companies; those records are validated before
filtering by the exact controlled stock/corporate-code pair.

- All 574 mappings must resolve before list collection. Ambiguous/missing
  mappings prevent PASS. No fallback to 574 separate requests or retries occurs.
- Page number, total pages/count, per-page row count, unchanged pagination
  metadata and repeated pages are checked. The 100-page limit is checked on the
  first response, before requesting remaining pages. HTTP/API failures, quota
  status `020`, bad records, conflicting receipts or an incomplete page invalidate
  the entire full-universe collection: no partial disclosures are published,
  all mapped companies become `ERROR`, and no company is mislabeled `NO_DATA`.
- `NO_DATA` means either an explicit empty-market `013` response or absence of a
  company's filings after **all** market pages have been validated. It does not
  require 574 individual empty responses.
- Exact repeated receipts are deduplicated. A repeated whole page fails rather
  than masking an omitted page. Distinct original/corrected receipts and their
  viewer URLs are preserved. The API offers no snapshot token: pagination
  changes that it exposes fail closed; a structurally valid response cannot
  prove that the remote service itself omitted nothing.
- The new JSON remains under `output/dart_disclosures/` and records 574 company
  statuses, the actual start/end dates, UTC start/collection timestamps, elapsed
  collection seconds, API requests, companies with disclosures, filing/correction/
  duplicate counts, error codes, `uncollected_count` and `collection_complete`.
  Source-market page/raw/unique/filter/dedup counts are recorded separately;
  headline disclosure/dedup/correction counts cover only the 574 targets.
  Elapsed time includes universe loading, mapping and collection, not JSON
  serialization. Source completion totals are `null` after a failed list scan.
- A PASS requires all mappings and the entire source scan to complete. With an
  unavailable source the run is BLOCKED; other validation failures are FAIL.
  Counts from an incomplete run are **not** evidence of no disclosures.
  Exit codes and the no-scheduler/no-signal/no-Expanded-write boundaries remain
  unchanged. Reports are new observations and never overwrite previous reports.

### Offline disclosure classification (STEP 6-C)

```powershell
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_classify --input output\dart_disclosures\20261020T044347_fa35ed73fa1e4f96a8417d1fbd58e6e6.json
```

- Reads only an existing complete, all-universe PASS report. No key loading,
  network, AI model, original-document retrieval, collector changes or pipeline
  integration. Source count/status/identity/date/link inconsistencies fail
  explicitly rather than being omitted or called REVIEW.
- Writes a new immutable JSON under `output/dart_disclosure_classification/`.
  The input bytes and all existing outputs stay unchanged. Reports record the
  source path/hash, classification time, rule version and full rule catalog.
- `HIGH / MEDIUM / LOW / REVIEW` are **information-review priorities only**, not
  recommendations, predictions, sentiment, materiality findings or impact scores.
  HIGH includes trading/safety notices, restructuring, earnings, contracts,
  financing and asset transactions; MEDIUM includes credit, shareholder returns,
  ownership, issuance documents and management changes; LOW includes scheduling
  and meeting notices. Broad management notices require REVIEW; all unmatched
  titles remain `UNCLASSIFIED / REVIEW`.
- Versioned, ordered regular-expression rules use the whitespace-normalized
  title. First match wins, with trading notices ahead of contracts appearing in
  subtitles and earnings announcements separated from announcement schedules.
  The matched text, rule ID and human-readable reason are stored for every row.
  The existing input has no detailed DART disclosure-type codes or original
  text: metadata verifies company identity and correction status, but categories
  are title-rule-based. No economic outcome is asserted from a title.
- All original disclosure fields (including raw title, receipt/date/time, URL
  and correction tag) and company name/code/market are preserved. Exact receipt
  duplicates are removed; conflicting duplicates fail. Corrections stay separate
  from importance. No original receipt is guessed, even for similarly named
  reports: `original_receipt_no=null`, `original_relationship=NOT_ESTABLISHED`
  for corrections. Every row has `original_reviewed=false` and
  `economic_impact=NOT_ASSESSED`.
- Report includes all classified disclosures, category/priority counts,
  correction/unresolved counts, unclassified count and up to ten rows per
  priority. Within a priority, date then receipt descending is a deterministic
  display order, **not** an economic-impact ranking. REVIEW does not mean low
  importance; it means human content review is necessary.
- Exit codes: `0` PASS; `1` explicit input/validation/output failure. The local
  168-record integration tests require the named source JSON to be present:
  `python -m pytest tests/test_dart_disclosure_classification.py`.

### Cumulative disclosure storage (STEP 9-B)

```powershell
# Offline: reuse an existing complete all-universe collection JSON.
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_accumulate --input output\dart_disclosures\20261020T044347_fa35ed73fa1e4f96a8417d1fbd58e6e6.json

# Alternatively, collect the last three Korean calendar days, classify and store.
# Requires the existing DART_API_KEY environment variable; makes live API calls.
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_accumulate
# Optional live date options: --end-date 2026-10-20 --lookback-days 1
```

- Reuses the existing full-universe collector and classification runner unchanged.
  `--input` skips collection/network access and cannot be combined with date options.
  Classification validates the complete all-universe collection PASS before storage;
  a failed collection/classification never updates cumulative data.
- Stores classified records in `output/dart_disclosure_store/disclosures.sqlite3`
  using the receipt number as the primary key. SQLite is part of Python's standard
  library; no new dependency or scheduler integration is required.
- Repeated receipts retain their first stored payload, source SHA-256, rule version
  and storage time. A conflicting receipt identity/title/date/correction flag fails
  the entire update rather than overwriting existing evidence. Changed classification
  rules or company display names do not automatically reclassify stored records.
  Distinct original/corrected receipts remain separate; no relationship is inferred.
- One transaction, serialized with `BEGIN IMMEDIATE`, inserts new receipts and
  updates the singleton `last_success` row together. SQL/write failures roll back
  both; retry the same command after resolving the error. Database lock waits are
  bounded to five seconds. Existing immutable collection/classification observations
  may still be saved when cumulative storage fails; they are not deleted or overwritten.
- JSON output includes `new_count`, `duplicate_count`, `total_count` and UTC
  `last_success_at`. Duplicate counts include repeats within the input as well as
  already-stored receipts. An empty complete input or duplicate-only success updates
  the last successful processing time, not the disclosure date or original analysis time.
  The same summary is persisted as JSON in `last_success.summary_json`.
- Exit codes: PASS `0`, validation/storage failure `1`, live collection BLOCKED `2`.
  This is manual cumulative storage only: no prediction, scheduler, original-document
  coverage, correction linking, dashboard or deployment changes.

Offline verification:
`python -m pytest tests/test_dart_disclosure_accumulation.py`.
The 168-record integration case uses the same persisted source as the existing
classification tests; all test database/report writes use temporary directories.

### Read-only company and HIGH disclosure queries (STEP 9-C)

```powershell
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_query --ticker 005930
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_query --ticker 000720 --limit 10
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_query --high --limit 20
```

- Opens the existing STEP 9-B SQLite store using `mode=ro` and `query_only`.
  No collection, classification, accumulation, schema migration, output file,
  scheduler, API or UI is triggered. A missing database fails without creating one.
  `--store` optionally selects another existing STEP 9-B database.
- Select exactly one of `--ticker` (six-character stock code, preserving leading
  zeros) or `--high` (HIGH filings across all stored companies). Results sort by
  disclosure date descending, then receipt number descending; the latter is only
  a deterministic tie-breaker, not an inferred receipt time. `--limit` defaults
  to 20 and must be positive. "Recent" means latest stored records, not a live
  date window.
- JSON includes company/code, receipt, disclosure date/title, importance,
  correction flag and official DART link. `query_count` is the returned count;
  `matching_count` is the count before the limit, and `total_count` covers the
  whole store. Zero matches only means no matching stored evidence.
- `last_success_at` is the STEP 9-B accumulation processing success time, **not**
  collection time. The query reads its referenced, already-stored collection JSON,
  verifies its SHA-256 and complete PASS status, then displays its `collected_at`
  as `last_collection_success_at` with source window and in-progress flag.
  It never accesses DART or treats query time as collection time.
- If that source file was removed or never recorded a collection timestamp,
  collection time stays null with an explicit `collection_time_status`; processing
  time is never substituted. Hash mismatch or corrupt evidence is an explicit
  failure. The timestamp describes the last successful input, not guaranteed
  full historical coverage or collection after that observation.
- Exit codes: success `0`, invalid input/store/evidence `1`; argparse usage errors
  `2`. Tests: `python -m pytest tests/test_dart_disclosure_query.py`.

### Isolated DART automatic-update preparation (STEP 9-D)

```powershell
# Safe preview only: no API calls, DB/output writes or task registration.
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_scheduler
# Print the proposed Windows task XML only; does not save or register it.
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_scheduler --task-xml
```

- The DART-only scheduler is prepared for **Monday-Friday 20:20 Asia/Seoul**,
  independent of the existing stock schedulers. Weekdays include public holidays;
  DART collection is not gated on stock-market trading days. No Windows task has
  been registered. Preview mode is the default; due live runs require `--execute`
  and separate operator approval.
- After approval, the proposed Windows CalendarTrigger uses an explicit `+09:00`
  boundary, weekday schedule, `IgnoreNew`, the dedicated PowerShell secure runner
  (which uses the repository virtualenv interpreter) and working directory.
  `StartWhenAvailable` permits late invocation: Python
  executes only on a weekday at/after 20:20 KST, once successfully per Korean date.
  It is a one-shot runner, not a resident timer; printing XML alone does not enable
  automation. No task account/password is embedded.
- A due execution reuses `run_collection(..., scope="all")`, then
  `run_accumulation` (which reuses classification and requires complete PASS).
  STEP 9-E expands an overdue run into consecutive 1-3-calendar-day windows.
  The existing receipt primary key and transaction protect against duplicate
  inserts and failed updates.
- A dedicated SQLite lock transaction under `output/dart_disclosure_scheduler/`
  serializes the whole operation, including collection. A concurrent invocation
  returns `ALREADY_RUNNING`. OS process exit releases the lock without manual stale
  lock deletion. Each attempt has an atomically saved RUNNING then PASS/FAIL/BLOCKED
  JSON report; interrupted attempts retain RUNNING evidence.
- Failures do not mark a date successful: the next due invocation retries collection.
  There is no immediate retry loop. Normally that invocation is the next scheduled
  weekday; manual approved reinvocation after 20:20 can retry on the same date.
  STEP 9-E includes weekends and holidays in catch-up windows instead of skipping
  them; a failed Friday run can be recovered by Monday's invocation.
- Final reports include stage/error codes, new/duplicate/total counts, the actual
  source collection timestamp and last accumulation success timestamp. Failed
  attempts report committed segment counts (zero if no segment committed) and
  retain prior known success/counts. Whole-run PASS requires all planned segments.
  After commit, the STEP 9-C read-only query verifies totals and source time.
  `accumulation_committed` distinguishes post-commit verification/report failures:
  SQLite evidence is retained and retry safely deduplicates it. If writing the
  final report itself fails, CLI returns failure; the RUNNING record and committed
  store may remain, never silently undone or labeled fully successful.
- Exit codes: PASS/skip/preview `0`, failure `1`, live collection BLOCKED `2`.
  The proposed two-hour task time limit can interrupt stalled upstream collection;
  completed segments remain committed, but the interrupted window is retried.
  The existing corporate-code downloader
  still has no explicit timeout; it is not redeveloped here.
- Before real operation approve task registration, run account with existing
  `DART_API_KEY` and directory permissions, first live execution/DB writes, and
  sleep/missed-run/retry/catch-up baseline policies. Do not assume a key available
  in an interactive VS Code terminal is available to a scheduled account.

Local-only tests:
`python -m pytest tests/test_dart_disclosure_scheduler.py`.
They substitute the collector, block network access and write only to temporary
directories. A persisted 574-company fixture plus a synthetic new receipt verifies
that the next STEP 9-C company query sees exactly the added record.
No task registration, operational execution, original-document expansion, correction
linking, stock-code/scheduler changes, UI, commits or pushes are part of this step.

#### Gap recovery and read-only preflight (STEP 9-E)

```powershell
# No API calls, key values, directory creation or write probes.
.\.venv\Scripts\python.exe -m scripts.dart_disclosure_scheduler --preflight
```

- On the first enabled run, the catch-up start is the earlier of the last
  accumulated complete source's end date and today minus two days. Without any
  store, the initial baseline is today minus two days. This is an explicit
  operational baseline, not proof that older history has been collected.
- `output/dart_disclosure_scheduler/coverage.json` preserves that baseline before
  the first request and advances only after a segment's collection, classification,
  accumulation and read-only verification succeed. The last successful end date
  is included again on retry/next run: an observation made during that date cannot
  certify later filings on the same date. At least the recent three days are
  rechecked, with receipt-key deduplication.
- Segments are consecutive, inclusive and at most three calendar days each,
  including weekends/public holidays. The first failed segment stops the run;
  later windows are neither requested nor marked successful. Earlier committed
  windows stay intact. The next enabled invocation resumes from the checkpoint
  with conservative overlap, not from today's rolling window alone.
- Checkpoint writes are atomic. A crash/write failure after SQLite commit but
  before checkpoint publication repeats the segment safely. Baseline-only state
  also survives a first collection failure followed by a long outage. Do not
  delete the checkpoint or use manual accumulation to claim continuous coverage.
  Existing historical gaps before the chosen baseline require separate approval.
- `--preflight` displays only key presence, current account, interpreter presence
  and read/write access hints for the existing store and nearest existing output
  parents. It does not expose the key, authenticate it, enumerate credentials,
  create directories or test writes. Windows `os.access` is not an ACL guarantee;
  the actual scheduled account and effective write permissions remain unverified
  until separately approved account-specific validation. Missing key/access hints
  must be resolved before activation.
- Operational sequence after approval: confirm the baseline and back up the
  existing SQLite/source evidence; validate the intended account's key environment
  and permissions; approve the first live catch-up; verify all segment PASS records
  and STEP 9-C company/HIGH queries; then separately approve/register the weekday
  task. A two-hour task deadline may require several invocations for a long outage;
  committed checkpoint progress survives each interruption.

#### User-scoped DPAPI secure runner (STEP 9-G-2)

Run setup **as `BAIKAL777\baika`** in an interactive PowerShell terminal with no
transcription/session recording. No key value belongs in these commands:

```powershell
# Reuses this terminal's DART_API_KEY if present; otherwise prompts with hidden input.
powershell.exe -NoLogo -NoProfile -File .\scripts\dart_secure_runner.ps1 -Mode Setup
# No API calls: decrypt and verify only that child Python received a nonempty key.
powershell.exe -NoLogo -NoProfile -NonInteractive -File .\scripts\dart_secure_runner.ps1 -Mode Verify
# Preview only; does not collect or update data.
powershell.exe -NoLogo -NoProfile -NonInteractive -File .\scripts\dart_secure_runner.ps1 -Mode Preview
```

- Defaults to Verify, never live collection. Only explicitly approved `-Mode Execute`
  calls the DART scheduler with `--execute`; it retains the scheduler's weekday/time
  gates. Setup/Verify run only a fixed Python presence check, never DART requests.
- The ciphertext is `%LOCALAPPDATA%\BAIKAL\Dart\dart_api_key.dpapi`, outside Git.
  `ConvertFrom-SecureString` without `-Key` uses Windows current-user DPAPI.
  Setup refuses to overwrite an existing file. It creates a private leaf directory
  and file with inheritance disabled and only the current user's FullControl grant.
  Existing unsafe ACLs, wrong ownership, repository-local locations and reparse-point
  paths fail closed rather than being silently accepted or repaired.
- Verify/Preview/Execute require the existing private directory/file ACLs before
  decryption. The wrapper passes the key through `ProcessStartInfo` child environment,
  not command-line arguments, XML, persistent variables or plaintext files. It never
  changes the parent's environment. Unmanaged BSTR memory is zeroed and references
  cleared on exit; managed plaintext strings/environment must briefly exist in memory
  for Python's existing API. This is not protection from administrators or processes
  already running as the same user.
- Setup includes a DPAPI round trip and child presence check. Failures print only
  local stage codes, exception type/line and exit status, never exception bodies or
  ciphertext/key values. Existing inherited secrets in the invoking terminal are
  not deleted by the child wrapper; remove them there after successful setup if needed.
- `-SecretDirectory` supports a different private directory outside the repository
  (also used by dummy-value tests); scheduled use defaults to the standard location.
  The task XML proposal now calls the secure runner, not Python directly. It still
  does not register a task or embed a credential. Choose the same user's password
  logon or interactive session with the profile available; do not assume S4U can
  access user DPAPI material. Task account/password and unattended profile behavior
  require separate approval/validation.
- If Copilot cannot receive secret input and has no existing process key, real
  setup remains pending: run Setup yourself in the intended account's terminal.
  Never send a real key in chat or a script argument. Synthetic-value tests use
  private temporary directories and do not configure the real secret or call DART.

Windows integration tests:
`python -m pytest tests/test_dart_secure_runner.py tests/test_dart_disclosure_scheduler.py`.

### Official-document extraction foundation (STEP 6-D-1)

```powershell
.\.venv\Scripts\python.exe -m scripts.dart_document_analyze --input output\dart_disclosure_classification\20261020T044712_db81de1c8684428c997682585a0e3d59.json
```

- This is a **live public DART request**, not an offline classification command.
  It does not read credentials. Only five receipt IDs are allowlisted: S-Oil
  contract `20261020800241`, LS Eco Energy earnings `20261020800175`,
  EcoPro BM subscription `20261019900366`, Hana Financial corrected bond
  issuance `20261019000037`, and Hyundai Wia business transfer
  `20261019800137`. Selection requires matching HIGH/category metadata.
- Uses existing collector/classifier metadata and the full-document `viewDoc`
  entry used by OpenDartReader, without executing JavaScript. The entry title
  must match company, report title and disclosure date, and its document link
  must match the selected receipt. Only official HTTPS viewer URLs are built;
  redirects, attachments, prior receipts and unrelated documents are not followed.
  There are no retries: at most two requests per selected receipt, ten per run.
  Connection/read timeouts are 5/20 seconds; responses over 5 MB are rejected
  after download. Unsupported page structures fail explicitly.
- Reuses existing `requests`/`lxml` dependencies; EUC-KR exchange filings and UTF-8
  DART documents are parsed according to declared encoding. Tests use synthetic
  HTML fixtures and persisted real evidence, never live requests.
- Named fields cover contract amount/ratio/counterparty/period/payment terms;
  earnings period/current revenue, operating income and net income (with explicit
  source units and table context); subscription type/method/counts/ratio; bond
  principal/use of funds/rate/dates/call terms; business transfer
  amount/counterparty/date/payment conditions. Numeric values are decimal strings
  in the disclosed unit, not estimated, converted or interpreted as realized
  results. `VERIFIED` means the disclosed cell was verified, not that forecasts,
  conditional contracts or prospective dates have been realized.
- Each field retains raw values, source table cells/XPath, official URL, content
  SHA-256 and UTC access timestamp. Missing, ambiguous or `-` values remain
  `UNCONFIRMED`; no numeric zero or fallback is substituted. Only named table
  fields are extracted: not all footnotes, legal conditions or economic context.
  `original_accessed=true` means HTML retrieval/parsing succeeded;
  `original_reviewed=false` deliberately prevents claiming a complete
  substantive review.
- For corrections, explicit item/reason/before/after cells are recorded.
  This verifies the correction table, not an independently retrieved original.
  `original_receipt_no` remains null; dates/titles are not used to guess identity.
- Market linkage is local/read-only using ticker plus exact disclosure date.
  Existing Expanded snapshots are preferred, with existing Production CSVs also
  checked for the exact date. The most recent prior Expanded directory is
  diagnostic only; it is never used to fill a missing same-day row. Missing files,
  missing dates, schema/identity/value errors have separate statuses. A same-day
  row does not establish causality or intraday availability; receipt time remains
  unknown. No market/flow data is downloaded or modified.
- New observations are saved under `output/dart_document_analysis/`, separate
  from collection/classification and Expanded outputs. Previous attempts,
  including BLOCKED reports, are not overwritten. PASS requires five accessible
  documents with at least one verified field each, a verified correction table
  and no local linkage errors; unconfirmed optional fields and missing same-day
  data remain visible. Exit codes: PASS 0, FAIL 1, BLOCKED 2.
  No signals, scores, performance calculations, scheduling or investment
  recommendations are added.

Offline validation: `python -m pytest tests/test_dart_document_analysis.py`.
The real-evidence test requires the persisted local five-document report;
synthetic fixtures are explicitly test-only, never a substitute for real data.

#### Minimal extraction improvements (STEP 7-H)

- Earnings amounts recognize explicit `원`, `천원`, `백만원`, `억원` and `조원`
  declarations in the same table as each matched amount. Raw values and decimal
  strings remain in the disclosed unit; no currency conversion is performed.
  Unit declaration cells, XPath, URL, SHA-256 and access time are retained in
  `unit_evidence`. Missing, conflicting, foreign or unsupported declarations leave
  amounts `UNCONFIRMED` with no numeric value or inferred unit. Units from another
  table cannot confirm an amount.
- Existing `VERIFIED` / `UNCONFIRMED` statuses remain unchanged. Each extracted
  field and contract condition adds `verification_method=AUTO` or `NONE`.
  `AUTO` verifies a disclosed cell/condition, not realization or economic impact.
  The extractor never emits `MANUAL`; actual human review would require a separate
  review record and must not overwrite the automatic observation.
- Contracts add a separate `contract_conditions` list for explicit company-share,
  VAT and relative-period wording under section 9 footnotes. Narrow supported
  phrases retain the original clause and complete source row, including nearby
  qualifications such as demolition exclusions and changeability. Missing,
  contradictory, negative or unsupported wording remains `UNCONFIRMED`, with
  candidate evidence preserved. This is not general legal-language interpretation.
  Relative periods never populate undisclosed start/end dates.
- Existing `verified_field_count`, `unconfirmed_field_count`, PASS criteria and
  the five-receipt execution allowlist are unchanged. New conditions are counted
  separately in `contract_condition_counts`; extraction does not claim full
  original review. Existing JSON observations are never migrated or overwritten.
- Samsung's STEP 7-E observed cells are reconstructed offline to check 3/4
  original fields (revenue and operating income in 조원; net income undisclosed).
  Hyundai's observed cells check 4/6 original fields plus three separate contract
  conditions (100% company share, VAT excluded, 49 months after actual start with
  demolition excluded). These are **reproduction tests, not fresh original-page
  verification**. The fixtures use test evidence and do not claim new access times.
  Bio-event automation, collector changes and new external calls are out of scope.

Final validation record (STEP 7-H through 7-K, 2026-10-20):

- Related DART and signal-engine regression: **216 PASS**. Full repository
  regression: **1,661 PASS / 7 FAIL**, still unresolved: four Git change-policy
  conflicts and three existing report input/reference-date mismatches.
- Separate from the offline reproduction tests, STEP 7-J fetched two official
  DART originals with four HTTP requests (two entry pages and two bodies).
  Samsung `20261008800004`: **PASS**, 3/4 original fields verified.
  Hyundai `20260923800033`: **PASS**, 4/6 original fields retained and 3/3
  contract conditions verified. Values, units and source evidence matched;
  undisclosed values remained unconfirmed. AUTO extraction and subsequent
  source comparison were kept distinct; no MANUAL record was generated.
- **GO for the limited original-field extraction improvement only;
  NO-GO for full deployment or commercialization.** Two-document verification
  does not establish general coverage or resolve the seven regression failures.
  Existing JSON observations and core evidence hashes were preserved.

## Expanded verified trading-halt exclusions

- The controlled universe stays at 574. A ticker is excluded only when its
  published halt period and the provider's trading status for the requested
  source date both verify. Missing/stale prices never establish a halt.
- For Unison (018000), the [KOSCOM disclosure carried by
  Naver](https://m.stock.naver.com/api/stock/018000/disclosure/153834975)
  sets the suspension from 2026-10-15 until the day before new-share listing.
  The current status is checked for each source date: confirmed `HALTED` status
  within that disclosed period excludes Unison; confirmed active status returns
  it to normal preparation and analysis. An unavailable, ambiguous, or
  date-mismatched status blocks the run instead of excluding the ticker.
- Preparation/readiness JSON, daily results, immutable run manifests and
  registry events record `trading_halt_exclusions` with ticker, session, reason
  and evidence URL. This is separate from signal-level `excluded_count`.
  `canonical_universe_count` stays 574; `attempted_ticker_count` counts actual
  analysis attempts (573 while Unison is halted). A run with all non-halted
  tickers ready can succeed; excluded tickers receive no fabricated prices or
  signals.
- Existing snapshots are neither deleted nor rewritten for an excluded ticker.
  Recovery, when separately approved, must specify the intended source date;
  applying this code alone does not rerun or repair historical output.

## Expanded benchmark diagnostics (STEP 19-M3-D)

- The global benchmark provider default remains `legacy`; Naver is opt-in.
- When Naver preflight suppresses a fetch, performance diagnostics distinguish
  an existing Benchmark (`benchmark_already_filled`) from an unmatured Return
  (`benchmark_not_due`). Both counters are also exposed per horizon in
  `benchmark_status_by_horizon`.
- `NO_SOURCE` and `MATURED_RETURN_BENCHMARK_UNAVAILABLE` apply only when a
  matured Return needs a missing Benchmark and the source is absent or cannot
  supply the required return.
  `missing_benchmark` counts affected candidates once, not already-filled or
  not-due candidates. Provider failures retain their provider/source warnings.
- Existing metrics remain fill-only. Excess filling and mismatch checks still
  use the stored Benchmark when no fetch is required; supplied benchmark
  sources still undergo the existing mismatch checks. Calculations, maturity,
  fetch conditions, Signal Engine, and scheduling are unchanged.

## Expanded Forward Validation (STEP 31-C)

- Validation tracks only CANDIDATE signals whose `source_basDd >= 2026-10-08`.
- It writes to `output/expanded_shadow/expanded_validation_candidate_performance_ledger.csv`;
  the Discovery performance ledger and its schema are unchanged.
- Industry membership is snapshotted at Validation registration in
  `output/expanded_shadow/expanded_validation_sector_membership.csv`.
- Latest Validation execution status is recorded separately in
  `output/expanded_shadow/expanded_forward_validation_status.json`.
- The Expanded dashboard presents Validation separately from Discovery and
  reports 5D/10D/20D maturity, H1 Foreign, H2 score/Foreign, H3 industry, and
  H4 score-vs-5D-Excess Spearman correlation. An empty post-cutoff cohort is a
  normal state. Validation update errors are reported separately and do not
  change the existing Expanded/Daily operational result.

## Mobile Candidate First UI (STEP 32-B)

- At every screen width, Expanded shows the analysis summary, Candidates, then
  Forward Validation (STEP 32-E). Below 768px it uses mobile cards; wider screens
  retain desktop tables. Discovery summaries follow Validation on desktop.
  Section contents and Validation calculations are unchanged.
- The compact summary shows the source date, calendar-date freshness in Korea
  time, and Candidate count. Prior dates are not labeled as today's analysis;
  this is not a trading-calendar or real-time freshness check. Run counts and
  the stored completion timestamp remain in expandable supporting information.
- Cards show industry, Signal date/price (not the current quote), the stored
  Signal Score, evidence summary, performance status, and an OVERHEATED
  explanation when present. OPEN shows measurement in progress; matured cards
  show the latest available stored Return and Excess without recalculation.
- Company information, score components, Foreign details, and all 5D/10D/20D
  metrics remain expandable. Missing/partial evidence is still explicit.
- This is display-only: no API, engine, threshold, filter, universe, scheduler,
  ledger, data collection, or Discovery/Validation join changes.

## Fixed-574 OHLCV Research (STEP 35-C)

- Isolated offline research uses the existing `2026-10-07` Expanded OHLCV
  snapshot and unchanged 574-ticker universe; no external fetch or fitting.
- A/B/C rules, causal features, session targets, year splits, and deterministic
  reports are documented in [the Research guide](docs/FIXED574_OHLCV_RESEARCH.md).
- Outputs stay under `output/research/fixed574_2026-10-07/`; operational data,
  ledgers, Forward Validation, scheduler, API and dashboard are unchanged.
- This is survivorship-biased retrospective research, not prospective
  validation or executable trading performance.
