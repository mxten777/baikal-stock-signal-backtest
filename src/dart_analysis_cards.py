"""Evidence-backed cards isolated from the operational disclosure store."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import ExitStack, closing
from datetime import date
from decimal import Decimal
from pathlib import Path

from lxml import etree, html

from scripts.dart_disclosure_query import collection_time
from src import dart_document_analysis as analysis

ROOT = Path(__file__).resolve().parents[1]
SOURCE_STORE = ROOT / "output" / "dart_disclosure_store" / "disclosures.sqlite3"
CARD_STORE = ROOT / "output" / "dart_analysis_cards" / "cards.sqlite3"
EXTRACTOR_VERSION = "cards-v1:" + hashlib.sha256(
    Path(analysis.__file__).read_bytes() + Path(__file__).read_bytes()
).hexdigest()
ARCHIVE_REANALYSIS_RECEIPTS = (
    "20261019800267", "20261019900272", "20261019900317", "20261019900334",
    "20261019900668", "20261020800596", "20261020900678",
)

GUIDANCE = {
    "CONTRACT": {
        "checklist": [
            "계약금액·납기 변경, 해지 조건 및 후속 정정공시 확인",
            "대금 결제 일정, 실제 이행 현황 및 매출 인식 시기 확인",
        ],
        "cautions": [
            "계약금액은 확정 이익이나 당기 현금 유입액이 아닙니다.",
            "매출액 대비 비율은 이익률이나 실적 증가율이 아닙니다.",
        ],
    },
    "SAFETY_LEGAL": {
        "checklist": [
            "작업중지의 구체적인 구역·공정·기간과 해제 여부 확인",
            "조사 결과, 재발방지대책의 실제 이행 및 후속 공시 확인",
        ],
        "cautions": [
            "부분 작업중지를 전체 사업장 또는 모회사 전체 중단으로 해석하지 않습니다.",
            "후속 대책의 예정은 완료가 아니며, 손실액·법적 책임을 추론하지 않습니다.",
        ],
    },
}
COMMON_CAUTIONS = [
    "공시 기재값의 추출 확인이며, 실제 이행이나 조사 결과의 독립 검증이 아닙니다.",
    "미확인 값은 보완하지 않으며, 주가 예측·수익률 추정·매매 추천을 제공하지 않습니다.",
    "마지막 본문 확인 시각 기준이며, 이후 변경·정정 여부는 별도 확인이 필요합니다.",
]
FIELD_LABELS = {
    "contract_amount": "계약금액", "revenue_ratio": "매출액 대비", "counterparty": "계약상대",
    "start_date": "계약 시작일", "end_date": "계약 종료일", "payment_terms": "대금지급 조건",
    "company_share": "회사 지분 조건", "vat": "부가가치세 조건", "period_terms": "기간 관련 조건",
    "accident_location": "사고 장소", "accident_description": "사고 내용",
    "fatalities": "사망자 수", "injuries": "부상자 수", "accident_date": "사고 발생일",
    "authority_report_date": "고용노동부 보고일", "follow_up_actions": "조사·후속 조치",
    "work_stoppage": "작업중지",
}


class CardError(ValueError):
    """Safe error codes, without remote response or credential contents."""


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _receipt(value: str) -> None:
    if re.fullmatch(r"[0-9]{14}", value) is None:
        raise CardError("INVALID_RECEIPT")


def profile_for(row: dict) -> str | None:
    title = re.sub(r"\s+|\[기재정정\]|\[첨부정정\]", "", row["report_nm"])
    category = row["classification"]["category"]
    if category == "CONTRACT" and re.fullmatch(
        r"단일판매[ㆍ·]공급계약체결(?:\(자율공시\))?", title,
    ):
        return "CONTRACT"
    if category == "SAFETY_LEGAL" and re.fullmatch(
        r"중대재해발생(?:\(종속회사의주요경영사항\))?", title,
    ):
        return "SAFETY_LEGAL"
    return None


def read_candidates(source: Path, receipts: list[str] | None = None) -> list[dict]:
    if receipts is not None:
        if not receipts:
            raise CardError("EMPTY_RECEIPTS")
        for receipt in receipts:
            _receipt(receipt)
    with closing(sqlite3.connect(
        source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5,
    )) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise CardError("UNSUPPORTED_SOURCE_VERSION")
        saved = connection.execute("SELECT summary_json FROM last_success WHERE id=1").fetchone()
        if saved is None:
            raise CardError("SOURCE_SUCCESS_MISSING")
        summary = json.loads(saved[0])
        rows = connection.execute(
            "SELECT rcept_no,payload_json FROM disclosures ORDER BY rcept_no"
        ).fetchall()
        if summary["verdict"] != "PASS" or summary["total_count"] != len(rows):
            raise CardError("SOURCE_NOT_CONSISTENT")
        if collection_time(summary)["collection_time_status"] != "VERIFIED_STORED_SOURCE":
            raise CardError("SOURCE_COLLECTION_NOT_VERIFIED")
    selected = []
    found = set()
    for receipt, payload in rows:
        if receipts is not None and receipt not in receipts:
            continue
        row = json.loads(payload)
        _receipt(receipt)
        if (row["rcept_no"] != receipt or type(row["is_correction"]) is not bool
                or row["original_url"] != analysis.BASE + "/dsaf001/main.do?rcpNo=" + receipt
                or not isinstance(row["name"], str) or not row["name"].strip()
                or re.fullmatch(r"[0-9A-Z]{6}", row["ticker"]) is None
                or date.fromisoformat(row["rcept_dt"]).isoformat() != row["rcept_dt"]):
            raise CardError("INVALID_SOURCE_DISCLOSURE")
        found.add(receipt)
        if receipts is not None or profile_for(row) is not None:
            selected.append(row)
    if receipts is not None and found != set(receipts):
        raise CardError("RECEIPT_NOT_FOUND")
    return selected


def _evidence_tree(content: bytes, digest: str):
    if hashlib.sha256(content).hexdigest() != digest:
        raise CardError("ARCHIVE_HASH_MISMATCH")
    try:
        return html.fromstring(content, parser=html.HTMLParser(no_network=True))
    except (etree.ParserError, UnicodeError, LookupError):
        raise CardError("INVALID_ARCHIVED_HTML") from None


def verify_evidence(card: dict, content: bytes) -> int:
    """Re-resolve every evidence row in the exact archived body, including unknowns."""
    if (card["document"]["sha256"] != card["body_sha256"]
            or card["original_url"] != analysis.BASE + "/dsaf001/main.do?rcpNo=" + card["rcept_no"]
            or not card["document"]["url"].startswith(
                analysis.BASE + "/report/viewer.do?rcpNo=" + card["rcept_no"] + "&"
            )):
        raise CardError("CARD_DOCUMENT_IDENTITY_MISMATCH")
    tree = _evidence_tree(content, card["body_sha256"])
    count = 0
    for field in card["fields"] + card["conditions"]:
        if field["status"] not in ("VERIFIED", "UNCONFIRMED", "UNSUPPORTED"):
            raise CardError("INVALID_FIELD_STATUS")
        if field["status"] == "VERIFIED":
            if field["raw_value"] in (None, "", "-", "미정") or not field["evidence"]:
                raise CardError("VERIFIED_WITHOUT_VALUE_OR_EVIDENCE")
            count += 1
        for evidence in field["evidence"]:
            if (evidence["sha256"] != card["body_sha256"]
                    or evidence["url"] != card["document"]["url"]
                    or evidence["checked_at"] != card["document"]["checked_at"]):
                raise CardError("FIELD_DOCUMENT_MISMATCH")
            try:
                nodes = tree.xpath(evidence["xpath"])
            except etree.XPathError:
                raise CardError("INVALID_EVIDENCE_XPATH") from None
            if len(nodes) != 1 or not isinstance(nodes[0], etree._Element):
                raise CardError("EVIDENCE_ROW_NOT_FOUND")
            cells = [" ".join(cell.text_content().split()) for cell in nodes[0].xpath("./td|./th")]
            tables = tree.xpath("//table")
            ancestors = nodes[0].xpath("ancestor::table[1]")
            if (cells != evidence["cells"] or not ancestors
                    or evidence["table"] < 1 or evidence["table"] > len(tables)
                    or tables[evidence["table"] - 1] is not ancestors[0]):
                raise CardError("EVIDENCE_CELLS_MISMATCH")
            if field["status"] == "VERIFIED":
                raw = field["raw_value"]
                if not any(raw in cell for cell in cells):
                    raise CardError("VALUE_NOT_IN_EVIDENCE")
    return count


def _conservative_contract_fields(fields: list[dict]) -> None:
    # The legacy parser deliberately retains raw strings; do not promote ambiguity in cards.
    for field in fields:
        if field["status"] != "VERIFIED":
            continue
        value = field["raw_value"]
        ambiguous = re.search(r"미확인|미상|불명|확인\s*중|협의\s*중|추정", value) is not None
        if field["field"] in ("start_date", "end_date"):
            try:
                ambiguous |= date.fromisoformat(value).isoformat() != value
            except ValueError:
                ambiguous = True
        if field["numeric_value"] is not None:
            ambiguous |= Decimal(field["numeric_value"]) < 0
        if ambiguous:
            field.update(
                status="UNCONFIRMED", numeric_value=None, verification_method="NONE",
                reason="AMBIGUOUS_OR_INVALID_CONTRACT_VALUE; no inference",
            )


def build_card(row: dict) -> tuple[dict, bytes]:
    if profile_for(row) is None:
        raise CardError("UNSUPPORTED_TYPE")
    entry, _ = analysis._get(row["original_url"])
    body, evidence = analysis._get(analysis.document_url(entry, row))
    return _card_from_body(row, body, evidence), body


def _card_from_body(row: dict, body: bytes, evidence: dict) -> dict:
    profile = profile_for(row)
    if profile is None:
        raise CardError("UNSUPPORTED_TYPE")
    _evidence_tree(body, evidence["sha256"])
    try:
        result = analysis.extract_fields(body, row, evidence)
    except analysis.DocumentError as exc:
        if str(exc) != "DOCUMENT_TABLES_UNAVAILABLE":
            raise
        result = {"fields": [], "extraction_status": "UNSUPPORTED"}
    fields = result["fields"]
    if profile == "CONTRACT":
        _conservative_contract_fields(fields)
    recognized = any(field["evidence"] for field in fields)
    unsupported = result.get("extraction_status") == "UNSUPPORTED" or not recognized
    status = "UNSUPPORTED" if unsupported else (
        "COMPLETE" if all(field["status"] == "VERIFIED" for field in fields) else "PARTIAL"
    )
    card = {
        "schema_version": 1, "template_version": 1,
        **{key: row[key] for key in (
            "rcept_no", "ticker", "name", "report_nm", "rcept_dt", "original_url", "is_correction",
        )},
        "profile": profile, "status": status, "generated_at": analysis.now_iso(),
        "extractor_version": EXTRACTOR_VERSION, "body_sha256": evidence["sha256"],
        "document": evidence, "fields": fields,
        "conditions": result.get("contract_conditions", []),
        "checklist": GUIDANCE[profile]["checklist"],
        "cautions": GUIDANCE[profile]["cautions"] + COMMON_CAUTIONS,
        "original_receipt_no": None,
        "original_relationship": "NOT_ESTABLISHED" if row["is_correction"] else "NOT_APPLICABLE",
        "scope_note": "Named table fields only; not a full substantive review.",
    }
    if row["is_correction"]:
        card["cautions"] += ["정정공시입니다. 원공시 연결·자동 대체·독립 비교는 수행하지 않았습니다."]
    card["verified_evidence_count"] = verify_evidence(card, body)
    return card


def reanalyze_archived(
    store: Path = CARD_STORE, *, execute: bool = False, receipts: list[str] | None = None,
) -> dict:
    """Analyze the fixed pilot batch without entering any download/generation path."""
    if receipts is not None and (
        len(receipts) != len(ARCHIVE_REANALYSIS_RECEIPTS)
        or set(receipts) != set(ARCHIVE_REANALYSIS_RECEIPTS)
    ):
        raise CardError("EXACT_ARCHIVE_BATCH_REQUIRED")
    if store.resolve() == SOURCE_STORE.resolve() or (
        store.exists() and SOURCE_STORE.exists() and store.samefile(SOURCE_STORE)
    ):
        raise CardError("CARD_STORE_MUST_BE_SEPARATE")
    # mode=rw refuses missing stores; preview must never create a DB or a lease.
    mode = "rw" if execute else "ro"
    with closing(sqlite3.connect(
        store.resolve().as_uri() + "?mode=" + mode, uri=True, timeout=0,
    )) as connection:
        if not execute:
            connection.execute("PRAGMA query_only=ON")
        with ExitStack() as resources:
            try:
                if execute:
                    lock = resources.enter_context(closing(_archive_lock(store)))
                    lock.execute("BEGIN IMMEDIATE")
                with connection:
                    connection.execute("BEGIN IMMEDIATE" if execute else "BEGIN")
                    if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                        raise CardError("UNSUPPORTED_CARD_STORE")
                    prepared = []
                    results = []
                    for receipt in ARCHIVE_REANALYSIS_RECEIPTS:
                        saved = connection.execute(
                            "SELECT receipt,body_sha256,extractor_version,payload_json FROM cards "
                            "WHERE receipt=? ORDER BY json_extract(payload_json,'$.generated_at') "
                            "DESC,rowid DESC LIMIT 1", (receipt,),
                        ).fetchone()
                        if saved is None:
                            raise CardError("ARCHIVED_CARD_MISSING")
                        old = json.loads(saved[3])
                        if (old["rcept_no"], old["body_sha256"], old["extractor_version"]) != saved[:3]:
                            raise CardError("CARD_IDENTITY_MISMATCH")
                        _receipt(old["rcept_no"])
                        if (re.fullmatch(r"[0-9A-Z]{6}", old["ticker"]) is None
                                or type(old["is_correction"]) is not bool
                                or date.fromisoformat(old["rcept_dt"]).isoformat() != old["rcept_dt"]):
                            raise CardError("CARD_IDENTITY_MISMATCH")
                        archived = connection.execute(
                            "SELECT content FROM documents WHERE sha256=?", (old["body_sha256"],),
                        ).fetchone()
                        if archived is None:
                            raise CardError("ARCHIVED_BODY_MISSING")
                        body = archived[0]
                        verify_evidence(old, body)
                        row = {**old, "classification": {"category": old["profile"]}}
                        if profile_for(row) != old["profile"]:
                            raise CardError("UNSUPPORTED_TYPE")
                        card = _card_from_body(row, body, old["document"])
                        if card["status"] not in ("COMPLETE", "PARTIAL"):
                            raise CardError("ARCHIVE_REANALYSIS_NOT_SUPPORTED")
                        card["generation_mode"] = "ARCHIVED_REANALYSIS"
                        existing = connection.execute(
                            "SELECT payload_json FROM cards WHERE receipt=? AND body_sha256=? "
                            "AND extractor_version=?",
                            (receipt, card["body_sha256"], EXTRACTOR_VERSION),
                        ).fetchone()
                        duplicate = existing is not None
                        if existing is not None:
                            previous = json.loads(existing[0])
                            if (previous["rcept_no"], previous["body_sha256"],
                                    previous["extractor_version"]) != (
                                        receipt, card["body_sha256"], EXTRACTOR_VERSION,
                                    ):
                                raise CardError("CARD_IDENTITY_MISMATCH")
                            verify_evidence(previous, body)
                            if previous["fields"] != card["fields"] or previous["conditions"] != card["conditions"]:
                                raise CardError("EXISTING_VERSION_CONFLICT")
                            if connection.execute(
                                "SELECT 1 FROM attempts WHERE receipt=? AND body_sha256=? "
                                "AND extractor_version=? AND status=?",
                                (receipt, card["body_sha256"], EXTRACTOR_VERSION, previous["status"]),
                            ).fetchone() is None:
                                raise CardError("EXISTING_VERSION_ATTEMPT_MISSING")
                        prepared.append((card, duplicate))
                        results.append({
                            "receipt": receipt, "name": card["name"], "status": card["status"],
                            "body_sha256": card["body_sha256"], "extractor_version": EXTRACTOR_VERSION,
                            "previous_extractor_version": old["extractor_version"],
                            "original_checked_at": card["document"]["checked_at"],
                            "analyzed_at": card["generated_at"], "duplicate": duplicate,
                            "verified_evidence_count": card["verified_evidence_count"],
                        })
                    if execute:
                        for card, duplicate in prepared:
                            if duplicate:
                                continue
                            connection.execute("INSERT INTO cards VALUES (?,?,?,?)", (
                                card["rcept_no"], card["body_sha256"], EXTRACTOR_VERSION, _dump(card),
                            ))
                            connection.execute(
                                "INSERT INTO attempts(receipt,ticker,status,attempted_at,error_code,"
                                "body_sha256,extractor_version) VALUES (?,?,?,?,?,?,?)",
                                (card["rcept_no"], card["ticker"], card["status"], card["generated_at"],
                                 None, card["body_sha256"], EXTRACTOR_VERSION),
                            )
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode == sqlite3.SQLITE_BUSY:
                    raise CardError("ALREADY_RUNNING") from exc
                raise
    return {
        "verdict": "PASS" if execute else "PREVIEW", "mode": "ARCHIVED_REANALYSIS",
        "execute": execute, "http_request_count": 0, "extractor_version": EXTRACTOR_VERSION,
        "selected_count": len(results), "created": sum(not d for _, d in prepared) if execute else 0,
        "attempts_created": sum(not d for _, d in prepared) if execute else 0,
        "complete_count": sum(c["status"] == "COMPLETE" for c, _ in prepared),
        "partial_count": sum(c["status"] == "PARTIAL" for c, _ in prepared),
        "duplicates": sum(d for _, d in prepared), "results": results,
    }


def _archive_lock(store: Path) -> sqlite3.Connection:
    directory = store.parent / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(directory / "execution.lock.sqlite3", timeout=0)


def _open_store(path: Path, source: Path) -> sqlite3.Connection:
    if (path.resolve() == source.resolve()
            or (path.exists() and source.exists() and path.samefile(source))
            or path.resolve() == SOURCE_STORE.resolve()
            or (path.exists() and SOURCE_STORE.exists() and path.samefile(SOURCE_STORE))):
        raise CardError("CARD_STORE_MUST_BE_SEPARATE")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if version == 0 and not tables:
                connection.execute(
                    "CREATE TABLE documents (sha256 TEXT PRIMARY KEY,content BLOB NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE cards (receipt TEXT NOT NULL,body_sha256 TEXT NOT NULL,"
                    "extractor_version TEXT NOT NULL,payload_json TEXT NOT NULL,"
                    "PRIMARY KEY(receipt,body_sha256,extractor_version))"
                )
                connection.execute(
                    "CREATE TABLE attempts (id INTEGER PRIMARY KEY,receipt TEXT NOT NULL,"
                    "ticker TEXT NOT NULL,status TEXT NOT NULL,attempted_at TEXT NOT NULL,"
                    "error_code TEXT,body_sha256 TEXT,extractor_version TEXT)"
                )
                connection.execute("PRAGMA user_version=1")
            elif version != 1 or {item[0] for item in tables} != {"documents", "cards", "attempts"}:
                raise CardError("UNSUPPORTED_CARD_STORE")
    except (sqlite3.Error, CardError):
        connection.close()
        raise
    return connection


def generate_cards(
    source: Path = SOURCE_STORE, store: Path = CARD_STORE, receipts: list[str] | None = None,
) -> dict:
    rows = read_candidates(source, receipts)
    report = {"created": 0, "duplicates": 0, "failed": 0, "unsupported": 0, "results": []}
    with closing(_open_store(store, source)) as connection:
        for row in rows:
            card = body = None
            code = None
            try:
                card, body = build_card(row)
                status = card["status"]
            except (analysis.DocumentError, CardError, UnicodeError) as exc:
                code = str(exc) if isinstance(exc, (analysis.DocumentError, CardError)) else "INVALID_DOCUMENT_ENCODING"
                status = "UNSUPPORTED" if code == "UNSUPPORTED_TYPE" else "FAILED"
            digest = card["body_sha256"] if card else None
            version = card["extractor_version"] if card else None
            duplicate = False
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if card is not None:
                    archived = connection.execute(
                        "SELECT content FROM documents WHERE sha256=?", (digest,),
                    ).fetchone()
                    if archived is not None and archived[0] != body:
                        raise CardError("ARCHIVE_CONTENT_CONFLICT")
                    connection.execute(
                        "INSERT OR IGNORE INTO documents VALUES (?,?)", (digest, body),
                    )
                    duplicate = connection.execute(
                        "SELECT 1 FROM cards WHERE receipt=? AND body_sha256=? AND extractor_version=?",
                        (row["rcept_no"], digest, version),
                    ).fetchone() is not None
                    if not duplicate:
                        connection.execute(
                            "INSERT INTO cards VALUES (?,?,?,?)",
                            (row["rcept_no"], digest, version, _dump(card)),
                        )
                connection.execute(
                    "INSERT INTO attempts(receipt,ticker,status,attempted_at,error_code,"
                    "body_sha256,extractor_version) VALUES (?,?,?,?,?,?,?)",
                    (row["rcept_no"], row["ticker"], status, analysis.now_iso(), code, digest, version),
                )
            report["duplicates"] += int(duplicate)
            report["created"] += int(card is not None and not duplicate)
            report["failed"] += int(status == "FAILED")
            report["unsupported"] += int(status == "UNSUPPORTED")
            report["results"].append({
                "receipt": row["rcept_no"], "status": status, "duplicate": duplicate,
                "error_code": code, "body_sha256": digest, "extractor_version": version,
                "verified_evidence_count": card["verified_evidence_count"] if card else 0,
            })
    return {"verdict": "FAIL" if report["failed"] else "PASS", **report}


def query_cards(
    store: Path = CARD_STORE, *, receipt: str | None = None,
    ticker: str | None = None, status: str | None = None, limit: int = 20,
) -> list[dict]:
    if receipt is not None:
        _receipt(receipt)
    if ticker is not None and re.fullmatch(r"[0-9A-Z]{6}", ticker) is None:
        raise CardError("INVALID_TICKER")
    if status is not None and status not in ("COMPLETE", "PARTIAL", "UNSUPPORTED", "FAILED"):
        raise CardError("INVALID_STATUS")
    if type(limit) is not int or limit < 1:
        raise CardError("INVALID_LIMIT")
    with closing(sqlite3.connect(
        store.resolve().as_uri() + "?mode=ro", uri=True, timeout=5,
    )) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise CardError("UNSUPPORTED_CARD_STORE")
        attempts = connection.execute(
            "SELECT receipt,ticker,status,attempted_at,error_code,body_sha256,extractor_version "
            "FROM attempts WHERE id IN (SELECT MAX(id) FROM attempts GROUP BY receipt)"
            " AND (? IS NULL OR receipt=?) AND (? IS NULL OR ticker=?)"
            " AND (? IS NULL OR status=?) ORDER BY receipt DESC LIMIT ?",
            (receipt, receipt, ticker, ticker, status, status, limit),
        ).fetchall()
        results = []
        for r, t, state, stamp, error, digest, version in attempts:
            saved = connection.execute(
                "SELECT c.payload_json,d.content FROM cards c JOIN documents d "
                "ON d.sha256=c.body_sha256 WHERE c.receipt=?"
                " ORDER BY (c.body_sha256=? AND c.extractor_version=?) DESC,"
                " json_extract(c.payload_json,'$.generated_at') DESC,c.rowid DESC LIMIT 1",
                (r, digest, version),
            ).fetchone()
            card = json.loads(saved[0]) if saved else None
            if digest is not None and (
                card is None or card["body_sha256"] != digest or card["extractor_version"] != version
            ):
                raise CardError("ATTEMPT_CARD_MISSING")
            if card is not None:
                verify_evidence(card, saved[1])
                if card["rcept_no"] != r or card["ticker"] != t:
                    raise CardError("CARD_IDENTITY_MISMATCH")
            results.append({
                "receipt": r, "latest_attempt_status": state, "last_attempt_at": stamp,
                "error_code": error, "card": card,
                "previous_result_only": bool(card and (
                    digest != card["body_sha256"] or version != card["extractor_version"]
                )),
            })
    return results


def render_markdown(result: dict) -> str:
    card = result["card"]
    lines = [
        f"## {result['receipt']}",
        f"- 최신 시도: {result['latest_attempt_status']} / {result['last_attempt_at']}",
    ]
    if result["error_code"]:
        lines.append(f"- 오류: {result['error_code']}")
    if card is None:
        return "\n".join(lines + ["- 생성된 사실 카드 없음"])
    if result["previous_result_only"]:
        lines.append("- 주의: 최신 시도 결과가 아닌 이전 저장본입니다.")
    lines += [
        f"- 기업: {card['name']}", f"- 공시: {card['report_nm'].strip()} / {card['rcept_dt']}",
        f"- 카드 상태: {card['status']}",
        f"- 본문 확인: {card['document']['checked_at']}",
        f"- 분석 시각: {card['generated_at']}",
        f"- [DART 원문]({card['original_url']})",
        f"- [분석 본문]({card['document']['url']})",
        f"- 본문 SHA-256: `{card['body_sha256']}`",
        f"- 추출기: `{card['extractor_version']}`", "### 확인된 사실",
    ]
    fields = card["fields"] + card["conditions"]
    for field in fields:
        if field["status"] == "VERIFIED":
            label = FIELD_LABELS[field["field"]]
            unit = {"KRW": "원", "%": "%", "persons": "명"}.get(field.get("unit"), "")
            lines.append(f"- {label} ({field['field']}): {field['raw_value']}{unit}")
            if field.get("scope") is not None:
                lines.append(f"  - 상태/범위: {field['normalized_value']} / {field['scope']}")
    lines.append("### 미확인·미지원 필드")
    for field in fields:
        if field["status"] != "VERIFIED":
            lines.append(
                f"- {FIELD_LABELS[field['field']]} ({field['field']}): "
                f"{field['status']} / {field['reason']}"
            )
    lines.append("### 필드별 원문 근거")
    for field in fields:
        for evidence in field["evidence"]:
            lines.append(
                f"- {field['field']} ({field['status']}): `{evidence['xpath']}` / "
                f"셀: {_dump(evidence['cells'])} / [본문]({evidence['url']}) / "
                f"SHA-256 `{evidence['sha256']}`"
            )
    lines += ["### 추가 확인 체크리스트"] + ["- " + text for text in card["checklist"]]
    lines += ["### 해석상 주의사항"] + ["- " + text for text in card["cautions"]]
    return "\n".join(lines)
