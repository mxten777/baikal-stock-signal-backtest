# STEP 15-F: isolated three-disclosure LLM runner

## Status

Prepared and tested offline. **No OpenAI API request has been made.**
This directory is separate from operational code, databases and the unchanged
STEP 15-C candidate answers. No human review or accuracy certification is implied.

## Files

- [runner.py](runner.py): preview by default; fixed source, model, endpoint and budget.
- [test_runner.py](test_runner.py): 33 offline test cases, all passed on 2026-10-21.
- [requirements.txt](requirements.txt): isolated tokenizer dependency.
- Local preview and preservation reports are intentionally not published.
- `.deps/` and `tokenizer_cache/`: ignored, local dependencies and public tokenizer data.

Only source, tests, documentation and ignore rules are committed. Actual disclosure
HTML, candidate answers, rule results, databases and execution reports remain
local-only. All source paths are repository-relative; no machine-specific absolute
paths or real credentials are included. Test credentials are explicit fake values.

The recorded **33 passed** result was obtained during STEP 15-F on the originating
machine with the authorized local archives and tokenizer present. A clean checkout
does not include those inputs or dependencies and cannot reproduce that test run
until they are provisioned separately. Missing inputs intentionally block execution;
the runner does not collect documents or silently download tokenizer data.

The experiment reuses existing lxml. tiktoken and its dependencies were installed
only into this directory's `.deps`, not the project's virtual environment.
The public o200k tokenizer asset was downloaded once during preparation, not via
an inference API. Runtime requires that local asset with SHA-256:
`446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`.
Missing/corrupt assets fail without automatic download. The local encoding was
tested against the pinned tiktoken reference pattern.

## Offline use

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B evaluation\step15f_llm\runner.py
.\.venv\Scripts\python.exe -B -m pytest evaluation\step15f_llm\test_runner.py -q -p no:cacheprovider
```

Default preview does not read credentials, make network requests or create an
attempt journal. `--save-dry-run-report` creates `dry_run.json` exclusively; it
does not overwrite the existing report. Test temporary journals are isolated.
The VS Code test tool did not discover this new standalone test file; the targeted
pytest command above was used instead. Final result: **33 passed**.

## Fixed input and output

Only these files under `evaluation\step15c_pilot` can be read as disclosure input:

| Receipt | Source SHA-256 | Local serialized-request tokens |
|---|---|---:|
| 20261020800221 | af513e580101656bca123d3ff4d7fb63ff864d9a2b52d9cda9ecf3fe05bad298 | 3,464 |
| 20261020800241 | f96256c4cdc994bc0e5412943e13261b49da7e9d34f8c26c5334d21886a3905c | 3,493 |
| 20261020800720 | 6b510c784741c706e077358b09412ef71269bc39df7a0a75cdf2018a3e6bfbe0 | 3,802 |

The runner reads no operational DB, source packet, candidate answer, comparison
result, prior conversation, arbitrary file path or entire directory. It builds
requests from fixed instructions, a generic schema, the receipt and exact
hash-verified HTML decoded using its declared encoding. Tools and external
retrieval are disabled. Source content is explicitly untrusted data.
Output fields contain status, nullable value/unit, meaning, exact source quote,
1-based table/own-row and 0-based cell. Successful output also records resolved
XPath and source hash. All confirmed facts require an existing quote. This is a
structural evidence check, **not semantic correctness or completeness approval**.
Unknown values must remain null.

Model: `gpt-4.1-mini-2025-04-14`.
Endpoint: `https://api.openai.com/v1/responses`.
Payload: `store=false`, `tools=[]`, `tool_choice=none`, strict JSON Schema,
`max_output_tokens=3000`, no previous response ID.

## Token and cost controls

- Count the entire serialized request locally with o200k before any request.
- Also require UTF-8 request byte length plus 1,024 framing allowance <= 20,000.
- Reserve a full **20,000 input + 3,000 output** tokens per document.
- Fixed uncached standard prices: input $0.40 / 1M, output $1.60 / 1M.
- Per-document reservation: **$0.0128**; three-document reservation: **$0.0384**.
- Total approved ceiling: **$0.04**, excluding taxes/currency conversion.
- Local request-token count: **10,759** total.
- If those local counts matched billed input and output used all 9,000 tokens,
  indicative total: **$0.0187036**, not a guaranteed bill.

The API's private message/schema framing is not identical to serialized JSON.
The 1,024 allowance is an engineering allowance, not an official server guarantee.
The implementation reserves far more input than the measured local counts,
checks reported usage afterwards, and stops on any reservation overrun. There is
no API parameter that imposes a hard dollar cap. If an absolute provider-enforced
monetary guarantee is required, execution must remain blocked until that control
is separately available/approved.

Sources checked during STEP 15-E:

- [Official model and snapshot](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [Standard pricing](https://developers.openai.com/api/docs/pricing)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [API data controls](https://developers.openai.com/api/docs/guides/your-data)

Reconfirm price and account model access before later execution. Cached discounts
are never required for the reservation to fit.

## Execution guard and audit

**No real execution is approved by this source-only commit.**
Actual execution later requires all of:

1. Explicit external public-data transfer approval.
2. Exact budget approval via `--approve-usd 0.04`.
3. `--execute --approve-public-transfer`.
4. `OPENAI_API_KEY` already injected into the execution process by the user/server.

No dotenv, secret files, browser credentials, alternate provider, proxy environment,
base-URL override or SDK credentials are read. The key is read only in execute mode
and is never included in printed or persisted results. Never paste keys into chat.

Before HTTP, the canonical request is reconstructed and compared; arbitrary
payload edits fail. A fixed `attempts.sqlite3` inside this experiment uses
`BEGIN IMMEDIATE` and a unique receipt key to reserve a slot and its full cost
atomically. This coordinates repeated/concurrent invocations. At most one slot
per document and three total; failed/time-out/interrupted reservations are never
released. There is no reset, alternate journal, resume or retry option.
Preserve this journal permanently: deleting/replacing it defeats the cross-run
quota, as with any local journal. Filesystem-administrator tampering is out of scope.
An interrupted batch is intentionally fail-closed; a later invocation encounters
the reserved first receipt rather than silently resuming or retrying.

HTTP uses a fixed HTTPS endpoint, TLS verification, no environment proxies, zero
adapter retries, no redirects, connect/read timeouts and a response size limit.
No tool outputs, polling, follow-up API requests or automatic repair requests.
Stop the batch after the first error, refusal, incomplete output, malformed JSON,
bad evidence, model mismatch or excessive usage.

The journal records status, safe local error code, HTTP status, request ID, usage,
cost when known, request/source hashes and validated output/evidence. It does not
log authorization headers, remote error bodies or exception text. Missing usage
remains unknown and the full reservation remains consumed. A hard crash can leave
RESERVED without a response record; this must never be treated as not attempted.

`store=false` does not mean zero provider retention. Review the official abuse
monitoring retention and account data-control policy before public-data transfer.

## Verification scope

Offline tests cover default no-network/no-key behavior, fixed request inputs,
tokenizer parity, corrupt sources, missing tokenizer, token and cost rejection,
approval/key gates, payload tampering, concurrent duplicate reservations, three
permanent slots, journal corruption, HTTP errors/redirects/quota errors, timeout,
secret-safe errors, strict schema, refusals/incomplete/tool outputs, evidence
mismatch, excessive usage and stopping after the first failed document.

No successful real API authentication, model availability, actual billing,
server schema acceptance or model output accuracy is claimed. These remain
unverified until a separately approved real experiment.
