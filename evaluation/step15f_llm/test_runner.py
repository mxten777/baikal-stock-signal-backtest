import importlib.util
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location("step15f_runner", Path(__file__).with_name("runner.py"))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "JOURNAL", tmp_path / "attempts.sqlite3")
    monkeypatch.setattr(
        runner.requests.sessions.Session, "request",
        lambda *_a, **_k: pytest.fail("Live HTTP forbidden"),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def tokenizer():
    return runner.encoding()


def test_encoding_matches_pinned_reference(monkeypatch, tokenizer):
    from tiktoken_ext import openai_public

    monkeypatch.setattr(
        openai_public, "load_tiktoken_bpe",
        lambda *_a, **_k: tokenizer._mergeable_ranks,
    )
    reference = runner.tiktoken.Encoding(**openai_public.o200k_base())
    for receipt in runner.ALLOWED:
        text, _ = runner.source(receipt)
        assert reference.encode(text) == tokenizer.encode(text)


def test_dry_run_is_offline_and_does_not_read_key_or_create_journal(monkeypatch):
    monkeypatch.setattr(runner.os, "environ", {})
    result = runner.run()
    assert result["mode"] == "DRY_RUN"
    assert result["network_requests_this_run"] == 0
    assert len(result["plans"]) == 3
    assert result["max_reserved_usd"] == "0.0384"
    assert not runner.JOURNAL.exists()


def test_payload_allowlist_no_answers_or_rules(tokenizer):
    for receipt in runner.ALLOWED:
        plan = runner.plan(receipt, tokenizer)
        payload = plan["payload"]
        assert set(payload) == {
            "model", "store", "instructions", "input", "max_output_tokens",
            "tools", "tool_choice", "text",
        }
        text, _ = runner.source(receipt)
        assert payload["input"] == f"Receipt: {receipt}\nArchived disclosure HTML follows:\n{text}"
        assert payload["model"] == runner.MODEL
        assert payload["store"] is False
        assert payload["tools"] == [] and payload["tool_choice"] == "none"
        assert "previous_response_id" not in payload
        assert "answer_draft" not in json.dumps(payload)
        assert "comparison.json" not in json.dumps(payload)
        assert plan["serialized_payload_tokens"] < runner.INPUT_LIMIT


@pytest.mark.parametrize("receipt", ["20261020800242", "..\\answer_draft", ""])
def test_disallowed_receipt(receipt):
    with pytest.raises(runner.ExperimentError, match="RECEIPT_NOT_ALLOWED"):
        runner.source(receipt)


def test_corrupt_body_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "SOURCE", tmp_path)
    receipt = next(iter(runner.ALLOWED))
    (tmp_path / f"{receipt}.html").write_bytes(b"corrupt")
    with pytest.raises(runner.ExperimentError, match="SOURCE_HASH_MISMATCH"):
        runner.source(receipt)


def test_missing_tokenizer_never_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "TOKENIZER", tmp_path / "missing")
    with pytest.raises(runner.ExperimentError, match="LOCAL_TOKENIZER"):
        runner.encoding()


def test_input_limit(monkeypatch, tokenizer):
    monkeypatch.setattr(runner, "INPUT_LIMIT", 1)
    with pytest.raises(runner.ExperimentError, match="INPUT_ALLOWANCE"):
        runner.plan(next(iter(runner.ALLOWED)), tokenizer)


@pytest.mark.parametrize("approved,budget", [(False, "0.04"), (True, ""), (True, "1")])
def test_execute_needs_explicit_approvals(approved, budget):
    with pytest.raises(runner.ExperimentError, match="APPROVAL_REQUIRED"):
        runner.run(execute_api=True, approval=approved, approved_usd=budget)
    assert not runner.JOURNAL.exists()


def test_execute_key_missing():
    with pytest.raises(runner.ExperimentError, match="API_KEY_MISSING"):
        runner.run(execute_api=True, approval=True, approved_usd="0.04")
    assert not runner.JOURNAL.exists()


def test_canonical_request_cannot_be_modified(tokenizer):
    plan = runner.plan(next(iter(runner.ALLOWED)), tokenizer)
    plan["payload"]["input"] += "private data"
    with pytest.raises(runner.ExperimentError, match="REQUEST_NOT_CANONICAL"):
        runner.execute(plan, "fake-key")
    assert not runner.JOURNAL.exists()


def test_atomic_one_attempt_across_threads(tokenizer):
    plan = runner.plan(next(iter(runner.ALLOWED)), tokenizer)

    def reserve():
        try:
            runner.reservation(plan["receipt"], plan)
            return "reserved"
        except runner.ExperimentError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reserve(), range(2)))
    assert results.count("reserved") == 1
    assert results.count("DOCUMENT_ATTEMPT_ALREADY_RESERVED") == 1


def test_total_three_and_never_release_failed_reservations(tokenizer):
    for receipt in runner.ALLOWED:
        request = runner.plan(receipt, tokenizer)
        runner.reservation(receipt, request)
        runner.finish(receipt, {"state": "FAILED", "error_code": "fixture"})
    with sqlite3.connect(runner.JOURNAL) as connection:
        assert connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 3
    with pytest.raises(runner.ExperimentError, match="ALREADY_RESERVED"):
        runner.reservation(next(iter(runner.ALLOWED)), request)


def test_three_limit_checked_even_with_preexisting_invalid_count(tokenizer):
    request = runner.plan(next(iter(runner.ALLOWED)), tokenizer)
    runner.reservation(request["receipt"], request)
    with sqlite3.connect(runner.JOURNAL) as connection:
        connection.execute("UPDATE attempts SET receipt='invalid'")
    with pytest.raises(runner.ExperimentError, match="JOURNAL_INTEGRITY"):
        runner.reservation(request["receipt"], request)


def test_budget_checked_before_reservation(monkeypatch, tokenizer):
    request = runner.plan(next(iter(runner.ALLOWED)), tokenizer)
    monkeypatch.setattr(runner, "BUDGET", runner.Decimal("0.01"))
    with pytest.raises(runner.ExperimentError, match="BUDGET_EXCEEDED"):
        runner.reservation(request["receipt"], request)
    with sqlite3.connect(runner.JOURNAL) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='attempts'",
        ).fetchone()[0] == 0


def sample(receipt):
    return {
        "receipt": receipt,
        "facts": [{
            "field": "contract_amount", "category": "basic", "status": "CONFIRMED",
            "value": "479,900,000,000", "unit": "KRW", "meaning": "Disclosed amount",
            "evidence": [{"table": 1, "row": 3, "cell": 2, "quote": "479,900,000,000"}],
        }],
        "limitations": ["Human review pending"],
    }


def response_data(receipt):
    return {
        "model": runner.MODEL, "status": "completed", "error": None,
        "usage": {"input_tokens": 4000, "output_tokens": 500, "total_tokens": 4500},
        "output": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": json.dumps(sample(receipt))}],
        }],
    }


def fake_transport(monkeypatch, data=None, *, code=200, error=None):
    response = Mock()
    response.status_code = code
    response.headers = {"x-request-id": "fixture-request"}
    response.iter_content.return_value = [json.dumps(data).encode()]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.post.return_value = response
    if error:
        session.post.side_effect = error
    monkeypatch.setattr(runner.requests, "Session", lambda: session)
    return session


def test_success_records_usage_evidence_and_transport_controls(monkeypatch, tokenizer):
    receipt = next(iter(runner.ALLOWED))
    session = fake_transport(monkeypatch, response_data(receipt))
    result = runner.execute(runner.plan(receipt, tokenizer), "fake-secret")
    assert result["state"] == "COMPLETED_HUMAN_REVIEW_PENDING"
    assert result["request_id"] == "fixture-request"
    assert result["actual_usd_uncached"] == "0.0024"
    assert result["evidence_checks"][0]["xpath"].endswith("tr[3]")
    assert "fake-secret" not in json.dumps(result)
    assert session.post.call_count == 1
    assert session.trust_env is False
    assert session.post.call_args.kwargs["allow_redirects"] is False
    assert session.post.call_args.args == (runner.ENDPOINT,)
    assert session.mount.call_args.args[1].max_retries.total == 0


@pytest.mark.parametrize("code", [301, 401, 429, 500])
def test_http_error_never_retries_and_does_not_log_remote_body(monkeypatch, tokenizer, code):
    receipt = next(iter(runner.ALLOWED))
    session = fake_transport(monkeypatch, {"secret": "fake-secret"}, code=code)
    result = runner.execute(runner.plan(receipt, tokenizer), "fake-secret")
    assert result["http_status"] == code
    assert result["error_code"] == "HTTP_FAILURE_NO_RETRY"
    assert session.post.call_count == 1
    assert "fake-secret" not in json.dumps(result)
    with pytest.raises(runner.ExperimentError, match="ALREADY_RESERVED"):
        runner.execute(runner.plan(receipt, tokenizer), "fake-secret")
    assert session.post.call_count == 1


def test_timeout_is_permanently_reserved_and_safe(monkeypatch, tokenizer):
    receipt = next(iter(runner.ALLOWED))
    session = fake_transport(
        monkeypatch, error=runner.requests.Timeout("Bearer fake-secret"),
    )
    result = runner.execute(runner.plan(receipt, tokenizer), "fake-secret")
    assert result["error_code"] == "TRANSPORT_FAILURE_NO_RETRY"
    assert "fake-secret" not in json.dumps(result)
    assert session.post.call_count == 1


@pytest.mark.parametrize("change,expected", [
    ("usage", "USAGE_EXCEEDS_RESERVATION_STOP"),
    ("incomplete", "RESPONSE_NOT_COMPLETED_OR_MODEL_MISMATCH"),
    ("tool", "NON_MESSAGE_OUTPUT"),
    ("refusal", "REFUSAL_OR_NON_TEXT_OUTPUT"),
    ("evidence", "OUTPUT_EVIDENCE_MISMATCH"),
    ("schema", "OUTPUT_SCHEMA_MISMATCH"),
    ("secret", "INVALID_OR_SECRET_CONTAINING_OUTPUT"),
])
def test_bad_outputs_are_not_success(monkeypatch, tokenizer, change, expected):
    receipt = next(iter(runner.ALLOWED))
    data = response_data(receipt)
    if change == "usage":
        data["usage"] = {"input_tokens": 21000, "output_tokens": 1, "total_tokens": 21001}
    elif change == "incomplete":
        data["status"] = "incomplete"
    elif change == "tool":
        data["output"][0]["type"] = "function_call"
    elif change == "refusal":
        data["output"][0]["content"][0]["type"] = "refusal"
    else:
        result = sample(receipt)
        if change == "evidence":
            result["facts"][0]["evidence"][0]["quote"] = "invented"
        elif change == "schema":
            result["extra"] = "unexpected"
        else:
            result["limitations"] = ["fake-secret"]
        data["output"][0]["content"][0]["text"] = json.dumps(result)
    fake_transport(monkeypatch, data)
    report = runner.execute(runner.plan(receipt, tokenizer), "fake-secret")
    assert report["state"] == "FAILED"
    assert report["error_code"] == expected
    assert "output" not in report


def test_run_stops_after_first_failure(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    execute = Mock(return_value={"state": "FAILED"})
    monkeypatch.setattr(runner, "execute", execute)
    result = runner.run(execute_api=True, approval=True, approved_usd="0.04")
    assert result["successful"] is False
    assert execute.call_count == 1


def test_unknown_value_cannot_be_asserted():
    result = sample(next(iter(runner.ALLOWED)))
    result["facts"][0]["status"] = "NOT_DISCLOSED"
    with pytest.raises(runner.ExperimentError, match="UNKNOWN_VALUE_NOT_NULL"):
        runner.evidence_check(result, result["receipt"])
