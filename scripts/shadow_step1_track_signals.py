"""Shadow STEP 1 — 신규 Signal 발생 시 Foreign 상태 Snapshot과 함께 Shadow Record로 저장.

이번 STEP에서 변경하지 않는 것:
  - 기존 Technical Signal 생성 로직 (src.signal_engine)
  - 기존 Foreign 판정 로직 (scripts.step1b_flow_verification.classify_flow, +-0.20 임계값)
  - Baseline threshold / weight / ROBUST_FILTER

이번 STEP에서 하는 것:
  - 위 기존 로직들을 그대로 재사용해 신규 Signal 1건을 Shadow Record로 저장하는
    최소 연결 함수만 추가한다 (5D/10D/20D 성과 계산, 실매수, 알림은 구현하지 않음).

실행 예시: python -m scripts.shadow_step1_track_signals
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.step1b_flow_verification import classify_flow  # 기존 Foreign 판정 로직 재사용 (변경 없음)
from src.shadow_tracking import (
    FOREIGN_STATUS_NEUTRAL,
    ShadowRecord,
    ShadowStore,
)


def foreign_status_from_ratio(ratio: float) -> str:
    """기존 classify_flow(+-0.20)를 그대로 사용하고, NO_DATA는 중립(NEUTRAL)으로 취급한다.

    (STEP 1-B에서도 investor 데이터 미존재 시 중립 처리하는 것과 동일한 관례를 따른다.)
    """
    flow_class = classify_flow(ratio)
    return FOREIGN_STATUS_NEUTRAL if flow_class == "NO_DATA" else flow_class


def track_new_signal(
    stock_code: str,
    stock_name: str,
    market: str,
    signal_date: str,
    signal_price: float,
    signal_score: float,
    foreign_5d_ratio: float,
    store: ShadowStore | None = None,
) -> ShadowRecord | None:
    """신규 Signal 1건 + 당시 foreign_5d_ratio를 받아 Shadow Record로 저장한다.

    반환값: 새로 저장되면 ShadowRecord, 이미 존재하는 (stock_code, signal_date)면 None.
    """
    store = store or ShadowStore()
    foreign_status = foreign_status_from_ratio(foreign_5d_ratio)
    return store.record_signal(
        stock_code=stock_code,
        stock_name=stock_name,
        market=market,
        signal_date=signal_date,
        signal_price=signal_price,
        signal_score=signal_score,
        foreign_status=foreign_status,
    )


def track_new_signals(signals: pd.DataFrame, store: ShadowStore | None = None) -> list[ShadowRecord]:
    """signals 프레임(각 행: stock_code/stock_name/market/signal_date/signal_price/signal_score/foreign_5d_ratio)을
    순회하며 Shadow Record로 저장한다. 중복은 건너뛴다.
    """
    store = store or ShadowStore()
    saved: list[ShadowRecord] = []
    for _, row in signals.iterrows():
        record = track_new_signal(
            stock_code=row["stock_code"],
            stock_name=row["stock_name"],
            market=row["market"],
            signal_date=row["signal_date"],
            signal_price=row["signal_price"],
            signal_score=row["signal_score"],
            foreign_5d_ratio=row["foreign_5d_ratio"],
            store=store,
        )
        if record is not None:
            saved.append(record)
    return saved


if __name__ == "__main__":
    print("Shadow STEP 1: 데이터 구조/저장 기능만 제공합니다.")
    print("신규 Signal 자동 탐지/실매수 연동은 다음 STEP에서 구현합니다.")
