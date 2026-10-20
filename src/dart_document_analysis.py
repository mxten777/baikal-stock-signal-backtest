"""Five-document factual extraction with official evidence, never trading signals."""

from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

import requests
from lxml import etree, html

from src.expanded_shadow_ops import ExpandedShadowPaths

SELECTION = (
    ("20261020800241", "CONTRACT", "판매·공급계약 금액과 조건"),
    ("20261020800175", "EARNINGS", "연결 잠정실적과 보고기간"),
    ("20261019900366", "CAPITAL_FINANCING", "유상증자 청약 결과"),
    ("20261019000037", "CAPITAL_FINANCING", "정정표와 자금조달 조건 변경"),
    ("20261019800137", "RESTRUCTURING", "영업양도 금액과 일정"),
)
BASE = "https://dart.fss.or.kr"
FIELDS = {
    "CONTRACT": (
        ("contract_amount", r"^계약금액\(원\)$", -1, "KRW"),
        ("revenue_ratio", r"^매출액대비\(%\)$", -1, "%"),
        ("counterparty", r"^\d+\.계약상대$", -1, None),
        ("start_date", r"^시작일$", -1, None),
        ("end_date", r"^종료일$", -1, None),
        ("payment_terms", r"^대금지급조건등$", -1, None),
    ),
    "EARNINGS": (
        ("reporting_period", r"^당기실적$", None, None),
        ("revenue_current", r"^매출액$", 2, "백만원"),
        ("operating_income_current", r"^영업이익$", 2, "백만원"),
        ("net_income_current", r"^당기순이익$", 2, "백만원"),
    ),
    "SUBSCRIPTION": (
        ("security_type", r"^\d+\.증권의종류$", -1, None),
        ("issuance_method", r"^\d+\.발행방법$", -1, None),
        ("planned_shares", r"^발행예정주식수\(주\)$", -1, "주"),
        ("subscribed_shares", r"^해당청약주식수\(주\)$", -1, "주"),
        ("subscription_ratio", r"^청약률\(%\)$", -1, "%"),
    ),
    "BOND_CORRECTION": (
        ("bond_face_amount", r"^\d+\.사채의권면\(전자등록\)총액\(원\)$", -1, "KRW"),
        ("debt_repayment_funding", r"^채무상환자금\(원\)$", -1, "KRW"),
        ("coupon_rate", r"^표면이자율\(%\)$", -1, "%"),
        ("subscription_date", r"^\d+\.청약일$", -1, None),
        ("payment_date", r"^\d+\.납입일$", -1, None),
        ("call_option", r"^\d+-\d+\.옵션에관한사항$", -1, None),
    ),
    "RESTRUCTURING": (
        ("business_transferred", r"^\d+\.양도영업$", -1, None),
        ("transfer_amount", r"^\d+\.양도가액\(원\)$", -1, "KRW"),
        ("counterparty", r"^회사명\(성명\)$", -1, None),
        ("effective_date", r"^양도기준일$", -1, None),
        ("payment_terms", r"^\d+\.양도대금지급$", -1, None),
    ),
}


class DocumentError(ValueError):
    """Safe local code, without request URLs, secrets or exception messages."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def select_documents(report: object) -> list[dict]:
    if not isinstance(report, dict) or report.get("verdict") != "PASS":
        raise DocumentError("CLASSIFICATION_NOT_PASS")
    rows = report.get("disclosures")
    if not isinstance(rows, list):
        raise DocumentError("INVALID_CLASSIFICATION")
    if any(not isinstance(row, dict) or not isinstance(row.get("rcept_no"), str)
           for row in rows):
        raise DocumentError("INVALID_CLASSIFICATION")
    indexed = {row["rcept_no"]: row for row in rows}
    if len(indexed) != len(rows):
        raise DocumentError("DUPLICATE_RECEIPT")
    selected = []
    for receipt, category, reason in SELECTION:
        row = indexed.get(receipt)
        if (row is None or not isinstance(row.get("classification"), dict)
                or row["classification"].get("importance") != "HIGH"
                or row["classification"].get("category") != category):
            raise DocumentError("SELECTION_NOT_AVAILABLE")
        if (not all(isinstance(row.get(key), str) and row[key].strip()
                    for key in ("name", "ticker", "report_nm", "rcept_dt"))
                or not re.fullmatch(r"[0-9A-Z]{6}", row["ticker"])
                or type(row.get("is_correction")) is not bool
                or row.get("original_url") != BASE + "/dsaf001/main.do?rcpNo=" + receipt):
            raise DocumentError("INVALID_SELECTED_METADATA")
        try:
            if date.fromisoformat(row["rcept_dt"]).isoformat() != row["rcept_dt"]:
                raise ValueError
        except ValueError:
            raise DocumentError("INVALID_SELECTED_DATE") from None
        selected.append({**row, "selection_reason": reason})
    return selected


def _get(url: str) -> tuple[bytes, dict]:
    checked_at = now_iso()
    try:
        response = requests.get(url, timeout=(5, 20), allow_redirects=False)
    except requests.RequestException:
        raise DocumentError("OFFICIAL_REQUEST_FAILED") from None
    if response.status_code != 200:
        raise DocumentError(f"OFFICIAL_HTTP_{response.status_code}")
    if len(response.content) > 5_000_000:
        raise DocumentError("DOCUMENT_SIZE_LIMIT")
    return response.content, {
        "url": url, "checked_at": checked_at,
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "http_status": response.status_code,
    }


def document_url(entry: bytes, row: dict) -> str:
    text = entry.decode("utf-8", errors="strict")
    tree = html.fromstring(text)
    title = " ".join(tree.xpath("//title/text()"))
    normalized = lambda value: re.sub(r"\s+|\[기재정정\]|\[첨부정정\]", "", value)
    if (row["name"] not in title or row["rcept_dt"].replace("-", ".") not in title
            or normalized(row["report_nm"]) not in normalized(title)):
        raise DocumentError("VIEWER_IDENTITY_NOT_VERIFIED")
    # Same literal full-document entry used by OpenDartReader's sub_docs helper.
    # Do not execute remote JavaScript or follow attachments/other receipts.
    pattern = (
        r"""viewDoc\(\s*['"](\d{14})['"]\s*,\s*['"](\d+)['"]"""
        r"""\s*,\s*['"]0['"]\s*,\s*['"]0['"]\s*,\s*['"]0['"]"""
        r"""\s*,\s*['"](HTML|dart[0-9.]+\.xsd)['"]"""
    )
    matches = {(receipt, doc, dtd) for receipt, doc, dtd in re.findall(pattern, text)
               if receipt == row["rcept_no"]}
    if len(matches) != 1:
        raise DocumentError("FULL_DOCUMENT_LINK_UNCONFIRMED")
    receipt, doc, dtd = matches.pop()
    return BASE + "/report/viewer.do?" + urlencode({
        "rcpNo": receipt, "dcmNo": doc, "eleId": "0", "offset": "0",
        "length": "0", "dtd": dtd,
    })


def _earnings_unit(rows: list[dict], table_no: int, evidence: dict) -> tuple[str | None, list[dict]]:
    declarations = [
        item for item in rows if item["table"] == table_no
        and any("단위" in cell for cell in item["cells"])
    ]
    units = []
    for item in declarations:
        for cell in item["cells"]:
            if "단위" not in cell:
                continue
            declaration = cell[1:-1].strip() if (
                (cell.startswith("(") and cell.endswith(")"))
                or (cell.startswith("[") and cell.endswith("]"))
            ) else cell
            match = re.fullmatch(
                r"단위\s*:\s*(원|천원|백만원|억원|조원)\s*(?:,\s*%)?", declaration,
            )
            if not match:
                return None, [{**evidence, **entry} for entry in declarations]
            units.append(match.group(1))
    unit = units[0] if units and len(set(units)) == 1 else None
    return unit, [{**evidence, **entry} for entry in declarations]


def _contract_conditions(rows: list[dict], evidence: dict) -> list[dict]:
    patterns = (
        ("company_share", r"당사분|당사지분",
         r"상기\s*2\.\s*계약금액은\s*당사분\s*(?:100|[0-9]{1,2}(?:\.[0-9]+)?)%\s*"
         r"(?:입니다|임)\.?(?:\s*\(VAT\s*(?:제외|포함)\))?"),
        ("vat", r"VAT|부가가치세",
         r"(?:상기\s*2\.\s*계약금액은\s*당사분\s*(?:100|[0-9]{1,2}(?:\.[0-9]+)?)%\s*"
         r"(?:입니다|임)\.?\s*\(VAT\s*(?:제외|포함)\)"
         r"|상기\s*2\.\s*(?:의\s*)?계약\s*금액은\s*부가가치세를\s*(?:제외|포함)한\s*금액임\.?)"),
        ("period_terms", r"계약기간|공사기간|실착공",
         r"(?:계약기간은\s*)?실착공\s*후\s*[1-9][0-9]*개월(?:입니다|임)?\.?"
         r"(?:\s*\(철거공사기간\s*제외\))?"),
    )
    footnotes = []
    active_tables = set()
    for item in rows:
        if any(re.match(r"^9\.\s*기타\s*투자판단", cell) for cell in item["cells"]):
            active_tables.add(item["table"])
            continue
        if any("※ 관련공시" in cell for cell in item["cells"]):
            active_tables.discard(item["table"])
        if item["table"] in active_tables:
            footnotes.append(item)
    conditions = []
    for field, marker, pattern in patterns:
        candidates = []
        for item in footnotes:
            for cell in item["cells"]:
                # Split bullet text, not decimal points or parenthesized qualifications.
                for clause in re.split(r"(?:^|\s)-\s+", cell):
                    if re.search(marker, clause):
                        candidates.append((clause.strip(), item))
        accepted = [
            (text, item) for text, item in candidates if re.fullmatch(pattern, text)
        ]
        qualifications = [
            text for text, _ in candidates if field == "period_terms"
            and re.fullmatch(
                r"공사계약서에서\s*규정하는\s*조건\s*등에\s*따라\s*공사기간\s*및\s*계약금액\s*등은\s*"
                r"변동될\s*수\s*있습니다\.", text,
            )
        ]
        verified = len(accepted) == 1 and len(candidates) == 1 + len(qualifications)
        conditions.append({
            "field": field,
            "status": "VERIFIED" if verified else "UNCONFIRMED",
            "verification_method": "AUTO" if verified else "NONE",
            "raw_value": accepted[0][0] if verified else None,
            "qualifications": qualifications,
            "reason": None if verified else "MISSING_OR_AMBIGUOUS_OR_UNSUPPORTED_CONDITION",
            "evidence": [{**evidence, **item} for _, item in candidates],
        })
    return conditions


def extract_fields(content: bytes, row: dict, evidence: dict) -> dict:
    try:
        # Exchange filings declare EUC-KR, whereas DART XML views use UTF-8.
        tree = html.fromstring(content, parser=html.HTMLParser(no_network=True))
    except (etree.ParserError, UnicodeError, LookupError):
        raise DocumentError("INVALID_DOCUMENT_HTML") from None
    tables = tree.xpath("//table")
    if not tables:
        raise DocumentError("DOCUMENT_TABLES_UNAVAILABLE")
    table_rows = []
    for table_no, table in enumerate(tables, 1):
        for tr in table.xpath(".//tr"):
            if tr.xpath("ancestor::table[1]")[0] is not table:
                continue
            cells = [" ".join(cell.text_content().split()) for cell in tr.xpath("./td|./th")]
            if cells:
                table_rows.append({
                    "cells": cells, "table": table_no,
                    "xpath": tree.getroottree().getpath(tr),
                })
    fields = []
    category = row["classification"]["category"]
    profile = category
    if category == "CAPITAL_FINANCING":
        profile = "BOND_CORRECTION" if "조건부자본증권" in row["report_nm"] else "SUBSCRIPTION"
    # Exclude correction before/after columns from current-value extraction.
    correction_tables = {
        item["table"] for item in table_rows
        if [re.sub(r"\s+", "", x) for x in item["cells"]]
        == ["항목", "정정사유", "정정전", "정정후"]
    }
    context = [item for item in table_rows if (
        any("단위" in cell or "실적" in cell for cell in item["cells"])
        and not any(len(cell) > 200 for cell in item["cells"])
    )][:12]
    for field, pattern, index, unit in FIELDS[profile]:
        hits = []
        for item in table_rows:
            if item["table"] in correction_tables:
                continue
            labels = [i for i, cell in enumerate(item["cells"])
                      if re.search(pattern, re.sub(r"\s+", "", cell))]
            if not labels:
                continue
            cells = item["cells"]
            if field == "reporting_period" and not (
                labels[0] == 0 and len(cells) == 4 and cells[2] == "~"
            ):
                continue
            if index is None:
                value = " ".join(cells[labels[0] + 1:])
            elif index == -1:
                # A multi-pair row (e.g. cumulative shares / subscription ratio)
                # requires the cell immediately after its matching label.
                value = cells[labels[0] + 1] if labels[0] + 1 < len(cells) else ""
            else:
                if len(cells) < 3 or cells[1] != "당해실적" or index >= len(cells):
                    continue
                value = cells[index]
            hits.append((value, item))
        status = "VERIFIED" if len(hits) == 1 and hits[0][0] not in {"", "-", "미정"} else "UNCONFIRMED"
        value = hits[0][0] if len(hits) == 1 else None
        numeric = None
        unit_evidence = []
        unit_reason = None
        if category == "EARNINGS" and unit:
            unit, unit_evidence = _earnings_unit(
                table_rows, hits[0][1]["table"], evidence,
            ) if len(hits) == 1 else (None, [])
            if unit is None:
                status = "UNCONFIRMED"
                unit_reason = "MISSING_OR_AMBIGUOUS_OR_UNSUPPORTED_TABLE_UNIT"
        if status == "VERIFIED" and unit and value is not None:
            try:
                number = Decimal(value.replace(",", ""))
                if not number.is_finite():
                    raise InvalidOperation
                numeric = str(number)
            except InvalidOperation:
                status = "UNCONFIRMED"
        fields.append({
            "field": field, "status": status, "raw_value": value,
            "numeric_value": numeric, "unit": unit,
            "verification_method": "AUTO" if status == "VERIFIED" else "NONE",
            "unit_evidence": unit_evidence,
            "reason": None if status == "VERIFIED" else (
                unit_reason or "MISSING_OR_AMBIGUOUS_OR_UNDISCLOSED; no inference"
            ),
            "evidence": [{**evidence, **item} for _, item in hits],
            "context_rows": context if category == "EARNINGS" else [],
        })
    changes = [
        {
            "item": item["cells"][0], "reason": item["cells"][1],
            "before": item["cells"][2], "after": item["cells"][3],
            "evidence": {**evidence, **item},
        }
        for item in table_rows
        if item["table"] in correction_tables and len(item["cells"]) == 4
        and re.sub(r"\s+", "", item["cells"][0]) != "항목"
    ]
    return {
        "fields": fields, "correction_changes": changes,
        "contract_conditions": _contract_conditions(
            [item for item in table_rows if item["table"] not in correction_tables], evidence,
        ) if category == "CONTRACT" else [],
        "correction_change_status": (
            "VERIFIED_FROM_CORRECTION_TABLE" if changes else
            "UNCONFIRMED" if row["is_correction"] else "NOT_APPLICABLE"
        ),
        "original_receipt_no": None,
        "original_relationship": "NOT_ESTABLISHED" if row["is_correction"] else "NOT_APPLICABLE",
        "extraction_scope": "NAMED_TABLE_FIELDS_ONLY; not a full substantive review",
        "extraction_profile": profile,
        "document_encoding": tree.getroottree().docinfo.encoding,
    }


def market_linkage(root: Path, ticker: str, day: str) -> dict:
    paths = ExpandedShadowPaths(root)
    result = {}
    for dataset, directory, suffix, required in (
        ("market", paths.data_root / "market", ".csv", ("open", "high", "low", "close", "volume")),
        ("investor", paths.data_root / "investor", "_investor.csv",
         ("foreign_net_buy", "institution_net_buy")),
    ):
        dates = sorted((p.name for p in directory.iterdir()
                        if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)
                        and p.name <= day), reverse=True) if directory.exists() else []
        candidates = [directory / day / f"{ticker}{suffix}"]
        prior_dates = [d for d in dates if d != day]
        candidates += [directory / d / f"{ticker}{suffix}" for d in prior_dates[:1]]
        candidates += [root / "data" / ("raw" if dataset == "market" else "investor")
                       / f"{ticker}{suffix}"]
        detail = {"ticker": ticker, "basis_date": day, "status": "FILE_NOT_FOUND",
                  "path": None, "matched_row": None}
        for candidate in candidates:
            if not candidate.is_file():
                continue
            current = {"ticker": ticker, "basis_date": day, "status": "FILE_NOT_FOUND",
                       "path": str(candidate), "matched_row": None}
            try:
                with candidate.open(encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    if not {"date", *required}.issubset(reader.fieldnames or []):
                        raise DocumentError("MARKET_SCHEMA_INVALID")
                    rows = list(reader)
                matches = [r for r in rows if r["date"] == day]
                if len(matches) > 1:
                    raise DocumentError("MARKET_DATE_DUPLICATE")
                current["latest_local_date"] = max((r["date"] for r in rows), default=None)
                current["status"] = "MATCHED" if matches else "DATE_NOT_AVAILABLE"
                if matches:
                    match = matches[0]
                    if dataset == "investor" and match.get("ticker") != ticker:
                        raise DocumentError("MARKET_TICKER_MISMATCH")
                    for name in required:
                        if not Decimal(match[name]).is_finite():
                            raise DocumentError("MARKET_VALUE_INVALID")
                    current["matched_row"] = {name: match[name] for name in ("date", *required)}
            except (OSError, csv.Error, UnicodeError, InvalidOperation, DocumentError, KeyError) as exc:
                current.update(status="ERROR", matched_row=None,
                               error_code=str(exc) if isinstance(exc, DocumentError) else type(exc).__name__)
            if detail["status"] == "FILE_NOT_FOUND" or current["status"] in {"MATCHED", "ERROR"}:
                detail = current
            if current["status"] in {"MATCHED", "ERROR"}:
                break
        result[dataset] = detail
    result["join_key"] = ["ticker", "rcept_dt = date"]
    result["limitation"] = "Same-day observation only; receipt time unknown, no causal or point-in-time trading claim."
    return result


def analyze_document(row: dict, root: Path) -> dict:
    receipt = row["rcept_no"]
    if receipt not in {item[0] for item in SELECTION}:
        raise DocumentError("OUTSIDE_FIVE_DOCUMENT_ALLOWLIST")
    entry_url = BASE + "/dsaf001/main.do?" + urlencode({"rcpNo": receipt})
    result = {
        "rcept_no": receipt, "ticker": row["ticker"], "name": row["name"],
        "report_nm": row["report_nm"], "rcept_dt": row["rcept_dt"],
        "is_correction": row["is_correction"], "selection_reason": row["selection_reason"],
        "original_url": entry_url, "access_status": "BLOCKED",
        "original_reviewed": False, "fields": [], "correction_changes": [],
        "contract_conditions": [],
        "access_attempted_at": now_iso(), "requests_attempted": 0,
        "market_linkage": market_linkage(root, row["ticker"], row["rcept_dt"]),
    }
    try:
        result["requests_attempted"] += 1
        entry, entry_evidence = _get(entry_url)
        result["entry_evidence"] = entry_evidence
        url = document_url(entry, row)
        result["requests_attempted"] += 1
        content, evidence = _get(url)
        result["document_evidence"] = evidence
        result.update(extract_fields(content, row, evidence))
    except (DocumentError, UnicodeError, etree.ParserError) as exc:
        result["error_code"] = str(exc) if isinstance(exc, DocumentError) else type(exc).__name__
        return result
    result.update(
        access_status="SUCCESS", original_accessed=True,
        extraction_status="PARTIAL" if any(f["status"] != "VERIFIED" for f in result["fields"])
        else "SUCCESS",
        verified_field_count=sum(f["status"] == "VERIFIED" for f in result["fields"]),
    )
    return result
