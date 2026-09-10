"""
src/dual_shadow_ledger.py
==========================
DUAL Shadow STEP 3 — Append-only DUAL Comparison Ledger.

목적:
  src.dual_shadow_evaluator.evaluate_latest_day() 결과(v0.1 Baseline vs v0.2
  Challenger)를 거래일 x 20종목 단위로 output/dual_shadow_signal_ledger.csv에
  append-only, idempotent하게 저장한다.

이번 STEP에서 하지 않는 것:
  - Forward Return 계산
  - Scheduler 연결
  - Dashboard/API 변경
  - 기존 Production Shadow(src.shadow_tracking) 변경

원칙:
  1. 동일 (trade_date, stock_code, baseline_engine_version, challenger_engine_version)
     comparison key는 중복 저장하지 않는다 (엔진 버전이 달라지면 별도 row로 append).
  2. 기존 기록은 절대 덮어쓰거나 삭제하지 않는다 (append-only).
  3. Challenger 평가 실패가 Baseline 저장에 영향을 주지 않는다
     (evaluate_latest_day가 이미 두 엔진을 함께 NOT_EVALUABLE 처리하므로,
      이 모듈은 종목 단위로 예외를 격리해 한 종목의 오류가 나머지 19종목에
      전파되지 않도록만 한다).
  4. NOT_EVALUABLE은 comparison_group에도 "NOT_EVALUABLE"로 기록하며, 정상 평가된
     4개 그룹(BOTH_YES/BASELINE_ONLY/CHALLENGER_ONLY/BOTH_NO)과 절대 혼용하지 않는다.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src import config
from src.config import OUTPUT_DIR
from src.data_provider.csv_provider import CsvDataProvider
from src.dual_shadow_evaluator import DualShadowEvaluation, evaluate_latest_day
from src.indicators import add_all_indicators

BASELINE_ENGINE_VERSION = "v0.1"
CHALLENGER_ENGINE_VERSION = "v0.2"

DEFAULT_LEDGER_PATH = OUTPUT_DIR / "dual_shadow_signal_ledger.csv"


@dataclass
class DualLedgerRecord:
    """DUAL Comparison Ledger 1행. 필드 순서가 곧 CSV 컬럼 순서."""

    trade_date: str
    stock_code: str
    stock_name: str
    evaluation_close: float | None
    baseline_raw_score: int | None
    baseline_score: float | None
    baseline_signal_type: str | None
    baseline_signal_present: bool
    challenger_raw_score: int | None
    challenger_score: float | None
    challenger_signal_type: str | None
    challenger_signal_present: bool
    challenger_volume_penalty: int
    challenger_pre_return_penalty: int
    challenger_rsi_penalty: int
    challenger_total_penalty: int
    comparison_group: str
    evaluation_status: str
    baseline_engine_version: str
    challenger_engine_version: str
    source_commit: str
    created_at: str


LEDGER_FIELDS = [f.name for f in fields(DualLedgerRecord)]


def get_source_commit(repo_root: Path | None = None) -> str:
    """현재 git HEAD commit hash를 조회한다. 실패 시 'UNKNOWN'."""
    root = repo_root or config.ROOT_DIR
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        commit = result.stdout.strip()
        return commit if result.returncode == 0 and commit else "UNKNOWN"
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


def build_ledger_record(
    evaluation: DualShadowEvaluation,
    source_commit: str,
    created_at: str | None = None,
    baseline_engine_version: str = BASELINE_ENGINE_VERSION,
    challenger_engine_version: str = CHALLENGER_ENGINE_VERSION,
) -> DualLedgerRecord:
    """단일 DualShadowEvaluation을 DualLedgerRecord로 변환한다.

    STEP 2 evaluator는 NOT_EVALUABLE도 comparison_group="BOTH_NO"로 반환하지만,
    Ledger에서는 정상 4개 그룹과 명확히 구분하기 위해 "NOT_EVALUABLE"로 덮어쓴다.
    """
    comparison_group = (
        "NOT_EVALUABLE"
        if evaluation.evaluation_status == "NOT_EVALUABLE"
        else evaluation.comparison_group
    )
    return DualLedgerRecord(
        trade_date=evaluation.trade_date,
        stock_code=evaluation.ticker,
        stock_name=evaluation.name,
        evaluation_close=evaluation.close,
        baseline_raw_score=evaluation.baseline.raw_score,
        baseline_score=evaluation.baseline.score,
        baseline_signal_type=evaluation.baseline.signal_type,
        baseline_signal_present=evaluation.baseline.signal_present,
        challenger_raw_score=evaluation.challenger.raw_score,
        challenger_score=evaluation.challenger.score,
        challenger_signal_type=evaluation.challenger.signal_type,
        challenger_signal_present=evaluation.challenger.signal_present,
        challenger_volume_penalty=evaluation.challenger.volume_penalty,
        challenger_pre_return_penalty=evaluation.challenger.pre_return_penalty,
        challenger_rsi_penalty=evaluation.challenger.rsi_penalty,
        challenger_total_penalty=evaluation.challenger.total_penalty,
        comparison_group=comparison_group,
        evaluation_status=evaluation.evaluation_status,
        baseline_engine_version=baseline_engine_version,
        challenger_engine_version=challenger_engine_version,
        source_commit=source_commit,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
    )


class DualShadowLedgerStore:
    """DUAL Comparison Ledger CSV 저장소.

    append-only, 동일 (trade_date, stock_code, baseline_engine_version,
    challenger_engine_version) 중복 방지. 엔진 버전이 달라지면 동일 날짜/종목이라도
    별도 row로 append된다.
    """

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH) -> None:
        self.path = Path(path)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=LEDGER_FIELDS)
        return pd.read_csv(self.path, dtype={"stock_code": str, "trade_date": str})

    def exists(
        self,
        trade_date: str,
        stock_code: str,
        baseline_engine_version: str,
        challenger_engine_version: str,
    ) -> bool:
        existing = self.load()
        if existing.empty:
            return False
        match = existing[
            (existing["trade_date"].astype(str) == str(trade_date))
            & (existing["stock_code"].astype(str) == str(stock_code))
            & (existing["baseline_engine_version"].astype(str) == str(baseline_engine_version))
            & (existing["challenger_engine_version"].astype(str) == str(challenger_engine_version))
        ]
        return not match.empty

    def add(self, record: DualLedgerRecord) -> bool:
        """record를 저장한다.

        이미 동일 (trade_date, stock_code, baseline_engine_version,
        challenger_engine_version) 기록이 있으면 저장하지 않고 False 반환.
        """
        if self.exists(
            record.trade_date,
            record.stock_code,
            record.baseline_engine_version,
            record.challenger_engine_version,
        ):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = pd.DataFrame([asdict(record)], columns=LEDGER_FIELDS)
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        row.to_csv(self.path, mode="a", header=write_header, index=False)
        return True


def build_ledger_records_for_all_tickers(
    tickers: dict[str, str] | None = None,
    price_data: dict[str, pd.DataFrame] | None = None,
    source_commit: str | None = None,
    created_at: str | None = None,
    baseline_engine_version: str = BASELINE_ENGINE_VERSION,
    challenger_engine_version: str = CHALLENGER_ENGINE_VERSION,
) -> list[DualLedgerRecord]:
    """config.TICKERS(또는 전달된 tickers) 전체에 대해 최신 거래일 DUAL 평가를 수행하고
    DualLedgerRecord 목록을 반환한다 (저장은 하지 않음).

    한 종목의 로드/평가 실패가 나머지 종목 처리에 영향을 주지 않도록 종목 단위로 격리한다.
    """
    tickers = tickers or config.TICKERS
    provider = CsvDataProvider(config.DATA_RAW_DIR)
    resolved_commit = source_commit if source_commit is not None else get_source_commit()

    records: list[DualLedgerRecord] = []
    for ticker, name in tickers.items():
        try:
            if price_data is not None:
                df = price_data.get(ticker)
            else:
                df = provider.load(ticker)
        except (FileNotFoundError, ValueError):
            df = None

        try:
            df_ind = add_all_indicators(df) if df is not None else None
        except Exception:
            df_ind = None

        evaluation = evaluate_latest_day(df_ind, ticker, name)
        records.append(
            build_ledger_record(
                evaluation,
                resolved_commit,
                created_at=created_at,
                baseline_engine_version=baseline_engine_version,
                challenger_engine_version=challenger_engine_version,
            )
        )

    return records


def run_dual_shadow_ledger_build(
    tickers: dict[str, str] | None = None,
    price_data: dict[str, pd.DataFrame] | None = None,
    store: DualShadowLedgerStore | None = None,
    source_commit: str | None = None,
    created_at: str | None = None,
    baseline_engine_version: str = BASELINE_ENGINE_VERSION,
    challenger_engine_version: str = CHALLENGER_ENGINE_VERSION,
) -> tuple[list[DualLedgerRecord], dict[str, int]]:
    """전체 종목 DUAL 평가를 수행하고 append-only로 저장한다. (저장된 record 목록, 통계)를 반환한다."""
    store = store or DualShadowLedgerStore()
    records = build_ledger_records_for_all_tickers(
        tickers=tickers,
        price_data=price_data,
        source_commit=source_commit,
        created_at=created_at,
        baseline_engine_version=baseline_engine_version,
        challenger_engine_version=challenger_engine_version,
    )

    stats = {
        "checked": len(records),
        "saved": 0,
        "duplicate_skip": 0,
        "both_yes": 0,
        "baseline_only": 0,
        "challenger_only": 0,
        "both_no": 0,
        "not_evaluable": 0,
    }

    saved: list[DualLedgerRecord] = []
    for record in records:
        if record.evaluation_status == "NOT_EVALUABLE":
            stats["not_evaluable"] += 1  # BOTH_NO와 의미상 구분 — comparison_group 카운트에는 넣지 않음
        else:
            stats[record.comparison_group.lower()] += 1

        if store.add(record):
            stats["saved"] += 1
            saved.append(record)
        else:
            stats["duplicate_skip"] += 1

    return saved, stats
