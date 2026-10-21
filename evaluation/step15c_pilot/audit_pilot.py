"""Offline pilot artifacts only; never import or run operational extractors."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lxml import html

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STORE = ROOT / "output" / "dart_analysis_cards" / "cards.sqlite3"
RECEIPTS = ("20261020800221", "20261020800241", "20261020800720")
STARTED_AT = "2026-10-21T23:36:32.543225+09:00"


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(content):
    return hashlib.sha256(content).hexdigest()


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def save(name, value):
    with (HERE / name).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def status():
    result = subprocess.run(
        ["git", "--no-pager", "status", "--porcelain=v1", "-uall"],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return sorted(
        line for line in result.stdout.splitlines()
        if not line[3:].replace("\\", "/").startswith("evaluation/step15c_pilot/")
    )


def protected_paths():
    paths = {ROOT / line[3:] for line in status()}
    for directory in (ROOT / "output").glob("dart*"):
        if directory.is_dir():
            paths.update(path for path in directory.rglob("*") if path.is_file())
        elif directory.is_file():
            paths.add(directory)
    paths.update((ROOT / "data" / "expanded_shadow" / "universe").rglob("*.csv"))
    return sorted(paths)


def readonly():
    connection = sqlite3.connect(STORE.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def prepare():
    original_status = status()
    if len(original_status) != 18:
        raise ValueError("Expected the preserved 18-file worktree")
    save("preservation_before.json", {
        "started_at": STARTED_AT, "captured_at": stamp(), "status": original_status,
        "hashes": {
            str(path.relative_to(ROOT)): digest(path.read_bytes())
            for path in protected_paths()
        },
    })
    packet = []
    with closing(readonly()) as connection:
        for receipt in RECEIPTS:
            rows = connection.execute(
                "SELECT DISTINCT body_sha256 FROM cards WHERE receipt=?", (receipt,),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError("Pilot requires one unambiguous body per receipt")
            body_hash = rows[0][0]
            body = connection.execute(
                "SELECT content FROM documents WHERE sha256=?", (body_hash,),
            ).fetchone()[0]
            if digest(body) != body_hash:
                raise ValueError("Body hash mismatch")
            metadata = connection.execute(
                "SELECT json_extract(payload_json,'$.ticker'),"
                "json_extract(payload_json,'$.name'),"
                "json_extract(payload_json,'$.report_nm'),"
                "json_extract(payload_json,'$.rcept_dt'),"
                "json_extract(payload_json,'$.document.url'),"
                "json_extract(payload_json,'$.document.checked_at') "
                "FROM cards WHERE receipt=? ORDER BY rowid DESC LIMIT 1",
                (receipt,),
            ).fetchone()
            tree = html.fromstring(body, parser=html.HTMLParser(no_network=True))
            source_rows = []
            for table_no, table in enumerate(tree.xpath("//table"), 1):
                own_rows = [
                    row for row in table.xpath(".//tr")
                    if row.xpath("ancestor::table[1]")[0] is table
                ]
                for row_no, row in enumerate(own_rows, 1):
                    source_rows.append({
                        "table": table_no, "row": row_no,
                        "xpath": tree.getroottree().getpath(row),
                        "cells": [
                            " ".join(cell.text_content().split())
                            for cell in row.xpath("./td|./th")
                        ],
                    })
            with (HERE / f"{receipt}.html").open("xb") as handle:
                handle.write(body)
            packet.append({
                "receipt": receipt, "body_sha256": body_hash,
                "ticker": metadata[0], "name": metadata[1], "title": metadata[2].strip(),
                "disclosure_date": metadata[3], "official_url": metadata[4],
                "original_checked_at": metadata[5], "rows": source_rows,
            })
    save("source_packet.json", {
        "created_at": stamp(), "scope": "Archived HTML only; no fresh identity check",
        "rule_fields_included": False, "documents": packet,
    })


def validate():
    draft = load("answer_draft.json")
    packet = {doc["receipt"]: doc for doc in load("source_packet.json")["documents"]}
    if draft["review_status"] != "HUMAN_REVIEW_PENDING":
        raise ValueError("Not a human-reviewed gold set")
    if set(packet) != {doc["receipt"] for doc in draft["documents"]}:
        raise ValueError("Receipt set mismatch")
    results = []
    ids = set()
    for doc in draft["documents"]:
        source = packet[doc["receipt"]]
        body = (HERE / f"{doc['receipt']}.html").read_bytes()
        if digest(body) != doc["body_sha256"] or doc["body_sha256"] != source["body_sha256"]:
            raise ValueError("Draft body hash mismatch")
        if parse_qs(urlsplit(source["official_url"]).query).get("rcpNo") != [doc["receipt"]]:
            raise ValueError("Stored official URL receipt mismatch")
        tree = html.fromstring(body, parser=html.HTMLParser(no_network=True))
        for item in doc["items"]:
            key = (doc["receipt"], item["id"])
            if key in ids:
                raise ValueError("Duplicate item")
            ids.add(key)
            evidence = item["evidence"]
            row = next(
                row for row in source["rows"]
                if (row["table"], row["row"]) == (evidence["table"], evidence["row"])
            )
            nodes = tree.xpath(row["xpath"])
            if len(nodes) != 1:
                raise ValueError("Evidence XPath does not resolve")
            cells = [" ".join(c.text_content().split()) for c in nodes[0].xpath("./td|./th")]
            if cells != row["cells"] or evidence["quote"] not in cells[evidence["cell"]]:
                raise ValueError("Evidence quote mismatch")
            if "numeric_value" in item:
                if Decimal(evidence["quote"].replace(",", "")) != Decimal(item["numeric_value"]):
                    raise ValueError("Numeric normalization mismatch")
            if item["section"] == "basic" and item["state"] == "CONFIRMED":
                raw_value = item["value"]
                if isinstance(raw_value, dict):
                    raw_value = raw_value["raw_text"]
                if raw_value != cells[evidence["cell"]]:
                    raise ValueError("Basic raw value mismatch")
                if item["id"].endswith("_date"):
                    if datetime.fromisoformat(raw_value).date().isoformat() != raw_value:
                        raise ValueError("Noncanonical date")
            results.append({
                "receipt": doc["receipt"], "item": item["id"],
                "xpath": row["xpath"], "cells": cells, "quote_matches": True,
            })
    save("draft_freeze.json", {
        "frozen_at": stamp(), "draft_sha256": digest((HERE / "answer_draft.json").read_bytes()),
        "source_packet_sha256": digest((HERE / "source_packet.json").read_bytes()),
        "item_count": len(results), "evidence_checks": results,
        "verdict": "STRUCTURAL_EVIDENCE_CHECKS_PASS",
        "semantic_correctness": "HUMAN_REVIEW_PENDING",
        "limitation": "Validates identity and evidence location, not human approval or completeness",
    })


def compare():
    freeze = load("draft_freeze.json")
    if freeze["draft_sha256"] != digest((HERE / "answer_draft.json").read_bytes()):
        raise ValueError("Draft changed after freeze")
    results = []
    with closing(readonly()) as connection:
        for doc in load("answer_draft.json")["documents"]:
            saved = connection.execute(
                "SELECT c.payload_json FROM attempts a JOIN cards c "
                "ON c.receipt=a.receipt AND c.body_sha256=a.body_sha256 "
                "AND c.extractor_version=a.extractor_version "
                "WHERE a.id=(SELECT MAX(id) FROM attempts WHERE receipt=?)",
                (doc["receipt"],),
            ).fetchone()
            if saved is None:
                raise ValueError("Latest attempt/card missing")
            card = json.loads(saved[0])
            if card["body_sha256"] != doc["body_sha256"]:
                raise ValueError("Comparison uses a different body")
            fields = {field["field"]: field for field in card["fields"]}
            conditions = {field["field"]: field for field in card["conditions"]}
            for item in doc["items"]:
                rule_key = item.get("rule_key", item["id"])
                field = (fields if item["section"] == "basic" else conditions).get(rule_key)
                match = None
                if field is not None and item["section"] == "basic":
                    if item["state"] == "CONFIRMED":
                        expected = item["value"]
                        actual = field["raw_value"]
                        if isinstance(expected, dict):
                            actual = {
                                "raw_text": field["raw_value"],
                                "normalized_value": field.get("normalized_value"),
                                "scope": field.get("scope"),
                            }
                        match = (
                            field["status"] == "VERIFIED" and actual == expected
                            and field.get("unit") == item["unit"]
                        )
                    else:
                        match = (
                            field["status"] == "UNCONFIRMED"
                            and field["numeric_value"] is None
                            and field["raw_value"] == item["evidence"]["quote"]
                        )
                results.append({
                    "receipt": doc["receipt"], "item": item["id"], "section": item["section"],
                    "draft_state": item["state"], "draft_value": item["value"],
                    "rule_result": field, "exact_basic_agreement": match,
                    "classification": (
                        "BASIC_AGREEMENT" if match is True else
                        "BASIC_DIFFERENCE" if match is False else
                        "RELATED_CONDITION_REQUIRES_SEMANTIC_REVIEW" if field is not None else
                        "OUTSIDE_NAMED_RULE_OUTPUT"
                    ),
                    "human_review": "PENDING",
                })
    save("comparison.json", {
        "compared_at": stamp(), "draft_sha256": freeze["draft_sha256"],
        "method": "Saved latest rule results only; no extractor execution",
        "not_an_accuracy_score": True, "items": results,
    })


def preservation():
    before = load("preservation_before.json")
    changed = [
        name for name, expected in before["hashes"].items()
        if not (ROOT / name).is_file() or digest((ROOT / name).read_bytes()) != expected
    ]
    status_matches = before["status"] == status()
    report = {
        "checked_at": stamp(), "protected_file_count": len(before["hashes"]),
        "changed_protected_files": changed, "original_18_status_matches": status_matches,
        "evaluation_artifact_hashes": {
            path.name: digest(path.read_bytes())
            for path in sorted(HERE.iterdir()) if path.is_file()
        },
        "elapsed_minutes_since_first_inspection": (
            datetime.now(timezone.utc) - datetime.fromisoformat(STARTED_AT)
        ).total_seconds() / 60,
    }
    save("preservation_after.json", report)
    if changed or not status_matches:
        raise ValueError("Protected files or original worktree changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "validate", "compare", "preservation"))
    arguments = parser.parse_args()
    globals()[arguments.phase]()
