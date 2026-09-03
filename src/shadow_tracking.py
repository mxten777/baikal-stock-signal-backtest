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
    # 향후 STEP(5D/10D/20D 성과 추적)을 위한 예약 필드 — 이번 STEP에서는 계산하지 않는다.
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
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
