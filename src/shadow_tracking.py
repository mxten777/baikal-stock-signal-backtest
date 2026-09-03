"""
Shadow STEP 1 — 신규 Signal Shadow 운영 데이터 구조 및 저장 기능.

목적:
  Baseline(과거 검증, GO 판정 완료)은 더 이상 수정하지 않고,
  앞으로 새롭게 발생하는 Signal을 Shadow Record로 기록만 한다.

이 모듈에서 하지 않는 것 (다음 STEP 이후 범위):
  - 5D/10D/20D 성과 계산
  - 실매수 / 자동매매
  - 대시보드 / 알림
  - threshold, weight, ROBUST_FILTER 등 기존 조건 변경

원칙:
  1. Signal 발생 당시 foreign_status를 Snapshot으로 저장하고 이후 변경하지 않는다.
  2. Foreign NEGATIVE 종목도 삭제하지 않고 EXCLUDED로 보존한다.
  3. 동일 (stock_code, signal_date) 조합의 중복 기록을 방지한다 (append-only, 최초 기록 유지).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.config import OUTPUT_DIR

# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────
FOREIGN_STATUS_POSITIVE = "POSITIVE"
FOREIGN_STATUS_NEUTRAL = "NEUTRAL"
FOREIGN_STATUS_NEGATIVE = "NEGATIVE"
VALID_FOREIGN_STATUSES = {FOREIGN_STATUS_POSITIVE, FOREIGN_STATUS_NEUTRAL, FOREIGN_STATUS_NEGATIVE}

DECISION_CANDIDATE = "CANDIDATE"
DECISION_EXCLUDED = "EXCLUDED"

EXCLUSION_REASON_FOREIGN_NEGATIVE = "FOREIGN_NEGATIVE"

STATUS_OPEN = "OPEN"
STATUS_5D_DONE = "5D_DONE"
STATUS_10D_DONE = "10D_DONE"
STATUS_COMPLETE = "COMPLETE"

DEFAULT_SHADOW_STORE_PATH = OUTPUT_DIR / "shadow_signal_records.csv"

# Shadow STEP 3 — Forward Return 추적
FORWARD_HORIZONS = (5, 10, 20)
RETURN_FIELD_BY_HORIZON = {5: "return_5d", 10: "return_10d", 20: "return_20d"}

# Shadow STEP 4 — Benchmark / Excess Return
BENCHMARK_FIELD_BY_HORIZON = {
    5: "benchmark_return_5d",
    10: "benchmark_return_10d",
    20: "benchmark_return_20d",
}
EXCESS_FIELD_BY_HORIZON = {5: "excess_5d", 10: "excess_10d", 20: "excess_20d"}
# 기존 백테스트와 동일하게 KOSPI→KS11, KOSDAQ→KQ11만 사용한다 (임의 추론 금지).
BENCHMARK_SYMBOL_BY_MARKET = {
    "KS11": "KS11",
    "KOSPI": "KS11",
    "KQ11": "KQ11",
    "KOSDAQ": "KQ11",
}

UPDATABLE_FIELDS = (
    "return_5d",
    "return_10d",
    "return_20d",
    "status",
    "benchmark_return_5d",
    "benchmark_return_10d",
    "benchmark_return_20d",
    "excess_5d",
    "excess_10d",
    "excess_20d",
)
IMMUTABLE_FIELDS = (
    "stock_code",
    "stock_name",
    "market",
    "signal_date",
    "signal_price",
    "signal_score",
    "foreign_status",
    "decision",
    "exclusion_reason",
    "created_at",
)
# 부동소수 저장/재계산 오차 허용치(%p). 이보다 크게 다르면 정합성 문제로 간주한다.
RETURN_MISMATCH_TOLERANCE = 1e-6


@dataclass
class ShadowRecord:
    """Shadow 운영 대상 Signal 1건. 필드 순서가 곧 CSV 컬럼 순서."""

    stock_code: str
    stock_name: str
    market: str
    signal_date: str
    signal_price: float
    signal_score: float
    foreign_status: str
    decision: str
    exclusion_reason: str | None
    created_at: str
    status: str = STATUS_OPEN
    # Shadow STEP 3에서 계산하는 Forward Return (퍼센트 단위)
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
    # Shadow STEP 4에서 계산하는 Benchmark / Excess Return (퍼센트 단위)
    benchmark_return_5d: float | None = None
    benchmark_return_10d: float | None = None
    benchmark_return_20d: float | None = None
    excess_5d: float | None = None
    excess_10d: float | None = None
    excess_20d: float | None = None


SHADOW_RECORD_FIELDS = [f.name for f in fields(ShadowRecord)]


def decide_candidate(foreign_status: str) -> tuple[str, str | None]:
    """[확정 규칙] Technical Signal 유지 → Foreign 확인 → NEGATIVE면 매수 후보 제외.

    Foreign 판정 로직 자체(POSITIVE/NEUTRAL/NEGATIVE 산출)는 변경하지 않으며,
    이미 산출된 foreign_status를 입력받아 CANDIDATE/EXCLUDED만 결정한다.
    """
    if foreign_status not in VALID_FOREIGN_STATUSES:
        raise ValueError(f"unknown foreign_status: {foreign_status!r}")
    if foreign_status == FOREIGN_STATUS_NEGATIVE:
        return DECISION_EXCLUDED, EXCLUSION_REASON_FOREIGN_NEGATIVE
    return DECISION_CANDIDATE, None


def build_shadow_record(
    stock_code: str,
    stock_name: str,
    market: str,
    signal_date: str,
    signal_price: float,
    signal_score: float,
    foreign_status: str,
    created_at: str | None = None,
) -> ShadowRecord:
    """Signal 1건 + 당시 foreign_status(Snapshot)으로 ShadowRecord를 생성한다."""
    decision, exclusion_reason = decide_candidate(foreign_status)
    return ShadowRecord(
        stock_code=stock_code,
        stock_name=stock_name,
        market=market,
        signal_date=str(signal_date),
        signal_price=signal_price,
        signal_score=signal_score,
        foreign_status=foreign_status,
        decision=decision,
        exclusion_reason=exclusion_reason,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        status=STATUS_OPEN,
    )


def compute_forward_returns(
    price_df: pd.DataFrame,
    signal_date: str,
    signal_price: float,
) -> dict[str, float | None] | None:
    """signal_date의 거래일 위치를 기준으로 +5/+10/+20 거래일 종가로 Forward Return(%)을 계산한다.

    - 거래일은 해당 종목 가격 데이터의 행 순서(자연스럽게 주말/공휴일 제외)를 사용한다.
    - 미래 데이터가 부족한 구간은 None으로 남기며, 가장 최근 종가로 대체하지 않는다.
    - signal_date가 가격 데이터에 없으면 None을 반환한다(해당 record 미갱신).
    """
    if price_df is None or price_df.empty or "date" not in price_df or "close" not in price_df:
        return None
    if signal_price is None or pd.isna(signal_price) or float(signal_price) <= 0:
        return None

    dates = pd.to_datetime(price_df["date"]).reset_index(drop=True)
    closes = pd.to_numeric(price_df["close"], errors="coerce").reset_index(drop=True)
    target = pd.Timestamp(signal_date).normalize()

    matches = dates[dates.dt.normalize() == target].index
    if len(matches) == 0:
        return None
    idx = int(matches[0])

    result: dict[str, float | None] = {field: None for field in RETURN_FIELD_BY_HORIZON.values()}
    for horizon, field in RETURN_FIELD_BY_HORIZON.items():
        future_idx = idx + horizon
        if future_idx >= len(closes):
            continue
        close = closes.iloc[future_idx]
        if pd.isna(close):
            continue
        result[field] = (float(close) / float(signal_price) - 1.0) * 100.0
    return result


def normalize_market(market: object) -> str | None:
    """Shadow Record의 market 값을 Benchmark 심볼(KS11/KQ11)로 정규화한다.

    알 수 없는 값이면 None을 반환하고, 호출자가 해당 record를 건드리지 않도록 한다.
    """
    if market is None or (not isinstance(market, str) and pd.isna(market)):
        return None
    return BENCHMARK_SYMBOL_BY_MARKET.get(str(market).strip().upper())


def compute_benchmark_returns(
    benchmark_df: pd.DataFrame,
    signal_date: str,
) -> dict[str, float | None] | None:
    """signal_date의 Benchmark 거래일 위치를 기준으로 +5/+10/+20 거래일 수익률(%)을 계산한다.

    - Benchmark 자체의 거래일(행 순서)을 사용하므로 주말/공휴일은 자연스럽게 건너뛴다.
    - 미래 데이터가 부족하면 해당 horizon은 None으로 남기며 최근 값으로 대체하지 않는다.
    - signal_date가 Benchmark 데이터에 없으면 None을 반환한다(해당 record 미갱신).
    """
    if (
        benchmark_df is None
        or benchmark_df.empty
        or "date" not in benchmark_df
        or "close" not in benchmark_df
    ):
        return None

    dates = pd.to_datetime(benchmark_df["date"]).reset_index(drop=True)
    closes = pd.to_numeric(benchmark_df["close"], errors="coerce").reset_index(drop=True)
    target = pd.Timestamp(signal_date).normalize()

    matches = dates[dates.dt.normalize() == target].index
    if len(matches) == 0:
        return None
    idx = int(matches[0])

    base_close = closes.iloc[idx]
    if pd.isna(base_close) or float(base_close) <= 0:
        return None

    result: dict[str, float | None] = {field: None for field in BENCHMARK_FIELD_BY_HORIZON.values()}
    for horizon, field in BENCHMARK_FIELD_BY_HORIZON.items():
        future_idx = idx + horizon
        if future_idx >= len(closes):
            continue
        close = closes.iloc[future_idx]
        if pd.isna(close):
            continue
        result[field] = (float(close) / float(base_close) - 1.0) * 100.0
    return result


def compute_excess(stock_return: object, benchmark_return: object) -> float | None:
    """stock/benchmark 수익률이 모두 존재할 때만 Excess(%)를 계산한다."""
    if stock_return is None or benchmark_return is None:
        return None
    if pd.isna(stock_return) or pd.isna(benchmark_return):
        return None
    return float(stock_return) - float(benchmark_return)


def resolve_status(
    return_5d: float | None,
    return_10d: float | None,
    return_20d: float | None,
) -> str:
    """계산된 Forward Return 조합으로 status를 결정한다.

    중간 구간이 비어 있으면 status를 앞당기지 않는다(연속 완료분까지만 인정).
    """
    has_5 = return_5d is not None and not pd.isna(return_5d)
    has_10 = return_10d is not None and not pd.isna(return_10d)
    has_20 = return_20d is not None and not pd.isna(return_20d)

    if has_5 and has_10 and has_20:
        return STATUS_COMPLETE
    if has_5 and has_10:
        return STATUS_10D_DONE
    if has_5:
        return STATUS_5D_DONE
    return STATUS_OPEN


class ShadowStore:
    """Shadow Record CSV 저장소. append-only, 동일 (stock_code, signal_date) 중복 방지."""


    def __init__(self, path: Path = DEFAULT_SHADOW_STORE_PATH) -> None:
        self.path = Path(path)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=SHADOW_RECORD_FIELDS)
        return pd.read_csv(self.path, dtype={"stock_code": str})

    def exists(self, stock_code: str, signal_date: str) -> bool:
        existing = self.load()
        if existing.empty:
            return False
        match = existing[
            (existing["stock_code"] == stock_code)
            & (existing["signal_date"].astype(str) == str(signal_date))
        ]
        return not match.empty

    def add(self, record: ShadowRecord) -> bool:
        """record를 저장한다. 이미 동일 (stock_code, signal_date) 기록이 있으면 저장하지 않고 False 반환.

        기존 기록은 절대 덮어쓰거나 삭제하지 않는다 (Snapshot 보존 원칙).
        """
        if self.exists(record.stock_code, record.signal_date):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = pd.DataFrame([asdict(record)], columns=SHADOW_RECORD_FIELDS)
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        row.to_csv(self.path, mode="a", header=write_header, index=False)
        return True

    def record_signal(
        self,
        stock_code: str,
        stock_name: str,
        market: str,
        signal_date: str,
        signal_price: float,
        signal_score: float,
        foreign_status: str,
        created_at: str | None = None,
    ) -> ShadowRecord | None:
        """ShadowRecord를 생성하고 저장한다. 중복이면 저장하지 않고 None을 반환한다."""
        record = build_shadow_record(
            stock_code=stock_code,
            stock_name=stock_name,
            market=market,
            signal_date=signal_date,
            signal_price=signal_price,
            signal_score=signal_score,
            foreign_status=foreign_status,
            created_at=created_at,
        )
        return record if self.add(record) else None

    def write_atomic(self, df: pd.DataFrame) -> None:
        """임시 파일에 기록한 뒤 replace로 교체하여 부분 손상 위험을 줄인다."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            df.to_csv(tmp_path, index=False)
            os.replace(tmp_path, self.path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def update_performance(self, updates: dict[tuple[str, str], dict[str, object]]) -> int:
        """성과 필드(return_5d/10d/20d, status)만 갱신하고 atomic write로 저장한다.

        키는 (stock_code, signal_date). Signal 판정 관련 불변 필드는 건드리지 않는다.
        """
        if not updates:
            return 0
        df = self.load()
        if df.empty:
            return 0

        keys = list(zip(df["stock_code"].astype(str), df["signal_date"].astype(str)))
        changed = 0
        for pos, key in enumerate(keys):
            patch = updates.get(key)
            if not patch:
                continue
            row_changed = False
            for field, value in patch.items():
                if field not in UPDATABLE_FIELDS:
                    raise ValueError(f"수정 불가 필드입니다: {field!r}")
                current = df.iloc[pos][field] if field in df.columns else None
                if _is_same_value(current, value):
                    continue
                df.iloc[pos, df.columns.get_loc(field)] = value
                row_changed = True
            if row_changed:
                changed += 1

        if changed:
            self.write_atomic(df)
        return changed


def _is_same_value(current: object, new: object) -> bool:
    current_na = current is None or bool(pd.isna(current))
    new_na = new is None or bool(pd.isna(new))
    if current_na and new_na:
        return True
    if current_na or new_na:
        return False
    if isinstance(new, float):
        return abs(float(current) - float(new)) <= RETURN_MISMATCH_TOLERANCE
    return str(current) == str(new)

