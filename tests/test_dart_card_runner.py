"""Offline safety gates and independent card execution management."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import dart_card_runner as runner
from src import dart_analysis_cards as cards
from src import dart_document_analysis as analysis
from tests.test_dart_analysis_cards import RECEIPTS, setup  # Shared isolated DB/HTTP fixture.

NOW = datetime(2026, 10, 20, 21, 15, tzinfo=runner.KST)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def environment(setup, tmp_path, monkeypatch):
    original_run = runner.run_cards

    def bounded_run(*args, **kwargs):
        kwargs.setdefault("max_items", 5)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(runner, "run_cards", bounded_run)
    monkeypatch.setattr(analysis, "now_iso", lambda: NOW.isoformat())
    old_source, old_store, rows, bodies, entries = setup
    source = tmp_path / cards.SOURCE_STORE.relative_to(cards.ROOT)
    store = tmp_path / cards.CARD_STORE.relative_to(cards.ROOT)
    source.parent.mkdir(parents=True)
    source.write_bytes(old_source.read_bytes())
    store.parent.mkdir(parents=True)
    success = "2026-10-20T11:20:10+00:00"
    with closing(sqlite3.connect(source)) as conn, conn:
        summary = json.loads(conn.execute("SELECT summary_json FROM last_success").fetchone()[0])
        summary["last_success_at"] = success
        conn.execute("UPDATE last_success SET summary_json=?", (json.dumps(summary),))
    directory = tmp_path / "output" / "dart_disclosure_scheduler"
    report = {
        "scheduler_date": "2026-10-20", "checked_at": "2026-10-20T20:20:00+09:00",
        "started_at": "2026-10-20T20:20:00+09:00",
        "completed_at": "2026-10-20T20:20:20+09:00",
        "actual_collected_at": "2026-10-20T11:20:00+00:00",
        "last_success_at": success, "verdict": "PASS", "stage": "COMPLETE",
        "executed": True, "accumulation_committed": True, "total_count": 3,
        "collection_report": summary["source_path"],
    }
    checkpoint = {
        "schema_version": 1, "baseline_only": False,
        "last_success_end_date": "2026-10-20", "last_success_at": success,
    }
    report_path = directory / "run.json"
    checkpoint_path = directory / "coverage.json"
    save(report_path, report)
    save(checkpoint_path, checkpoint)
    return tmp_path, source, store, report_path, checkpoint_path, report, checkpoint, bodies, entries


def snapshot(env):
    root, source, _, report_path, checkpoint_path, *_ = env
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        source, report_path, checkpoint_path,
    )}


def test_preview_then_execution_then_no_download_duplicates(environment, monkeypatch):
    root, source, store, *_ = environment
    before = snapshot(environment)
    cards.generate_cards(source, store, [RECEIPTS[0]])
    report, path = runner.run_cards(root, now=NOW)
    assert report["verdict"] == "PREVIEW" and report["selected_count"] == 2
    assert report["candidate_count"] == 3 and report["excluded_processed_count"] == 1
    assert RECEIPTS[0] not in report["selected"] and report["attempts"] == []
    assert json.loads(path.read_bytes()) == report
    report, _ = runner.run_cards(root, now=NOW, execute=True)
    assert report["verdict"] == "PASS" and report["created_count"] == 2
    assert report["success_count"] == 2 and report["partial_count"] == 0
    monkeypatch.setattr(analysis, "_get", lambda _url: pytest.fail("No redownload"))
    report, _ = runner.run_cards(root, now=NOW, execute=True)
    assert report["selected_count"] == report["created_count"] == report["duplicate_count"] == 0
    assert report["excluded_processed_count"] == 3
    assert snapshot(environment) == before


@pytest.mark.parametrize("key,value", [
    ("verdict", "FAIL"), ("stage", "COLLECTION"), ("accumulation_committed", False),
    ("executed", False), ("scheduler_date", "2026-10-19"), ("total_count", 999),
    ("actual_collected_at", "2026-10-19T11:20:00+00:00"),
    ("last_success_at", "2026-10-20T11:21:00+00:00"),
    ("completed_at", "2026-10-20T23:00:00+09:00"),
    ("collection_report", "wrong.json"),
])
def test_collection_gate_defers_before_any_http(environment, monkeypatch, key, value):
    root, _, store, path, _, report, *_ = environment
    report[key] = value
    save(path, report)
    before = snapshot(environment)
    monkeypatch.setattr(analysis, "_get", lambda _url: pytest.fail("Gate must stop HTTP"))
    result, output = runner.run_cards(root, now=NOW, execute=True)
    assert result["verdict"] == "DEFERRED" and result["exit_code"] == 2
    assert result["attempts"] == [] and result["reason"]
    assert output.exists() and not store.exists()
    assert snapshot(environment) == before


@pytest.mark.parametrize("key,value", [
    ("last_success_end_date", "2026-10-19"), ("last_success_at", "wrong"),
    ("baseline_only", True), ("schema_version", 99),
])
def test_checkpoint_mismatch_defers(environment, key, value):
    root, _, store, _, path, _, checkpoint, *_ = environment
    checkpoint[key] = value
    save(path, checkpoint)
    result, _ = runner.run_cards(root, now=NOW, execute=True)
    assert result["verdict"] == "DEFERRED"
    assert result["reason"] == "COLLECTION_CHECKPOINT_DB_MISMATCH"
    assert not store.exists()


def test_latest_failed_attempt_overrides_earlier_success(environment):
    root, _, _, path, _, report, *_ = environment
    failed = dict(report, verdict="FAIL", started_at="2026-10-20T20:30:00+09:00")
    save(path.parent / "later.json", failed)
    result, _ = runner.run_cards(root, now=NOW, execute=True)
    assert result["reason"] == "TODAY_COLLECTION_NOT_COMPLETE"


def test_explicit_historical_collection_keeps_source_verification(environment):
    root, _, _, _, checkpoint_path, _, checkpoint, *_ = environment
    next_day = NOW + timedelta(days=1)
    blocked, _ = runner.run_cards(root, now=next_day)
    assert blocked["reason"] == "TODAY_COLLECTION_NOT_FOUND"
    result, _ = runner.run_cards(root, now=next_day, collection_date=date(2026, 10, 20))
    assert result["verdict"] == "PREVIEW"
    assert result["collection_gate"]["collection_date"] == "2026-10-20"
    checkpoint["last_success_end_date"] = "2026-10-19"
    save(checkpoint_path, checkpoint)
    blocked, _ = runner.run_cards(root, now=next_day, collection_date=date(2026, 10, 20))
    assert blocked["reason"] == "COLLECTION_CHECKPOINT_DB_MISMATCH"


def test_lock_contention_reports_and_does_not_generate(environment, monkeypatch):
    root, _, store, *_ = environment
    directory = store.parent / "runs"
    directory.mkdir()
    monkeypatch.setattr(analysis, "_get", lambda _url: pytest.fail("No HTTP on contention"))
    with closing(sqlite3.connect(directory / "execution.lock.sqlite3")) as lock:
        lock.execute("BEGIN IMMEDIATE")
        result, path = runner.run_cards(root, now=NOW, execute=True)
        assert result["reason"] == "ALREADY_RUNNING" and result["exit_code"] == 2
        assert json.loads(path.read_bytes()) == result
    result, _ = runner.run_cards(root, now=NOW)
    assert result["verdict"] == "PREVIEW" and result["selected_count"] == 3


def test_failed_retry_cooldown_and_budget_without_repeating_success(environment, monkeypatch):
    root, _, _, *_ = environment
    original = analysis._get

    def get(url):
        if RECEIPTS[0] in url:
            raise analysis.DocumentError("OFFICIAL_HTTP_503")
        return original(url)

    monkeypatch.setattr(analysis, "_get", get)
    first, _ = runner.run_cards(root, now=NOW, execute=True)
    assert first["failed_count"] == 1 and first["success_count"] == 2 and first["exit_code"] == 1
    blocked, _ = runner.run_cards(root, now=NOW + timedelta(minutes=5), execute=True)
    assert blocked["selected_count"] == 0 and blocked["deferred_count"] == 1
    assert blocked["exit_code"] == 2
    for minutes in (31, 62):
        result, _ = runner.run_cards(root, now=NOW + timedelta(minutes=minutes), execute=True)
        assert result["selected"] == [RECEIPTS[0]] and result["failed_count"] == 1
    exhausted, _ = runner.run_cards(root, now=NOW + timedelta(minutes=93), execute=True)
    assert exhausted["selected_count"] == 0
    assert any(row["reason"] == "RETRY_EXHAUSTED" for row in exhausted["skipped"])


def test_retry_success_stops_future_http(environment, monkeypatch):
    root, _, _, *_ = environment
    original = analysis._get
    monkeypatch.setattr(analysis, "_get", lambda _url: (_ for _ in ()).throw(
        analysis.DocumentError("OFFICIAL_HTTP_503"),
    ))
    first, _ = runner.run_cards(root, now=NOW, execute=True)
    assert first["failed_count"] == 3
    monkeypatch.setattr(analysis, "_get", original)
    result, _ = runner.run_cards(root, now=NOW + timedelta(minutes=31), execute=True)
    assert result["created_count"] == 3 and result["partial_count"] == 1
    assert result["success_count"] == 2
    monkeypatch.setattr(analysis, "_get", lambda _url: pytest.fail("Done"))
    result, _ = runner.run_cards(root, now=NOW + timedelta(minutes=62), execute=True)
    assert result["selected_count"] == 0


def test_unsupported_is_not_retried_even_after_version_change(environment, monkeypatch):
    root, _, _, _, _, _, _, bodies, _ = environment
    bodies[RECEIPTS[0]] = b"<html>No table</html>"
    first, _ = runner.run_cards(root, now=NOW, execute=True)
    assert first["unsupported_count"] == 1
    monkeypatch.setattr(analysis, "_get", lambda _url: pytest.fail("Unsupported must stay excluded"))
    second, _ = runner.run_cards(root, now=NOW + timedelta(minutes=31), execute=True)
    assert second["selected_count"] == 0 and second["excluded_unsupported_count"] == 1
    monkeypatch.setattr(cards, "EXTRACTOR_VERSION", "next")
    preview, _ = runner.run_cards(root, now=NOW + timedelta(minutes=62))
    assert preview["selected_count"] == 0
    assert preview["excluded_unsupported_count"] == 1
    assert preview["excluded_processed_count"] == 2
    single, _ = runner.run_cards(
        root, now=NOW + timedelta(minutes=62), receipt_no=RECEIPTS[0],
    )
    assert single["selected"] == []
    assert single["skipped"][0]["reason"] == "UNSUPPORTED_PREVIOUS_RESULT"


def test_abrupt_interruption_is_reserved_and_recoverable(environment, monkeypatch):
    root, _, _, *_ = environment
    original = cards.generate_cards
    monkeypatch.setattr(cards, "generate_cards", lambda *_a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        runner.run_cards(root, now=NOW, execute=True)
    report, _ = runner.run_cards(root, now=NOW + timedelta(minutes=5))
    assert any(row["reason"] == "RETRY_COOLDOWN" for row in report["skipped"])
    monkeypatch.setattr(cards, "generate_cards", original)
    report, _ = runner.run_cards(root, now=NOW + timedelta(minutes=31), execute=True)
    assert report["created_count"] == 3


def test_legacy_failure_plus_interruptions_cannot_exceed_budget(environment, monkeypatch):
    root, source, store, *_ = environment
    cards.generate_cards(source, store, RECEIPTS[1:])
    original = analysis._get
    monkeypatch.setattr(analysis, "_get", lambda _url: (_ for _ in ()).throw(
        analysis.DocumentError("OFFICIAL_HTTP_503"),
    ))
    cards.generate_cards(source, store, [RECEIPTS[0]])
    monkeypatch.setattr(analysis, "_get", original)
    monkeypatch.setattr(cards, "generate_cards", lambda *_a: (_ for _ in ()).throw(KeyboardInterrupt()))
    # Include a legacy failure and two reservations that never reach the card DB.
    for minutes in (31, 62):
        with pytest.raises(KeyboardInterrupt):
            runner.run_cards(root, now=NOW + timedelta(minutes=minutes), execute=True)
    report, _ = runner.run_cards(root, now=NOW + timedelta(minutes=93))
    assert any(
        row["receipt"] == RECEIPTS[0] and row["reason"] == "RETRY_EXHAUSTED"
        for row in report["skipped"]
    )
    assert report["attempt_counts"][RECEIPTS[0]] == 3


def test_corrupt_collection_source_defers_without_changing_operational_files(environment):
    root, source, store, *_ = environment
    with closing(sqlite3.connect(source)) as conn:
        summary = json.loads(conn.execute("SELECT summary_json FROM last_success").fetchone()[0])
    source_file = Path(summary["source_path"])
    source_file.write_text("{}", encoding="utf-8")
    before = snapshot(environment)
    report, _ = runner.run_cards(root, now=NOW, execute=True)
    assert report["verdict"] == "DEFERRED" and report["reason"] == "SOURCE_HASH_MISMATCH"
    assert not store.exists() and snapshot(environment) == before


def test_middle_storage_failure_preserves_prior_commit_and_stops_later_work(environment, monkeypatch):
    root, _, store, *_ = environment
    original = cards.generate_cards

    def generate(source, dest, receipts):
        if receipts == [RECEIPTS[0]]:
            raise sqlite3.OperationalError("do not leak exception text")
        return original(source, dest, receipts)

    monkeypatch.setattr(cards, "generate_cards", generate)
    report, path = runner.run_cards(root, now=NOW, execute=True)
    assert report["failed_count"] == 1 and report["created_count"] == 1
    assert report["aborted"] and report["exit_code"] == 1
    assert len(report["attempts"]) == 2 and report["deferred_count"] == 1
    assert "do not leak" not in path.read_text()
    assert len(cards.query_cards(store)) == 1


def test_collection_changes_mid_run_defers_remaining(environment, monkeypatch):
    root, _, _, _, checkpoint_path, _, checkpoint, *_ = environment
    original = cards.generate_cards

    def generate(*args):
        result = original(*args)
        checkpoint["last_success_end_date"] = "2026-10-19"
        save(checkpoint_path, checkpoint)
        return result

    monkeypatch.setattr(cards, "generate_cards", generate)
    report, _ = runner.run_cards(root, now=NOW, execute=True)
    assert len(report["attempts"]) == 1 and report["deferred_count"] == 2
    assert report["exit_code"] != 0


def test_cli_exit_codes_and_report_path(environment, monkeypatch, capsys):
    root, *_ = environment
    original = runner.run_cards
    monkeypatch.setattr(runner, "run_cards", lambda **kw: original(root, now=NOW, **kw))
    assert runner.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "PREVIEW" and report["run_report"]
    assert runner.main(["--execute", "--max-items", "5"]) == 0
    assert json.loads(capsys.readouterr().out)["created_count"] == 3


def test_report_storage_error_is_explicit_in_cli(monkeypatch, capsys):
    monkeypatch.setattr(runner, "run_cards", lambda **_kw: (_ for _ in ()).throw(
        PermissionError("do not expose internal paths"),
    ))
    assert runner.main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["report_saved"] is False and report["reason"] == "PermissionError"


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "wrong"])
def test_cli_rejects_invalid_limits_before_work(monkeypatch, value):
    monkeypatch.setattr(runner, "run_cards", lambda **_kw: pytest.fail("Must reject before work"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["--execute", "--max-items", value])
    assert exc.value.code == 2


def test_cli_requires_explicit_unlimited_approval(monkeypatch):
    monkeypatch.setattr(runner, "run_cards", lambda **_kw: pytest.fail("Must reject before work"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["--execute"])
    assert exc.value.code == 2


@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_api_rejects_invalid_limit_before_output(tmp_path, value):
    with pytest.raises(cards.CardError, match="INVALID_MAX_ITEMS"):
        runner.run_cards(tmp_path, execute=True, max_items=value)
    assert not (tmp_path / "output").exists()


def test_api_rejects_unapproved_unlimited(tmp_path):
    with pytest.raises(cards.CardError, match="EXPLICIT_UNLIMITED_APPROVAL_REQUIRED"):
        runner.run_cards(tmp_path, execute=True)
    assert not (tmp_path / "output").exists()


def test_newest_five_of_fifteen_selected_before_generation(environment, monkeypatch):
    root, *_ = environment
    receipts = [f"2026102000{i:04d}" for i in range(15)]
    monkeypatch.setattr(runner, "select_pending", lambda *_a: {
        "candidate_count": 18, "selected": list(reversed(receipts)),
        "skipped": [{"receipt": r, "reason": "ALREADY_PROCESSED"} for r in RECEIPTS],
        "attempt_counts": dict.fromkeys(receipts, 0),
    })
    monkeypatch.setattr(analysis, "_get", lambda _url: pytest.fail("No HTTP in preview"))
    report, _ = runner.run_cards(root, now=NOW, max_items=5)
    assert report["selected"] == list(reversed(receipts))[:5]
    assert report["eligible_count"] == 15 and report["selected_count"] == 5
    assert report["limit_excluded_count"] == 10 and report["attempts"] == []
    calls = []

    def generate(_source, _store, selected):
        calls.extend(selected)
        return {
            "created": 1, "duplicates": 0,
            "results": [{"receipt": selected[0], "status": "COMPLETE"}],
        }

    monkeypatch.setattr(cards, "generate_cards", generate)
    report, _ = runner.run_cards(root, now=NOW, execute=True, max_items=5)
    assert calls == list(reversed(receipts))[:5] and report["created_count"] == 5


def test_time_budget_leaves_unstarted_items_for_next_run(environment, monkeypatch):
    root, *_ = environment
    clock = iter([0, 1, 601])
    with monkeypatch.context() as patch:
        patch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(clock)))
        report, _ = runner.run_cards(root, now=NOW, execute=True)
    assert report["reason"] == "RUN_TIME_LIMIT_EXCEEDED"
    assert report["exit_code"] == 2
    assert report["created_count"] == 1 and report["deferred_count"] == 2
    next_run, _ = runner.run_cards(root, now=NOW + timedelta(minutes=1))
    assert next_run["selected_count"] == 2
    assert report["selected"][0] not in next_run["selected"]


def test_success_with_retry_backlog_still_allows_snapshot_update(environment, monkeypatch):
    root, *_ = environment
    monkeypatch.setattr(runner, "select_pending", lambda *_a: {
        "candidate_count": 2, "selected": [RECEIPTS[0]],
        "skipped": [{"receipt": RECEIPTS[1], "reason": "RETRY_COOLDOWN"}],
        "attempt_counts": {RECEIPTS[0]: 0},
    })
    report, _ = runner.run_cards(root, now=NOW, execute=True)
    assert report["verdict"] == "PASS_WITH_PENDING"
    assert report["exit_code"] == 0 and report["deferred_count"] == 1
    assert report["created_count"] == 1


def test_explicit_unlimited_executes_all_eligible(environment):
    root, *_ = environment
    report, _ = runner.run_cards(root, now=NOW, execute=True, max_items=None, allow_unlimited=True)
    assert report["created_count"] == 3 and report["allow_unlimited"]


@pytest.mark.parametrize("exception", [RuntimeError, KeyError, TypeError, sqlite3.OperationalError])
def test_unexpected_exception_stops_every_later_receipt(environment, monkeypatch, exception):
    root, *_ = environment
    calls = []

    def crash(_source, _store, receipts):
        calls.extend(receipts)
        raise exception("private content must not appear")

    monkeypatch.setattr(cards, "generate_cards", crash)
    report, path = runner.run_cards(root, now=NOW, execute=True, max_items=5)
    assert len(calls) == 1 and len(report["attempts"]) == 1
    assert report["exit_code"] == 1 and report["aborted"]
    assert report["deferred_count"] == 2 and report["failed_count"] == 1
    assert report["reason"] == exception.__name__
    assert "private content" not in path.read_text()


def test_receipt_preview_and_single_execution_exclude_every_other_item(environment, monkeypatch):
    root, source, store, *_ = environment
    before = snapshot(environment)
    target = RECEIPTS[1]
    report, _ = runner.run_cards(root, now=NOW, receipt_no=target, max_items=None)
    assert report["selected"] == [target] and report["candidate_count"] == 1
    assert report["attempts"] == [] and not store.exists()
    original = cards.generate_cards
    calls = []

    def generate(src, dest, receipts):
        calls.extend(receipts)
        assert receipts == [target]
        return original(src, dest, receipts)

    monkeypatch.setattr(cards, "generate_cards", generate)
    report, _ = runner.run_cards(
        root, now=NOW, execute=True, receipt_no=target, max_items=None,
    )
    assert calls == [target] and report["created_count"] == 1
    assert [r["receipt"] for r in cards.query_cards(store)] == [target]
    again, _ = runner.run_cards(root, now=NOW, execute=True, receipt_no=target)
    assert again["selected"] == [] and calls == [target]
    assert snapshot(environment) == before


@pytest.mark.parametrize("value", ["wrong", "", "2026101980010", "202610198001080"])
def test_bad_receipt_rejected_before_work(tmp_path, value, monkeypatch):
    with pytest.raises(cards.CardError, match="INVALID_RECEIPT"):
        runner.run_cards(tmp_path, receipt_no=value)
    assert not (tmp_path / "output").exists()
    monkeypatch.setattr(runner, "run_cards", lambda **_kw: pytest.fail("Invalid receipt"))
    with pytest.raises(SystemExit) as exc:
        runner.main(["--receipt-no", value])
    assert exc.value.code == 2


@pytest.mark.parametrize("unsupported", [False, True])
def test_missing_or_unsupported_receipt_never_falls_back_to_other_candidates(
    environment, monkeypatch, unsupported,
):
    root, source, store, *_ = environment
    target = "20261019999999"
    if unsupported:
        target = RECEIPTS[0]
        with closing(sqlite3.connect(source)) as conn, conn:
            row = json.loads(conn.execute(
                "SELECT payload_json FROM disclosures WHERE rcept_no=?", (target,),
            ).fetchone()[0])
            row["report_nm"] = "지원하지 않는 공시"
            conn.execute("UPDATE disclosures SET payload_json=? WHERE rcept_no=?",
                         (json.dumps(row), target))
    monkeypatch.setattr(cards, "generate_cards", lambda *_a: pytest.fail("No generation"))
    report, _ = runner.run_cards(root, now=NOW, execute=True, receipt_no=target)
    assert report["reason"] == "RECEIPT_NOT_SUPPORTED_CANDIDATE"
    assert report["exit_code"] != 0 and report["attempts"] == []
    assert not store.exists()


def test_receipt_retry_keeps_cooldown_and_budget(environment, monkeypatch):
    root, *_ = environment
    monkeypatch.setattr(analysis, "_get", lambda _url: (_ for _ in ()).throw(
        analysis.DocumentError("OFFICIAL_HTTP_503"),
    ))
    target = RECEIPTS[0]
    for minutes in (0, 31, 62):
        report, _ = runner.run_cards(
            root, now=NOW + timedelta(minutes=minutes), execute=True, receipt_no=target,
        )
        assert [a["receipt"] for a in report["attempts"]] == [target]
        if minutes == 0:
            blocked, _ = runner.run_cards(
                root, now=NOW + timedelta(minutes=1), receipt_no=target,
            )
            assert blocked["selected"] == []
            assert blocked["skipped"][0]["reason"] == "RETRY_COOLDOWN"
    report, _ = runner.run_cards(root, now=NOW + timedelta(minutes=63), receipt_no=target)
    assert report["selected"] == [] and report["skipped"][0]["reason"] == "RETRY_EXHAUSTED"


def test_receipt_cli_and_version_change_scope(environment, monkeypatch, capsys):
    root, source, store, *_ = environment
    cards.generate_cards(source, store, RECEIPTS)
    monkeypatch.setattr(cards, "EXTRACTOR_VERSION", "new-alias-version")
    all_items, _ = runner.run_cards(root, now=NOW)
    assert all_items["selected_count"] == 0
    assert all_items["excluded_processed_count"] == 3
    original = runner.run_cards
    monkeypatch.setattr(runner, "run_cards", lambda **kw: original(root, now=NOW, **kw))
    assert runner.main(["--receipt-no", RECEIPTS[1]]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["selected"] == [] and report["attempts"] == []
    assert report["skipped"][0]["reason"] == "ALREADY_PROCESSED"


def test_receipt_mode_keeps_collection_gate_and_lock(environment):
    root, _, store, report_path, *_ = environment
    directory = store.parent / "runs"
    directory.mkdir()
    with closing(sqlite3.connect(directory / "execution.lock.sqlite3")) as lock:
        lock.execute("BEGIN IMMEDIATE")
        report, _ = runner.run_cards(root, now=NOW, execute=True, receipt_no=RECEIPTS[0])
        assert report["reason"] == "ALREADY_RUNNING" and report["attempts"] == []
    saved = json.loads(report_path.read_bytes())
    saved["verdict"] = "FAIL"
    save(report_path, saved)
    report, _ = runner.run_cards(root, now=NOW, execute=True, receipt_no=RECEIPTS[0])
    assert report["reason"] == "TODAY_COLLECTION_NOT_COMPLETE" and report["attempts"] == []
    assert not store.exists()


def test_mixed_versions_resolved_failure_and_new_candidate(environment, monkeypatch):
    root, source, store, *_ = environment
    original = analysis._get
    monkeypatch.setattr(analysis, "_get", lambda _url: (_ for _ in ()).throw(
        analysis.DocumentError("OFFICIAL_HTTP_503"),
    ))
    cards.generate_cards(source, store, [RECEIPTS[0]])
    monkeypatch.setattr(analysis, "_get", original)
    cards.generate_cards(source, store, [RECEIPTS[0]])  # PARTIAL resolves FAILED.
    monkeypatch.setattr(cards, "EXTRACTOR_VERSION", "other-complete-version")
    cards.generate_cards(source, store, [RECEIPTS[1]])
    monkeypatch.setattr(cards, "EXTRACTOR_VERSION", "new-current-version")
    before = store.read_bytes()
    monkeypatch.setattr(cards, "generate_cards", lambda *_a: pytest.fail("No regeneration"))
    report, _ = runner.run_cards(root, now=NOW)
    assert report["candidate_count"] == 3 and report["selected"] == [RECEIPTS[2]]
    assert report["excluded_processed_count"] == 2
    for receipt in RECEIPTS[:2]:
        single, _ = runner.run_cards(root, now=NOW, receipt_no=receipt, execute=True)
        assert single["selected"] == [] and single["attempts"] == []
        assert single["skipped"][0]["reason"] == "ALREADY_PROCESSED"
    assert store.read_bytes() == before


def test_unsupported_attempt_without_card_is_excluded_across_versions(environment, monkeypatch):
    root, source, store, *_ = environment
    cards.generate_cards(source, store, [RECEIPTS[1]])
    with closing(sqlite3.connect(store)) as conn, conn:
        conn.execute(
            "INSERT INTO attempts(receipt,ticker,status,attempted_at,error_code,extractor_version)"
            " VALUES (?,?,?,?,?,?)",
            (RECEIPTS[0], "010950", "UNSUPPORTED", NOW.isoformat(), "UNSUPPORTED_TYPE", "old"),
        )
    monkeypatch.setattr(cards, "EXTRACTOR_VERSION", "next")
    report, _ = runner.run_cards(root, now=NOW)
    assert report["selected"] == [RECEIPTS[2]]
    assert report["excluded_unsupported_count"] == 1 and report["excluded_processed_count"] == 1
