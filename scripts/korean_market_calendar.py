"""Korean market (KRX) trading-day calendar for the STEP 7 daily scheduler.

No external dependency is used (policy: minimal change surface). Trading-day
determination is:

    weekday (Mon-Fri) AND not in the explicit KRX holiday table

Holiday table management:
- The embedded table below is the built-in baseline. Every date on or before
  2026-09-03 was empirically verified against the real trading dates in
  data/raw/005930.csv (삼성전자 trades every KRX session; a missing weekday is
  a market holiday). This empirically captures ad-hoc closures such as
  2024-10-01 (국군의날 임시공휴일), election days (2024-04-10, 2025-06-03,
  2026-06-03), 2026-07-17 (제헌절), substitute holidays (2026-05-25 석가탄신일
  대체 — applied), and confirms non-closures (2026-06-08 현충일 대체 미적용 —
  verified TRADING day).
- Dates after 2026-09-03 are rule-based (공휴일/대체공휴일 + KRX 연말 휴장일)
  and cover through 2027-02. The table MUST be reviewed annually against the
  KRX 휴장일 공지.
- Operators can supply an authoritative override file (default:
  data/krx_holidays.json) without a code change:

      {"holidays": ["YYYY-MM-DD", ...]}

  When the file exists it REPLACES the embedded table entirely. If the file
  is missing or malformed, the embedded table is used (malformed files
  produce a warning so the failure is visible instead of silent).

A weekday absent from the table is treated as a trading day (fail-open), so an
out-of-date table never blocks operations; correctness for known holidays is
guaranteed by the explicit table plus the override file.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OVERRIDE_PATH = "data/krx_holidays.json"

# Verified against data/raw/005930.csv for 2024-01-01..2026-09-03.
# 2026-09-04 이후 날짜는 규칙 기반이며 KRX 공지로 확정/갱신한다.
EMBEDDED_HOLIDAYS: frozenset[str] = frozenset(
    {
        # 2024 (verified)
        "2024-01-01",  # 신정
        "2024-02-09",  # 설날 연휴
        "2024-02-12",  # 설날 대체공휴일
        "2024-03-01",  # 3·1절
        "2024-04-10",  # 국회의원선거일
        "2024-05-01",  # 근로자의날
        "2024-05-06",  # 어린이날 대체공휴일
        "2024-05-15",  # 석가탄신일
        "2024-06-06",  # 현충일
        "2024-08-15",  # 광복절
        "2024-09-16",  # 추석 연휴
        "2024-09-17",  # 추석
        "2024-09-18",  # 추석 연휴
        "2024-10-01",  # 국군의날 임시공휴일
        "2024-10-03",  # 개천절
        "2024-10-09",  # 한글날
        "2024-12-25",  # 성탄절
        "2024-12-31",  # 연말 휴장일
        # 2025 (verified)
        "2025-01-01",  # 신정
        "2025-01-27",  # 임시공휴일 (설날 전날)
        "2025-01-28",  # 설날 연휴
        "2025-01-29",  # 설날
        "2025-01-30",  # 설날 연휴
        "2025-03-03",  # 3·1절 대체공휴일
        "2025-05-01",  # 근로자의날
        "2025-05-05",  # 어린이날 / 석가탄신일
        "2025-05-06",  # 대체공휴일
        "2025-06-03",  # 대통령선거일
        "2025-06-06",  # 현충일
        "2025-08-15",  # 광복절
        "2025-10-03",  # 개천절
        "2025-10-06",  # 추석 연휴
        "2025-10-07",  # 추석
        "2025-10-08",  # 추석 대체공휴일
        "2025-10-09",  # 한글날
        "2025-12-25",  # 성탄절
        "2025-12-31",  # 연말 휴장일
        # 2026 up to 2026-09-03 (verified)
        "2026-01-01",  # 신정
        "2026-02-16",  # 설날 연휴
        "2026-02-17",  # 설날
        "2026-02-18",  # 설날 연휴
        "2026-03-02",  # 3·1절 대체공휴일
        "2026-05-01",  # 근로자의날
        "2026-05-05",  # 어린이날
        "2026-05-25",  # 석가탄신일 대체공휴일 (verified CLOSED)
        "2026-06-03",  # 지방선거일
        "2026-07-17",  # 제헌절 (verified CLOSED)
        "2026-08-17",  # 광복절 대체공휴일
        # 2026 after 2026-09-03 (rule-based; confirm with KRX 공지)
        "2026-09-24",  # 추석 연휴
        "2026-09-25",  # 추석
        "2026-09-28",  # 추석 대체공휴일
        "2026-10-05",  # 개천절 대체공휴일
        "2026-10-09",  # 한글날
        "2026-12-25",  # 성탄절
        "2026-12-31",  # 연말 휴장일
        # 2027 (rule-based; confirm with KRX 공지)
        "2027-01-01",  # 신정
        "2027-02-05",  # 설날 연휴
        "2027-02-08",  # 설날 대체공휴일
    }
)


def _parse_dates(values: Iterable[str]) -> frozenset[date]:
    parsed: set[date] = set()
    for value in values:
        parsed.add(date.fromisoformat(str(value)))
    return frozenset(parsed)


def load_holidays(repo_root: Path = ROOT_DIR, override_path: Path | None = None) -> tuple[frozenset[date], str | None]:
    """Return (holidays, warning).

    If the override file exists it replaces the embedded table. A malformed
    override falls back to the embedded table and returns a warning string so
    the misconfiguration is visible to operators.
    """
    path = Path(override_path) if override_path is not None else Path(repo_root) / DEFAULT_OVERRIDE_PATH
    if not path.exists():
        return _parse_dates(EMBEDDED_HOLIDAYS), None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["holidays"]
        if not isinstance(values, list):
            raise TypeError('"holidays" must be a list of YYYY-MM-DD strings')
        return _parse_dates(values), None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _parse_dates(EMBEDDED_HOLIDAYS), f"HOLIDAY_OVERRIDE_INVALID: {path}: {exc}; embedded holiday table used"


def is_trading_day(day: date, holidays: frozenset[date]) -> bool:
    """A trading day is a weekday that is not an explicit KRX holiday."""
    return day.weekday() < 5 and day not in holidays
