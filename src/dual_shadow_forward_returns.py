"""
src/dual_shadow_forward_returns.py
===================================
DUAL Shadow STEP 4 — Forward Return Tracking (독립 추적 계층).

목적:
  DUAL Shadow STEP 3 Comparison Ledger(output/dual_shadow_signal_ledger.csv)에
  고정된 "당시 판단"을 절대 수정하지 않고, 이후 실제 가격 데이터를 이용해
  Baseline(v0.1)/Challenger(v0.2) 각각의 사후 5D/10D/20D Forward Return을
  독립적으로 추적한다.

이번 STEP에서 하지 않는 것:
  - STEP 3 Ledger의 당시 판단값 수정
  - 기존 Production Shadow(src.shadow_tracking) 코드/산출물/schema 변경
  - Baseline/Challenger Signal 재평가, 과거 정보로 Signal 재계산
  - Scheduler 연결, Dashboard/API 변경
  - 성과 우열 결론(어느 엔진이 더 낫다는 판단)

거래일 오프셋 방식(진입 거래일 위치 + horizon 거래일)은
src.shadow_tracking.compute_forward_returns와 동일한 의미를 재사용한다
(가격 데이터 자체의 행 순서를 거래일 시퀀스로 사용 → 주말/공휴일 자연 제외).

NOT_AVAILABLE 설계 (append-only 안전성):
  완성된(미래 가격이 이미 존재하는) Forward Return만 output/dual_shadow_forward_returns.csv에
  immutable evidence로 append한다. 아직 +horizon 거래일이 도래하지 않은 조합은
  "pending/NOT_AVAILABLE" 통계로만 보고하고 CSV row로 고정하지 않는다.
  이유: NOT_AVAILABLE을 permanent row로 append하면, 이후 실제 가격이 도착해도
  append-only/no-overwrite 원칙 때문에 그 row를 AVAILABLE 값으로 바꿔 쓸 방법이 없어진다.
  (동일 key가 "이미 기록됨"으로 취급되어 영구히 NOT_AVAILABLE로 고정되는 문제 방지.)
  대신 완성되는 시점에 동일 key로 최초 1회 AVAILABLE row가 append된다.

Return Key / Idempotency:
  (trade_date, stock_code, baseline_engine_version, challenger_engine_version, horizon)
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src import config
from src.config import OUTPUT_DIR
from src.data_provider.csv_provider import CsvDataProvider
from src.dual_shadow_ledger import DEFAULT_LEDGER_PATH, DualShadowLedgerStore, get_source_commit
from src.shadow_tracking import FORWARD_HORIZONS

RETURN_STATUS_AVAILABLE = "AVAILABLE"
RETURN_STATUS_NOT_AVAILABLE = "NOT_AVAILABLE"

DEFAULT_FORWARD_RETURN_PATH = OUTPUT_DIR / "dual_shadow_forward_returns.csv"

REQUIRED_LEDGER_COLUMNS = [
    "trade_date",
    "stock_code",
    "stock_name",
    "evaluation_close",
    "baseline_signal_present",
    "challenger_signal_present",
    "comparison_group",
    "evaluation_status",
    "baseline_engine_version",
    "challenger_engine_version",
]


class MalformedLedgerError(ValueError):
    """STEP 3 Ledger가 필수 컬럼을 갖추지 않아 안전하게 처리할 수 없을 때 발생한다."""


@dataclass
class DualForwardReturnRecord:
    """DUAL Forward Return Ledger 1행. 필드 순서가 곧 CSV 컬럼 순서."""

    trade_date: str
    stock_code: str
    stock_name: str
    evaluation_close: float | None
    baseline_signal_present: bool
    challenger_signal_present: bool
    comparison_group: str
    horizon: int
    target_date: str
    target_close: float
    forward_return: float
    return_status: str
    baseline_engine_version: str
    challenger_engine_version: str
    source_commit: str
    created_at: str


FORWARD_RETURN_FIELDS = [f.name for f in fields(DualForwardReturnRecord)]


def compute_dual_forward_return(
    price_df: pd.DataFrame | None,
    trade_date: str,
    evaluation_close: float | None,
    horizon: int,
) -> dict[str, object]:
    """evaluation_close 기준 거래일 위치 + horizon 거래일 종가로 Forward Return(%)을 계산한다.

    미래 가격이 아직 없거나 trade_date를 가격 데이터에서 찾을 수 없으면
    target_date/target_close/forward_return을 임의로 채우지 않고 NOT_AVAILABLE을 반환한다.
    """
    not_available: dict[str, object] = {
        "target_date": None,
        "target_close": None,
        "forward_return": None,
        "return_status": RETURN_STATUS_NOT_AVAILABLE,
    }

    if price_df is None or price_df.empty or "date" not in price_df or "close" not in price_df:
        return not_available
    if evaluation_close is None or pd.isna(evaluation_close) or float(evaluation_close) <= 0:
        return not_available

    dates = pd.to_datetime(price_df["date"]).reset_index(drop=True)
    closes = pd.to_numeric(price_df["close"], errors="coerce").reset_index(drop=True)
    target_ts = pd.Timestamp(trade_date).normalize()

    matches = dates[dates.dt.normalize() == target_ts].index
    if len(matches) == 0:
        return not_available
    idx = int(matches[0])

    future_idx = idx + horizon
    if future_idx >= len(closes):
        return not_available

    target_close = closes.iloc[future_idx]
    if pd.isna(target_close):
        return not_available

    target_date_val = dates.iloc[future_idx]
    target_date_str = (
        target_date_val.strftime("%Y-%m-%d") if hasattr(target_date_val, "strftime") else str(target_date_val)[:10]
    )
    forward_return = (float(target_close) / float(evaluation_close) - 1.0) * 100.0

    return {
        "target_date": target_date_str,
        "target_close": float(target_close),
        "forward_return": forward_return,
        "return_status": RETURN_STATUS_AVAILABLE,
    }


class DualForwardReturnStore:
    """DUAL Forward Return CSV 저장소.

    append-only. 동일 (trade_date, stock_code, baseline_engine_version,
    challenger_engine_version, horizon) 중복 방지. AVAILABLE 상태 record만 저장 허용
    (NOT_AVAILABLE은 이 저장소에 영구 row로 고정하지 않는 설계).
    """

    def __init__(self, path: Path = DEFAULT_FORWARD_RETURN_PATH) -> None:
        self.path = Path(path)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=FORWARD_RETURN_FIELDS)
        return pd.read_csv(self.path, dtype={"stock_code": str, "trade_date": str})

    def exists(
        self,
        trade_date: str,
        stock_code: str,
        baseline_engine_version: str,
        challenger_engine_version: str,
        horizon: int,
    ) -> bool:
        existing = self.load()
        if existing.empty:
            return False
        match = existing[
            (existing["trade_date"].astype(str) == str(trade_date))
            & (existing["stock_code"].astype(str) == str(stock_code))
            & (existing["baseline_engine_version"].astype(str) == str(baseline_engine_version))
            & (existing["challenger_engine_version"].astype(str) == str(challenger_engine_version))
            & (existing["horizon"].astype(int) == int(horizon))
        ]
        return not match.empty

    def add(self, record: DualForwardReturnRecord) -> bool:
        """record를 저장한다. AVAILABLE이 아니면 저장을 거부한다 (fail-closed).

        이미 동일 key 기록이 있으면 저장하지 않고 False 반환. 기존 기록은 절대
        덮어쓰거나 삭제하지 않는다.
        """
        if record.return_status != RETURN_STATUS_AVAILABLE:
            raise ValueError(
                f"NOT_AVAILABLE record는 immutable evidence로 저장할 수 없습니다: {record!r}"
            )
        if self.exists(
            record.trade_date,
            record.stock_code,
            record.baseline_engine_version,
            record.challenger_engine_version,
            record.horizon,
        ):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = pd.DataFrame([asdict(record)], columns=FORWARD_RETURN_FIELDS)
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        row.to_csv(self.path, mode="a", header=write_header, index=False)
        return True


def _load_price_df(
    ticker: str,
    price_map: dict[str, pd.DataFrame] | None,
    provider: CsvDataProvider,
) -> pd.DataFrame | None:
    if price_map is not None:
        return price_map.get(ticker)
    try:
        return provider.load(ticker)
    except (FileNotFoundError, ValueError):
        return None


def _validate_ledger_columns(ledger_df: pd.DataFrame) -> None:
    missing = [col for col in REQUIRED_LEDGER_COLUMNS if col not in ledger_df.columns]
    if missing:
        raise MalformedLedgerError(f"STEP 3 Ledger에 필수 컬럼이 없습니다: {missing}")


def build_forward_return_records(
    ledger_df: pd.DataFrame,
    forward_store: DualForwardReturnStore,
    price_map: dict[str, pd.DataFrame] | None = None,
    provider: CsvDataProvider | None = None,
    source_commit: str | None = None,
    created_at: str | None = None,
) -> tuple[list[DualForwardReturnRecord], dict[str, int]]:
    """STEP 3 Ledger(읽기 전용)를 기준으로 신규 Forward Return record를 만든다 (저장은 하지 않음).

    Ledger 자체는 절대 수정하지 않는다. NOT_EVALUABLE 또는 evaluation_close가 없는 행은
    안전하게 건너뛴다(재계산/추정하지 않음).
    """
    stats = {
        "ledger_rows": 0 if ledger_df is None else len(ledger_df),
        "skipped_not_evaluable": 0,
        "already_recorded": 0,
        "available": 0,
        "not_available": 0,
        "missing_price": 0,
    }

    if ledger_df is None or ledger_df.empty:
        return [], stats

    _validate_ledger_columns(ledger_df)

    provider = provider or CsvDataProvider(config.DATA_RAW_DIR)
    resolved_commit = source_commit if source_commit is not None else get_source_commit()
    resolved_created_at = created_at or datetime.now(timezone.utc).isoformat()

    price_cache: dict[str, pd.DataFrame | None] = {}
    records: list[DualForwardReturnRecord] = []

    for _, row in ledger_df.iterrows():
        evaluation_status = str(row.get("evaluation_status"))
        evaluation_close = row.get("evaluation_close")
        if evaluation_status == "NOT_EVALUABLE" or evaluation_close is None or pd.isna(evaluation_close):
            stats["skipped_not_evaluable"] += 1
            continue

        trade_date = str(row["trade_date"])
        stock_code = str(row["stock_code"])
        baseline_ver = str(row["baseline_engine_version"])
        challenger_ver = str(row["challenger_engine_version"])

        if stock_code not in price_cache:
            price_cache[stock_code] = _load_price_df(stock_code, price_map, provider)
        price_df = price_cache[stock_code]
        if price_df is None:
            stats["missing_price"] += 1

        for horizon in FORWARD_HORIZONS:
            if forward_store.exists(trade_date, stock_code, baseline_ver, challenger_ver, horizon):
                stats["already_recorded"] += 1
                continue

            detail = compute_dual_forward_return(price_df, trade_date, evaluation_close, horizon)
            if detail["return_status"] != RETURN_STATUS_AVAILABLE:
                stats["not_available"] += 1
                continue

            stats["available"] += 1
            records.append(
                DualForwardReturnRecord(
                    trade_date=trade_date,
                    stock_code=stock_code,
                    stock_name=str(row["stock_name"]),
                    evaluation_close=float(evaluation_close),
                    baseline_signal_present=bool(row["baseline_signal_present"]),
                    challenger_signal_present=bool(row["challenger_signal_present"]),
                    comparison_group=str(row["comparison_group"]),
                    horizon=int(horizon),
                    target_date=str(detail["target_date"]),
                    target_close=float(detail["target_close"]),
                    forward_return=float(detail["forward_return"]),
                    return_status=RETURN_STATUS_AVAILABLE,
                    baseline_engine_version=baseline_ver,
                    challenger_engine_version=challenger_ver,
                    source_commit=resolved_commit,
                    created_at=resolved_created_at,
                )
            )

    return records, stats


def run_dual_shadow_forward_returns_update(
    ledger_store: DualShadowLedgerStore | None = None,
    forward_store: DualForwardReturnStore | None = None,
    price_map: dict[str, pd.DataFrame] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """DUAL Shadow STEP 4 실행. STEP 3 Ledger는 읽기만 하며 절대 수정하지 않는다."""
    ledger_store = ledger_store or DualShadowLedgerStore(path=DEFAULT_LEDGER_PATH)
    forward_store = forward_store or DualForwardReturnStore()

    ledger_df = ledger_store.load()
    records, stats = build_forward_return_records(
        ledger_df,
        forward_store=forward_store,
        price_map=price_map,
    )

    saved = 0
    if not dry_run:
        for record in records:
            if forward_store.add(record):
                saved += 1
    stats["saved"] = saved
    return stats
