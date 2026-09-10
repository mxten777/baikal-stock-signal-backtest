from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DUAL_LEDGER_SOURCE = "output/dual_shadow_signal_ledger.csv"
DUAL_FORWARD_SOURCE = "output/dual_shadow_forward_returns.csv"
DUAL_PERFORMANCE_SOURCE = "output/dual_shadow_performance_summary.json"
DUAL_REGISTRY_SOURCE = "output/dual_shadow_run_registry.jsonl"

HORIZONS = (5, 10, 20)
COMPARISON_GROUPS = ("BOTH_YES", "BASELINE_ONLY", "CHALLENGER_ONLY", "BOTH_NO", "NOT_EVALUABLE")
READ_ONLY_ENDPOINTS = frozenset(
    {
        "/api/dual-shadow/status",
        "/api/dual-shadow/latest",
        "/api/dual-shadow/performance",
        "/api/dual-shadow/runs",
    }
)

REQUIRED_LEDGER_COLUMNS = {
    "trade_date",
    "stock_code",
    "stock_name",
    "baseline_score",
    "baseline_signal_type",
    "challenger_score",
    "challenger_signal_type",
    "challenger_volume_penalty",
    "challenger_pre_return_penalty",
    "challenger_rsi_penalty",
    "challenger_total_penalty",
    "comparison_group",
    "evaluation_status",
    "baseline_engine_version",
    "challenger_engine_version",
}
REQUIRED_FORWARD_COLUMNS = {
    "trade_date",
    "stock_code",
    "horizon",
    "return_status",
    "forward_return",
    "baseline_engine_version",
    "challenger_engine_version",
}
REQUIRED_REGISTRY_FIELDS = {"trade_date", "started_at", "finished_at", "status", "ledger_saved", "forward_return_saved", "performance_status"}


@dataclass(frozen=True)
class SourceRead:
    status: str
    source: str
    rows: list[dict[str, Any]]
    warnings: list[str]


class DualShadowDashboardService:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def status(self) -> dict[str, Any]:
        ledger = self._read_ledger()
        forward = self._read_forward_returns()
        summary = self._read_performance_summary()
        registry = self._read_registry()

        latest_rows = _latest_ledger_rows(ledger.rows)
        latest_trade_date = latest_rows[0]["trade_date"] if latest_rows else _latest_registry_trade_date(registry.rows)
        last_run = registry.rows[-1] if registry.rows else None
        engine_versions = _engine_versions(latest_rows, summary.payload)
        performance_status = _performance_status(summary)
        status_warnings = ledger.warnings + forward.warnings + summary.warnings + registry.warnings

        return {
            "mode": "DUAL_SHADOW",
            "read_only": True,
            "baseline_label": "current reference model",
            "challenger_label": "experimental model",
            "latest_trade_date": latest_trade_date,
            "last_dual_run": last_run.get("finished_at") if last_run else None,
            "pipeline_status": last_run.get("status") if last_run else "NO_RUN",
            "baseline_engine_version": engine_versions["baseline"],
            "challenger_engine_version": engine_versions["challenger"],
            "ledger_status": ledger.status,
            "ledger_row_count": len(ledger.rows),
            "forward_return_status": forward.status,
            "forward_return_evidence_count": len(forward.rows),
            "performance_status": performance_status,
            "sources": {
                "ledger": DUAL_LEDGER_SOURCE,
                "forward_returns": DUAL_FORWARD_SOURCE,
                "performance": DUAL_PERFORMANCE_SOURCE,
                "runs": DUAL_REGISTRY_SOURCE,
            },
            "warnings": status_warnings,
        }

    def latest(self) -> dict[str, Any]:
        ledger = self._read_ledger()
        forward = self._read_forward_returns()
        latest_rows = _latest_ledger_rows(ledger.rows)
        trade_date = latest_rows[0]["trade_date"] if latest_rows else None
        counts = {group: 0 for group in COMPARISON_GROUPS}
        counts.update(Counter(str(row.get("comparison_group") or "NOT_EVALUABLE") for row in latest_rows))

        return {
            "status": ledger.status if ledger.status != "AVAILABLE" else "AVAILABLE",
            "source": DUAL_LEDGER_SOURCE,
            "trade_date": trade_date,
            "total_stocks": len(latest_rows),
            "counts": counts,
            "records": [_ledger_record_payload(row) for row in latest_rows],
            "evidence_maturity": self._evidence_maturity(latest_rows, forward),
            "warnings": ledger.warnings + forward.warnings,
        }

    def performance(self) -> dict[str, Any]:
        summary = self._read_performance_summary()
        if summary.status != "AVAILABLE":
            return {
                "status": summary.status,
                "source": DUAL_PERFORMANCE_SOURCE,
                "engine_versions": {"baseline": "v0.1", "challenger": "v0.2"},
                "total_evidence_rows": None,
                "horizons": _empty_performance_horizons(),
                "warnings": summary.warnings,
            }
        payload = summary.payload
        return {
            "status": str(payload.get("status") or "UNKNOWN"),
            "source": DUAL_PERFORMANCE_SOURCE,
            "generated_at": payload.get("generated_at"),
            "win_definition": payload.get("win_definition"),
            "engine_versions": payload.get("engine_versions") or {"baseline": "v0.1", "challenger": "v0.2"},
            "total_evidence_rows": payload.get("total_evidence_rows"),
            "horizons": _normalize_performance_horizons(payload.get("horizons")),
            "warnings": summary.warnings,
        }

    def runs(self, limit: int = 20) -> dict[str, Any]:
        registry = self._read_registry()
        return {
            "status": registry.status,
            "source": DUAL_REGISTRY_SOURCE,
            "items": list(reversed(registry.rows[-limit:])),
            "warnings": registry.warnings,
        }

    def _evidence_maturity(self, latest_rows: list[dict[str, Any]], forward: SourceRead) -> dict[str, dict[str, int | str]]:
        eligible_keys = {
            _ledger_key(row)
            for row in latest_rows
            if str(row.get("evaluation_status")) != "NOT_EVALUABLE"
        }
        available_by_horizon: Counter[int] = Counter()
        if forward.status == "AVAILABLE":
            for row in forward.rows:
                key = (
                    str(row.get("trade_date")),
                    str(row.get("stock_code")),
                    str(row.get("baseline_engine_version")),
                    str(row.get("challenger_engine_version")),
                )
                if key in eligible_keys:
                    try:
                        available_by_horizon[int(row.get("horizon"))] += 1
                    except (TypeError, ValueError):
                        continue
        maturity: dict[str, dict[str, int | str]] = {}
        for horizon in HORIZONS:
            available = int(available_by_horizon[horizon])
            expected = len(eligible_keys)
            maturity[f"{horizon}D"] = {
                "available": available,
                "pending": max(expected - available, 0),
                "status": forward.status,
            }
        return maturity

    def _read_ledger(self) -> SourceRead:
        path = self._source_path(DUAL_LEDGER_SOURCE)
        if not path.exists():
            return SourceRead("NO_DATA", DUAL_LEDGER_SOURCE, [], ["DUAL ledger file is missing"])
        rows, warnings = _read_csv_rows(path, REQUIRED_LEDGER_COLUMNS, DUAL_LEDGER_SOURCE)
        if warnings:
            return SourceRead("ERROR", DUAL_LEDGER_SOURCE, rows, warnings)
        if not rows:
            return SourceRead("NO_DATA", DUAL_LEDGER_SOURCE, [], ["DUAL ledger has no records"])
        warnings = _validate_ledger_rows(rows)
        if warnings:
            return SourceRead("ERROR", DUAL_LEDGER_SOURCE, rows, warnings)
        return SourceRead("AVAILABLE", DUAL_LEDGER_SOURCE, rows, [])

    def _read_forward_returns(self) -> SourceRead:
        path = self._source_path(DUAL_FORWARD_SOURCE)
        if not path.exists():
            return SourceRead("NO_AVAILABLE_EVIDENCE", DUAL_FORWARD_SOURCE, [], ["DUAL forward return file is missing"])
        rows, warnings = _read_csv_rows(path, REQUIRED_FORWARD_COLUMNS, DUAL_FORWARD_SOURCE)
        if warnings:
            return SourceRead("ERROR", DUAL_FORWARD_SOURCE, rows, warnings)
        if not rows:
            return SourceRead("NO_AVAILABLE_EVIDENCE", DUAL_FORWARD_SOURCE, [], ["DUAL forward return file has no records"])
        warnings = _validate_forward_rows(rows)
        if warnings:
            return SourceRead("ERROR", DUAL_FORWARD_SOURCE, rows, warnings)
        return SourceRead("AVAILABLE", DUAL_FORWARD_SOURCE, rows, [])

    def _read_performance_summary(self) -> "JsonRead":
        path = self._source_path(DUAL_PERFORMANCE_SOURCE)
        if not path.exists():
            return JsonRead("NO_SUMMARY", {}, ["DUAL performance summary file is missing"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return JsonRead("ERROR", {}, [f"DUAL performance summary is malformed: {type(exc).__name__}: {exc}"])
        if not isinstance(payload, dict):
            return JsonRead("ERROR", {}, ["DUAL performance summary must be a JSON object"])
        if not isinstance(payload.get("horizons"), dict) or "status" not in payload:
            return JsonRead("ERROR", payload, ["DUAL performance summary missing status or horizons"])
        return JsonRead("AVAILABLE", payload, [])

    def _read_registry(self) -> SourceRead:
        path = self._source_path(DUAL_REGISTRY_SOURCE)
        if not path.exists():
            return SourceRead("NO_RUN", DUAL_REGISTRY_SOURCE, [], ["DUAL run registry file is missing"])
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            return SourceRead("ERROR", DUAL_REGISTRY_SOURCE, [], [f"DUAL run registry is unreadable: {type(exc).__name__}: {exc}"])
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.append(f"line {line_number} malformed JSON: {exc}")
                continue
            if not isinstance(row, dict):
                warnings.append(f"line {line_number} must be a JSON object")
                continue
            missing = sorted(field for field in REQUIRED_REGISTRY_FIELDS if field not in row)
            if missing:
                warnings.append(f"line {line_number} missing required fields: {missing}")
                continue
            rows.append({field: row.get(field) for field in sorted(row) if field != "result"})
        if warnings:
            return SourceRead("ERROR", DUAL_REGISTRY_SOURCE, rows, warnings)
        if not rows:
            return SourceRead("NO_RUN", DUAL_REGISTRY_SOURCE, [], ["DUAL run registry has no records"])
        return SourceRead("AVAILABLE", DUAL_REGISTRY_SOURCE, rows, [])

    def _source_path(self, relative_path: str) -> Path:
        allowed = {DUAL_LEDGER_SOURCE, DUAL_FORWARD_SOURCE, DUAL_PERFORMANCE_SOURCE, DUAL_REGISTRY_SOURCE}
        if relative_path not in allowed:
            raise PermissionError(f"DUAL source is not allowlisted: {relative_path}")
        candidate = (self.repo_root / relative_path).resolve()
        root = self.repo_root.resolve()
        if candidate != root and root not in candidate.parents:
            raise PermissionError(f"DUAL source escapes repository root: {relative_path}")
        return candidate


@dataclass(frozen=True)
class JsonRead:
    status: str
    payload: dict[str, Any]
    warnings: list[str]


def _read_csv_rows(path: Path, required_columns: set[str], source: str) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return [], [f"{source} has no header"]
            missing = sorted(column for column in required_columns if column not in reader.fieldnames)
            if missing:
                return [], [f"{source} missing required columns: {missing}"]
            return list(reader), []
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return [], [f"{source} is malformed: {type(exc).__name__}: {exc}"]


def _validate_ledger_rows(rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for index, row in enumerate(rows, start=2):
        group = str(row.get("comparison_group") or "")
        if group not in COMPARISON_GROUPS:
            warnings.append(f"row {index} invalid comparison_group: {group}")
        if not row.get("trade_date") or not row.get("stock_code"):
            warnings.append(f"row {index} missing trade_date or stock_code")
        for column in ("baseline_score", "challenger_score"):
            value = row.get(column)
            if value not in (None, ""):
                try:
                    float(value)
                except ValueError:
                    warnings.append(f"row {index} invalid numeric value for {column}: {value}")
    return warnings


def _validate_forward_rows(rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for index, row in enumerate(rows, start=2):
        try:
            horizon = int(row.get("horizon"))
        except (TypeError, ValueError):
            warnings.append(f"row {index} invalid horizon: {row.get('horizon')}")
            continue
        if horizon not in HORIZONS:
            warnings.append(f"row {index} unsupported horizon: {horizon}")
        if str(row.get("return_status")) == "AVAILABLE" and row.get("forward_return") in (None, ""):
            warnings.append(f"row {index} AVAILABLE evidence missing forward_return")
    return warnings


def _latest_ledger_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest_date = max(str(row.get("trade_date")) for row in rows if row.get("trade_date"))
    return [row for row in rows if str(row.get("trade_date")) == latest_date]


def _latest_registry_trade_date(rows: list[dict[str, Any]]) -> str | None:
    for row in reversed(rows):
        if row.get("trade_date"):
            return str(row["trade_date"])
    return None


def _engine_versions(rows: list[dict[str, Any]], performance_payload: dict[str, Any]) -> dict[str, str]:
    if rows:
        return {
            "baseline": str(rows[0].get("baseline_engine_version") or "UNKNOWN"),
            "challenger": str(rows[0].get("challenger_engine_version") or "UNKNOWN"),
        }
    versions = performance_payload.get("engine_versions") if isinstance(performance_payload, dict) else None
    if isinstance(versions, dict):
        return {"baseline": str(versions.get("baseline") or "UNKNOWN"), "challenger": str(versions.get("challenger") or "UNKNOWN")}
    return {"baseline": "UNKNOWN", "challenger": "UNKNOWN"}


def _performance_status(summary: JsonRead) -> str:
    if summary.status == "NO_SUMMARY":
        return "NO_SUMMARY"
    if summary.status != "AVAILABLE":
        return "ERROR"
    return str(summary.payload.get("status") or "UNKNOWN")


def _ledger_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("trade_date")),
        str(row.get("stock_code")),
        str(row.get("baseline_engine_version")),
        str(row.get("challenger_engine_version")),
    )


def _ledger_record_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_name": row.get("stock_name"),
        "stock_code": row.get("stock_code"),
        "baseline_score": _number_or_none(row.get("baseline_score")),
        "baseline_signal": row.get("baseline_signal_type"),
        "challenger_score": _number_or_none(row.get("challenger_score")),
        "challenger_signal": row.get("challenger_signal_type"),
        "comparison_group": row.get("comparison_group"),
        "evaluation_status": row.get("evaluation_status"),
        "challenger_volume_penalty": _number_or_none(row.get("challenger_volume_penalty")),
        "challenger_pre_return_penalty": _number_or_none(row.get("challenger_pre_return_penalty")),
        "challenger_rsi_penalty": _number_or_none(row.get("challenger_rsi_penalty")),
        "challenger_total_penalty": _number_or_none(row.get("challenger_total_penalty")),
    }


def _number_or_none(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _empty_performance_horizons() -> dict[str, dict[str, Any]]:
    empty_stats = {
        "signal_count": 0,
        "avg_return": None,
        "median_return": None,
        "win_rate": None,
        "best_return": None,
        "worst_return": None,
    }
    return {
        f"{horizon}D": {
            "baseline": dict(empty_stats),
            "challenger": dict(empty_stats),
            "delta": {
                "avg_return_delta": None,
                "median_return_delta": None,
                "win_rate_delta": None,
                "signal_count_delta": 0,
            },
        }
        for horizon in HORIZONS
    }


def _normalize_performance_horizons(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return _empty_performance_horizons()
    normalized = _empty_performance_horizons()
    for horizon in HORIZONS:
        value = raw.get(str(horizon)) or raw.get(f"{horizon}D")
        if isinstance(value, dict):
            normalized[f"{horizon}D"] = value
    return normalized