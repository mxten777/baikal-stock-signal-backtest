"""Card generation, archival evidence and read-only queries, without live HTTP."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
import requests

from scripts.dart_card_generate import main as generate_main
from scripts.dart_card_query import main as query_main
from src import dart_analysis_cards as cards
from src import dart_document_analysis as analysis

RECEIPTS = ["20261020800241", "20261020800221", "20261020800720"]


def require_pilot_archive():
    if not cards.CARD_STORE.exists():
        pytest.skip("Local 18-disclosure pilot archive is not installed")
    with closing(sqlite3.connect(cards.CARD_STORE.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        counts = [conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("cards", "attempts", "documents")]
    if counts != [21, 28, 18]:
        pytest.skip("Local store is not the fixed 18-disclosure pilot archive")


def table(rows):
    return ("<html><head><meta charset='utf-8'></head><body><table>" + "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    ) + "</table></body></html>").encode()


def contract_rows(second=False):
    return [
        ["2. 계약내역", "계약금액(원)", "479,900,000,000" if second else "1,160,000,000,000"],
        ["매출액대비(%)", "2.73" if second else "3.4"],
        ["3. 계약상대", "HMM" if second else "주식회사 이수화학"],
        ["5. 계약기간", "시작일", "2026-10-20" if second else "2026-10-01"],
        ["종료일", "2029-12-31" if second else "2027-09-30"],
        ["대금지급 조건 등", "공사진척에 따른 수금" if second else "-"],
    ]


def safety_rows():
    return [
        ["1. 중대재해내용", "발생 장소", "변압기 신공장 공사현장 콘덴서룸"],
        ["발생 재해 내용", "콘덴서룸 개구부에서 하부로 추락"],
        ["사망자 수", "1"], ["부상자 수", "0"],
        ["2. 중대재해 발생일자", "2026-10-19"],
        ["3. 고용노동부 보고일자", "2026-10-20"],
        ["4. 조치사항 및 향후대책", "현장 조사 실시 및 재발방지대책 수립 예정"],
        ["5. 기타 투자판단과 관련한 중요사항", "부분 작업중지 이행 중입니다."],
    ]


def test_archived_pilot_recovers_twelve_fields_without_writes_or_network(monkeypatch):
    require_pilot_archive()
    monkeypatch.setattr(analysis, "_get", lambda *_a, **_k: pytest.fail("No downloads"))
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: pytest.fail("No HTTP"))
    before = cards.CARD_STORE.read_bytes()
    results = cards.query_cards(cards.CARD_STORE, limit=100)
    expected = {
        ("20261019800267", "contract_amount"): "180,274,763,321",
        ("20261019800267", "revenue_ratio"): "9.42",
        ("20261019900272", "contract_amount"): "10,197,087,353",
        ("20261019900272", "counterparty"): "미국 글로벌 우주항공 발사업체",
        ("20261019900317", "contract_amount"): "123,010,501,824",
        ("20261019900317", "counterparty"): "ACBEL POLYTECH INC",
        ("20261019900334", "contract_amount"): "3,821,693,400",
        ("20261019900334", "counterparty"): "일본 반도체 기판 제조회사",
        ("20261019900668", "contract_amount"): "9,172,521,329",
        ("20261019900668", "counterparty"): "알스톰 이스라엘",
        ("20261020900678", "counterparty"): "한국ULVAC",
        ("20261020800596", "work_stoppage"): None,
    }
    recovered, remaining, statuses = set(), set(), []
    with closing(sqlite3.connect(
        cards.CARD_STORE.resolve().as_uri() + "?mode=ro", uri=True,
    )) as conn:
        conn.execute("PRAGMA query_only=ON")
        for result in results:
            card = result["card"]
            assert card is not None
            body = conn.execute(
                "SELECT content FROM documents WHERE sha256=?", (card["body_sha256"],),
            ).fetchone()[0]
            extracted = analysis.extract_fields(body, {
                "classification": {"category": card["profile"]}, "report_nm": card["report_nm"],
                "is_correction": card["is_correction"],
            }, card["document"])
            fields = extracted["fields"]
            if card["profile"] == "CONTRACT":
                cards._conservative_contract_fields(fields)
            for old, new in zip(card["fields"], fields, strict=True):
                assert old["field"] == new["field"]
                key = (card["rcept_no"], old["field"])
                if key in expected:
                    assert old["status"] == "UNCONFIRMED"
                    assert new["status"] == "VERIFIED"
                    if expected[key] is not None:
                        assert new["raw_value"] == expected[key]
                    else:
                        assert new["normalized_value"] == "IN_EFFECT"
                        assert new["scope"] == "PARTIAL"
                        assert new["scope_detail"] == "거푸집 조립 및 해체 작업 일체"
                    recovered.add(key)
                elif old["status"] == "VERIFIED":
                    assert new["status"] == old["status"]
                    assert new["raw_value"] == old["raw_value"]
                    assert new["numeric_value"] == old["numeric_value"]
                else:
                    assert new["status"] == "UNCONFIRMED"
                    if key == ("20261020900678", "contract_amount"):
                        assert new["raw_value"] == "-"
                    else:
                        assert new["raw_value"] == old["raw_value"]
                    remaining.add(key)
            memory_card = {**card, "fields": fields}
            cards.verify_evidence(memory_card, body)
            statuses.append("COMPLETE" if all(f["status"] == "VERIFIED" for f in fields) else "PARTIAL")
    assert recovered == set(expected)
    assert len(remaining) == 12
    assert len(results) == 18
    assert statuses.count("COMPLETE") == 8
    assert statuses.count("PARTIAL") == 10
    assert cards.CARD_STORE.read_bytes() == before


@pytest.fixture
def archive_batch(setup, monkeypatch):
    source, store, rows, bodies, _ = setup
    with closing(cards._open_store(store, source)) as conn, conn:
        for index, receipt in enumerate(cards.ARCHIVE_REANALYSIS_RECEIPTS):
            row = {**rows[0], "rcept_no": receipt, "name": "보관기업",
                   "original_url": analysis.BASE + "/dsaf001/main.do?rcpNo=" + receipt}
            body = bodies[RECEIPTS[0]]
            evidence = {
                "url": analysis.BASE + "/report/viewer.do?rcpNo=" + receipt + "&dcmNo=123",
                "sha256": hashlib.sha256(body).hexdigest(),
                "checked_at": "2026-10-20T00:00:00+00:00", "http_status": 200,
            }
            card = cards._card_from_body(row, body, evidence)
            card["extractor_version"] = "old-version"
            conn.execute("INSERT OR IGNORE INTO documents VALUES (?,?)", (evidence["sha256"], body))
            conn.execute("INSERT INTO cards VALUES (?,?,?,?)", (
                receipt, evidence["sha256"], "old-version", json.dumps(card),
            ))
            conn.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)", (
                index + 1, receipt, row["ticker"], card["status"], evidence["checked_at"],
                None, evidence["sha256"], "old-version",
            ))
    monkeypatch.setattr(analysis, "_get", lambda *_a, **_k: pytest.fail("Archive HTTP path"))
    monkeypatch.setattr(cards, "build_card", lambda *_a, **_k: pytest.fail("Online builder"))
    return store


def archive_rows(store):
    with closing(sqlite3.connect(store.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return {name: conn.execute("SELECT * FROM " + name).fetchall()
                for name in ("cards", "attempts", "documents")}


def test_archive_preview_atomic_insert_and_idempotent_history(archive_batch):
    store = archive_batch
    original = archive_rows(store)
    before = store.read_bytes()
    preview = cards.reanalyze_archived(store)
    assert preview["verdict"] == "PREVIEW" and preview["selected_count"] == 7
    assert preview["created"] == preview["http_request_count"] == 0
    assert all(r["body_sha256"] and r["extractor_version"] for r in preview["results"])
    assert store.read_bytes() == before
    assert not (store.parent / "runs").exists()
    result = cards.reanalyze_archived(store, execute=True)
    assert result["created"] == 7 and result["http_request_count"] == 0
    saved = archive_rows(store)
    assert len(saved["cards"]) == len(saved["attempts"]) == 14
    assert saved["documents"] == original["documents"]
    for name in ("cards", "attempts"):
        assert all(row in saved[name] for row in original[name])
    for item in cards.query_cards(store):
        assert item["card"]["extractor_version"] == cards.EXTRACTOR_VERSION
        assert item["card"]["generation_mode"] == "ARCHIVED_REANALYSIS"
        assert item["card"]["document"]["checked_at"] == "2026-10-20T00:00:00+00:00"
        assert item["card"]["generated_at"] != item["card"]["document"]["checked_at"]
    repeat = cards.reanalyze_archived(store, execute=True)
    assert repeat["created"] == 0 and repeat["duplicates"] == 7
    assert archive_rows(store) == saved


@pytest.mark.parametrize("failure", ["analysis", "insert", "hash", "missing"])
def test_archive_batch_failure_never_partially_commits(archive_batch, monkeypatch, failure):
    store = archive_batch
    if failure == "analysis":
        real = cards._card_from_body
        def fail_third(row, body, evidence):
            if row["rcept_no"] == cards.ARCHIVE_REANALYSIS_RECEIPTS[2]:
                raise RuntimeError("synthetic analysis failure")
            return real(row, body, evidence)
        monkeypatch.setattr(cards, "_card_from_body", fail_third)
    else:
        with closing(sqlite3.connect(store)) as conn, conn:
            if failure == "insert":
                conn.execute(
                    "CREATE TRIGGER fail_third BEFORE INSERT ON attempts "
                    "WHEN NEW.extractor_version != 'old-version' AND NEW.receipt='"
                    + cards.ARCHIVE_REANALYSIS_RECEIPTS[2] + "' "
                    "BEGIN SELECT RAISE(ABORT,'synthetic insertion failure'); END"
                )
            elif failure == "hash":
                conn.execute("UPDATE documents SET content=?", (b"tampered HTML",))
            else:
                conn.execute("DELETE FROM cards WHERE receipt=?", (cards.ARCHIVE_REANALYSIS_RECEIPTS[-1],))
    before = archive_rows(store)
    with pytest.raises((RuntimeError, sqlite3.IntegrityError, cards.CardError)):
        cards.reanalyze_archived(store, execute=True)
    assert archive_rows(store) == before


def test_archive_uses_daily_runner_lease(archive_batch):
    store = archive_batch
    before = archive_rows(store)
    directory = store.parent / "runs"
    directory.mkdir()
    with closing(sqlite3.connect(directory / "execution.lock.sqlite3")) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(cards.CardError, match="ALREADY_RUNNING"):
                executor.submit(cards.reanalyze_archived, store, execute=True).result()
        assert archive_rows(store) == before
    assert cards.reanalyze_archived(store, execute=True)["created"] == 7


def test_archive_cli_preview_and_error_reporting(archive_batch, capsys, monkeypatch):
    assert generate_main(["--store", str(archive_batch), "--reanalyze-archive"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "PREVIEW"
    assert generate_main([
        "--store", str(archive_batch), "--reanalyze-archive", "--execute",
        "--receipt", cards.ARCHIVE_REANALYSIS_RECEIPTS[0],
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "EXACT_ARCHIVE_BATCH_REQUIRED"
    def fail(*_a, **_k):
        raise RuntimeError("private error detail")
    monkeypatch.setattr(cards, "_card_from_body", fail)
    assert generate_main(["--store", str(archive_batch), "--reanalyze-archive", "--execute"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error_code"] == "RuntimeError" and report["created"] == 0
    assert report["http_request_count"] == 0 and "private" not in json.dumps(report)


def test_archived_pilot_copy_adds_seven_versions_and_attempts(tmp_path, monkeypatch):
    require_pilot_archive()
    store = tmp_path / "pilot.sqlite3"
    shutil.copy2(cards.CARD_STORE, store)
    monkeypatch.setattr(analysis, "_get", lambda *_a, **_k: pytest.fail("No HTTP"))
    monkeypatch.setattr(cards, "build_card", lambda *_a, **_k: pytest.fail("No online builder"))
    original = archive_rows(store)
    assert [len(original[n]) for n in ("cards", "attempts", "documents")] == [21, 28, 18]
    assert cards.reanalyze_archived(store)["selected_count"] == 7
    assert cards.reanalyze_archived(store, execute=True)["created"] == 7
    saved = archive_rows(store)
    assert [len(saved[n]) for n in ("cards", "attempts", "documents")] == [28, 35, 18]
    assert saved["documents"] == original["documents"]
    for name in ("cards", "attempts"):
        assert all(row in saved[name] for row in original[name])
    statuses = [r["card"]["status"] for r in cards.query_cards(store, limit=100)]
    assert statuses.count("COMPLETE") == 8 and statuses.count("PARTIAL") == 10
    assert cards.reanalyze_archived(store, execute=True)["duplicates"] == 7
    assert archive_rows(store) == saved


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: pytest.fail("No live HTTP"))
    rows = []
    bodies = {}
    entries = {}
    for i, receipt in enumerate(RECEIPTS):
        row = {
            "rcept_no": receipt, "ticker": ("010950", "329180", "267260")[i],
            "name": ("S-Oil", "HD현대중공업", "HD현대일렉트릭")[i],
            "rcept_dt": "2026-10-20",
            "report_nm": "중대재해발생(종속회사의주요경영사항)" if i == 2 else "단일판매ㆍ공급계약체결",
            "original_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
            "is_correction": False,
            "classification": {"category": "SAFETY_LEGAL" if i == 2 else "CONTRACT", "importance": "HIGH"},
        }
        rows.append(row)
        entries[receipt] = (
            f"<html><title>{row['name']} {row['report_nm']} 2026.10.20</title>"
            f"<script>viewDoc('{receipt}','123','0','0','0','HTML');</script></html>"
        ).encode()
        bodies[receipt] = table(safety_rows() if i == 2 else contract_rows(i == 1))
    source = tmp_path / "source.sqlite3"
    source_json = tmp_path / "source.json"
    source_json.write_text(json.dumps({
        "verdict": "PASS", "collection_complete": True, "scope": "all",
        "collected_at": "2026-10-20T11:20:00+00:00",
        "start_date": "2026-10-18", "end_date": "2026-10-20",
    }), encoding="utf-8")
    summary = {
        "verdict": "PASS", "total_count": 3, "source_path": str(source_json),
        "source_sha256": hashlib.sha256(source_json.read_bytes()).hexdigest(),
    }
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute("PRAGMA user_version=1")
        conn.execute("CREATE TABLE disclosures(rcept_no TEXT PRIMARY KEY,payload_json TEXT)")
        conn.executemany("INSERT INTO disclosures VALUES (?,?)", [
            (row["rcept_no"], json.dumps(row)) for row in rows
        ])
        conn.execute("CREATE TABLE last_success(id INTEGER PRIMARY KEY,summary_json TEXT)")
        conn.execute("INSERT INTO last_success VALUES (1,?)", (json.dumps(summary),))

    def get(url):
        receipt = url.split("rcpNo=")[1].split("&")[0]
        content = bodies[receipt] if "/report/" in url else entries[receipt]
        return content, {
            "url": url, "sha256": hashlib.sha256(content).hexdigest(),
            "http_status": 200, "checked_at": "2026-10-20T11:36:00+00:00",
        }

    monkeypatch.setattr(analysis, "_get", get)
    return source, tmp_path / "cards.sqlite3", rows, bodies, entries


def test_three_cards_and_duplicate_roundtrip(setup):
    source, store, _, _, _ = setup
    before = source.read_bytes()
    report = cards.generate_cards(source, store, RECEIPTS)
    assert (report["created"], report["failed"], report["duplicates"]) == (3, 0, 0)
    results = {r["receipt"]: r for r in cards.query_cards(store)}
    for receipt, count, status in zip(RECEIPTS, (5, 6, 8), ("PARTIAL", "COMPLETE", "COMPLETE")):
        card = results[receipt]["card"]
        assert card["status"] == status
        assert sum(f["status"] == "VERIFIED" for f in card["fields"]) == count
        assert card["verified_evidence_count"] == count
        assert card["checklist"] and card["cautions"]
        assert results[receipt]["previous_result_only"] is False
        with closing(sqlite3.connect(store)) as conn:
            body = conn.execute(
                "SELECT content FROM documents WHERE sha256=?", (card["body_sha256"],),
            ).fetchone()[0]
        assert cards.verify_evidence(card, body) == count
        text = cards.render_markdown(results[receipt])
        assert card["document"]["url"] in text and "SHA-256" in text
    second = cards.generate_cards(source, store, RECEIPTS + [RECEIPTS[0]])
    assert (second["created"], second["duplicates"]) == (0, 3)
    with closing(sqlite3.connect(store)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
    assert source.read_bytes() == before


@pytest.mark.parametrize("text", [
    "부분 작업중지 예정입니다.", "부분 작업중지 이행 중이 아닙니다.",
    "부분 작업중지 이행 여부 확인 중입니다.",
    "- 부분 작업중지 이행 중입니다. - 이후 명령이 해제되었습니다.",
    "- 부분 작업중지 이행 중입니다. - 전체 작업중지 실시 중입니다.", "-",
])
def test_unknown_stoppage_never_rendered_as_fact(setup, text):
    source, store, _, bodies, _ = setup
    rows = safety_rows()
    rows[-1][-1] = text
    bodies[RECEIPTS[2]] = table(rows)
    cards.generate_cards(source, store, [RECEIPTS[2]])
    result = cards.query_cards(store)[0]
    field = result["card"]["fields"][-1]
    assert field["status"] == "UNCONFIRMED" and field["scope"] is None
    facts = cards.render_markdown(result).split("### 확인된 사실")[1].split("### 미확인")[0]
    assert "work_stoppage" not in facts


@pytest.mark.parametrize("text,scope,value", [
    ("작업중지 조치는 없습니다.", "NONE", "NOT_IN_EFFECT"),
    ("부분 작업중지 이행 중입니다.", "PARTIAL", "IN_EFFECT"),
    ("전체 작업중지 실시 중입니다.", "FULL", "IN_EFFECT"),
])
def test_explicit_stoppage_scope(setup, text, scope, value):
    source, store, _, bodies, _ = setup
    rows = safety_rows()
    rows[-1][-1] = text
    bodies[RECEIPTS[2]] = table(rows)
    cards.generate_cards(source, store, [RECEIPTS[2]])
    field = cards.query_cards(store)[0]["card"]["fields"][-1]
    assert (field["status"], field["scope"], field["normalized_value"]) == ("VERIFIED", scope, value)


def test_conflicting_contract_and_missing_safety_fields(setup):
    source, store, _, bodies, _ = setup
    rows = contract_rows()
    rows.append(["계약금액(원)", "999"])
    bodies[RECEIPTS[0]] = table(rows)
    rows = safety_rows()
    rows[2][-1] = "-1"
    del rows[3]
    bodies[RECEIPTS[2]] = table(rows)
    cards.generate_cards(source, store)
    contract = cards.query_cards(store, receipt=RECEIPTS[0])[0]["card"]
    assert contract["fields"][0]["status"] == "UNCONFIRMED"
    safety = cards.query_cards(store, ticker="267260")[0]["card"]
    assert safety["fields"][2]["status"] == safety["fields"][3]["status"] == "UNCONFIRMED"
    assert safety["status"] == "PARTIAL"


@pytest.mark.parametrize("index,value,field_index", [
    (0, "-1", 0), (1, "-2.73", 1), (2, "계약상대 확인 중", 2),
    (3, "2026-02-30", 3), (4, "2029-12-31 예정", 4), (5, "지급 조건 협의 중", 5),
])
def test_invalid_or_uncertain_contract_values_are_not_facts(setup, index, value, field_index):
    source, store, _, bodies, _ = setup
    rows = contract_rows(True)
    rows[index][-1] = value
    bodies[RECEIPTS[1]] = table(rows)
    cards.generate_cards(source, store, [RECEIPTS[1]])
    result = cards.query_cards(store)[0]
    field = result["card"]["fields"][field_index]
    assert field["status"] == "UNCONFIRMED"
    facts = cards.render_markdown(result).split("### 확인된 사실")[1].split("### 미확인")[0]
    assert field["field"] not in facts


@pytest.mark.parametrize("body", [b"<html>No tables</html>", table([["unknown", "value"]])])
@pytest.mark.parametrize("receipt", [RECEIPTS[0], RECEIPTS[2]])
def test_unsupported_body_has_no_confirmed_facts(setup, body, receipt):
    source, store, _, bodies, _ = setup
    bodies[receipt] = body
    report = cards.generate_cards(source, store, [receipt])
    assert report["unsupported"] == 1 and report["failed"] == 0
    card = cards.query_cards(store)[0]["card"]
    assert card["status"] == "UNSUPPORTED"
    assert not any(f["status"] == "VERIFIED" for f in card["fields"])


def test_non_safety_legal_and_wrong_contract_title_are_not_auto_selected(setup):
    source, store, rows, _, _ = setup
    for i, title in ((0, "기타계약"), (2, "횡령ㆍ배임발생")):
        rows[i]["report_nm"] = title
        with closing(sqlite3.connect(source)) as conn, conn:
            conn.execute("UPDATE disclosures SET payload_json=? WHERE rcept_no=?", (
                json.dumps(rows[i]), rows[i]["rcept_no"],
            ))
    assert [row["rcept_no"] for row in cards.read_candidates(source)] == [RECEIPTS[1]]
    result = cards.generate_cards(source, store, [RECEIPTS[2]])
    assert result["unsupported"] == 1 and result["created"] == 0
    assert cards.query_cards(store)[0]["card"] is None


def test_failure_retry_and_old_success_preserved(setup, monkeypatch):
    source, store, _, _, _ = setup
    original = analysis._get

    def fail(_url):
        raise analysis.DocumentError("OFFICIAL_HTTP_503")

    monkeypatch.setattr(analysis, "_get", fail)
    assert cards.generate_cards(source, store, [RECEIPTS[0]])["failed"] == 1
    result = cards.query_cards(store, status="FAILED")[0]
    assert result["card"] is None and result["error_code"] == "OFFICIAL_HTTP_503"
    monkeypatch.setattr(analysis, "_get", original)
    assert cards.generate_cards(source, store, [RECEIPTS[0]])["created"] == 1
    old = cards.query_cards(store)[0]["card"]
    monkeypatch.setattr(analysis, "_get", fail)
    assert cards.generate_cards(source, store, [RECEIPTS[0]])["failed"] == 1
    result = cards.query_cards(store)[0]
    assert result["card"] == old and result["previous_result_only"]
    assert "이전 저장본" in cards.render_markdown(result)


def test_body_and_version_changes_retain_history(setup, monkeypatch):
    source, store, _, bodies, _ = setup
    cards.generate_cards(source, store, [RECEIPTS[0]])
    bodies[RECEIPTS[0]] = bodies[RECEIPTS[0]].replace(b"1,160,000,000,000", b"1,170,000,000,000")
    assert cards.generate_cards(source, store, [RECEIPTS[0]])["created"] == 1
    monkeypatch.setattr(cards, "EXTRACTOR_VERSION", "test-next-version")
    assert cards.generate_cards(source, store, [RECEIPTS[0]])["created"] == 1
    assert cards.query_cards(store)[0]["card"]["extractor_version"] == "test-next-version"
    with closing(sqlite3.connect(store)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


@pytest.mark.parametrize("tamper", ["archive", "xpath", "cells", "hash", "value", "url", "table"])
def test_corrupt_evidence_fails_read_only_query(setup, tamper):
    source, store, _, _, _ = setup
    cards.generate_cards(source, store, [RECEIPTS[1]])
    with closing(sqlite3.connect(store)) as conn, conn:
        payload = json.loads(conn.execute("SELECT payload_json FROM cards").fetchone()[0])
        field = payload["fields"][0]
        if tamper == "archive":
            conn.execute("UPDATE documents SET content=?", (b"damaged",))
        elif tamper == "value":
            field["raw_value"] = "invented"
        else:
            key = {"hash": "sha256"}.get(tamper, tamper)
            field["evidence"][0][key] = {"cells": ["wrong"], "table": 999}.get(tamper, "wrong")
        conn.execute("UPDATE cards SET payload_json=?", (json.dumps(payload),))
    before = store.read_bytes()
    with pytest.raises(cards.CardError):
        cards.query_cards(store)
    assert store.read_bytes() == before


def test_atomic_write_failure_does_not_leave_card_or_archive(setup):
    source, store, _, _, _ = setup
    cards.generate_cards(source, store, [RECEIPTS[0]])
    with closing(sqlite3.connect(store)) as conn, conn:
        conn.execute(
            "CREATE TRIGGER fail_attempt BEFORE INSERT ON attempts BEGIN "
            "SELECT RAISE(ABORT,'test failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        cards.generate_cards(source, store, [RECEIPTS[1]])
    with closing(sqlite3.connect(store)) as conn:
        for name in ("cards", "documents", "attempts"):
            assert conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] == 1


def test_two_writers_only_insert_one_card(setup):
    source, store, _, _, _ = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(
            lambda _: cards.generate_cards(source, store, [RECEIPTS[0]]), range(2),
        ))
    assert sum(report["created"] for report in reports) == 1
    assert sum(report["duplicates"] for report in reports) == 1


def test_correction_is_separate_without_assumed_original(setup):
    source, store, rows, _, entries = setup
    rows[0]["is_correction"] = True
    rows[0]["report_nm"] = "[기재정정]단일판매ㆍ공급계약체결"
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute("UPDATE disclosures SET payload_json=? WHERE rcept_no=?", (
            json.dumps(rows[0]), RECEIPTS[0],
        ))
    cards.generate_cards(source, store, [RECEIPTS[0]])
    card = cards.query_cards(store)[0]["card"]
    assert card["is_correction"] and card["original_receipt_no"] is None
    assert card["original_relationship"] == "NOT_ESTABLISHED"
    assert any("정정공시" in caution for caution in card["cautions"])


def test_identity_failure_is_not_a_fact_card(setup):
    source, store, _, _, entries = setup
    entries[RECEIPTS[0]] = entries[RECEIPTS[0]].replace(b"S-Oil", b"Other")
    report = cards.generate_cards(source, store, [RECEIPTS[0]])
    assert report["failed"] == 1 and report["created"] == 0
    assert cards.query_cards(store)[0]["card"] is None


def test_one_failure_does_not_block_other_receipts(setup):
    source, store, _, _, entries = setup
    entries[RECEIPTS[0]] = entries[RECEIPTS[0]].replace(b"S-Oil", b"Other")
    report = cards.generate_cards(source, store, RECEIPTS)
    assert (report["failed"], report["created"], report["verdict"]) == (1, 2, "FAIL")
    assert len(cards.query_cards(store)) == 3


def test_source_integrity_failure_prevents_any_card_output(setup):
    source, store, _, _, _ = setup
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute("DELETE FROM disclosures WHERE rcept_no=?", (RECEIPTS[0],))
    before = source.read_bytes()
    with pytest.raises(cards.CardError, match="SOURCE_NOT_CONSISTENT"):
        cards.generate_cards(source, store)
    assert not store.exists() and source.read_bytes() == before


def test_same_source_destination_and_existing_noncard_db_are_preserved(setup, tmp_path):
    source, store, _, _, _ = setup
    before = source.read_bytes()
    with pytest.raises(cards.CardError, match="CARD_STORE_MUST_BE_SEPARATE"):
        cards.generate_cards(source, source)
    assert source.read_bytes() == before
    with closing(sqlite3.connect(store)) as conn, conn:
        conn.execute("CREATE TABLE other(value TEXT)")
        conn.execute("INSERT INTO other VALUES ('keep')")
    before = store.read_bytes()
    with pytest.raises(cards.CardError, match="UNSUPPORTED_CARD_STORE"):
        cards.generate_cards(source, store)
    assert store.read_bytes() == before


def test_missing_archive_blocks_query_instead_of_falling_back(setup):
    source, store, _, _, _ = setup
    cards.generate_cards(source, store, [RECEIPTS[0]])
    with closing(sqlite3.connect(store)) as conn, conn:
        conn.execute("DELETE FROM documents")
    with pytest.raises(cards.CardError, match="ATTEMPT_CARD_MISSING"):
        cards.query_cards(store)


def test_cli_generate_and_query_without_network_or_writes_on_query(setup, capsys, monkeypatch):
    source, store, _, _, _ = setup
    args = ["--source", str(source), "--store", str(store), "--receipt", RECEIPTS[0]]
    assert generate_main(args) == 0
    assert json.loads(capsys.readouterr().out)["created"] == 1
    before = store.read_bytes()
    monkeypatch.setattr(analysis, "_get", lambda _url: pytest.fail("Query must not use network"))
    assert query_main(["--store", str(store), "--receipt", RECEIPTS[0], "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["query_count"] == 1
    assert query_main(["--store", str(store), "--ticker", "010950"]) == 0
    text = capsys.readouterr().out
    assert "확인된 사실" in text and "계약금액" in text and "1,160,000,000,000원" in text
    assert store.read_bytes() == before
    assert generate_main(["--source", str(source), "--store", str(source)]) == 1
    assert "CARD_STORE_MUST_BE_SEPARATE" in capsys.readouterr().out


@pytest.mark.parametrize("receipt", ["invalid", "20261020000000"])
def test_invalid_or_missing_receipt_does_not_create_output(setup, receipt):
    source, store, _, _, _ = setup
    with pytest.raises(cards.CardError):
        cards.generate_cards(source, store, [receipt])
    assert not store.exists()


def test_missing_query_store_is_not_created(tmp_path):
    store = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        cards.query_cards(store)
    assert not store.exists()
