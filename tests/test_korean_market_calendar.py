import json
from datetime import date

from scripts.korean_market_calendar import EMBEDDED_HOLIDAYS, is_trading_day, load_holidays


def test_weekend_is_not_trading_day():
    holidays = frozenset()
    assert is_trading_day(date(2026, 9, 5), holidays) is False  # Saturday
    assert is_trading_day(date(2026, 9, 6), holidays) is False  # Sunday


def test_plain_weekday_is_trading_day():
    assert is_trading_day(date(2026, 9, 4), frozenset()) is True  # Friday
    assert is_trading_day(date(2026, 9, 7), frozenset()) is True  # Monday


def test_embedded_holidays_are_not_trading_days():
    holidays = frozenset(date.fromisoformat(value) for value in EMBEDDED_HOLIDAYS)
    assert is_trading_day(date(2026, 1, 1), holidays) is False   # 신정
    assert is_trading_day(date(2026, 2, 17), holidays) is False  # 설날
    assert is_trading_day(date(2026, 9, 25), holidays) is False  # 추석
    assert is_trading_day(date(2026, 12, 25), holidays) is False
    assert is_trading_day(date(2026, 12, 31), holidays) is False  # 연말 휴장일


def test_embedded_holiday_weekday_sanity():
    # 표에 있는 모든 날짜는 평일이어야 한다 (주말은 어차피 비거래일).
    for value in EMBEDDED_HOLIDAYS:
        assert date.fromisoformat(value).weekday() < 5, value


def test_rule_based_future_holidays_after_data_end():
    holidays = frozenset(date.fromisoformat(value) for value in EMBEDDED_HOLIDAYS)
    assert is_trading_day(date(2026, 9, 24), holidays) is False  # 추석 연휴
    assert is_trading_day(date(2026, 9, 28), holidays) is False  # 추석 대체공휴일
    assert is_trading_day(date(2026, 10, 5), holidays) is False  # 개천절 대체공휴일
    assert is_trading_day(date(2026, 10, 9), holidays) is False  # 한글날
    assert is_trading_day(date(2027, 1, 1), holidays) is False
    assert is_trading_day(date(2027, 2, 5), holidays) is False   # 설날 연휴
    assert is_trading_day(date(2027, 2, 8), holidays) is False   # 설날 대체공휴일


def test_substitute_holiday_nuances_verified_against_real_data():
    # 실데이터(data/raw/005930.csv)로 검증된 대체공휴일 차이:
    # 석가탄신일(일요일 겹침) 대체공휴일은 휴장, 현충일(토요일 겹침) 대체는 미적용.
    holidays = frozenset(date.fromisoformat(value) for value in EMBEDDED_HOLIDAYS)
    assert is_trading_day(date(2026, 5, 25), holidays) is False  # 석가탄신일 대체 (CLOSED in data)
    assert is_trading_day(date(2026, 6, 8), holidays) is True    # 현충일 대체 미적용 (TRADING in data)


def test_default_load_uses_embedded_table_when_no_override(tmp_path):
    holidays, warning = load_holidays(tmp_path)
    assert warning is None
    assert date(2026, 9, 25) in holidays
    assert is_trading_day(date(2026, 9, 4), holidays) is True


def test_override_file_replaces_embedded_table(tmp_path):
    override = tmp_path / "data" / "krx_holidays.json"
    override.parent.mkdir(parents=True)
    override.write_text(json.dumps({"holidays": ["2026-09-04"]}), encoding="utf-8")
    holidays, warning = load_holidays(tmp_path)
    assert warning is None
    assert holidays == frozenset({date(2026, 9, 4)})
    assert is_trading_day(date(2026, 9, 4), holidays) is False  # 임시휴장일 등록
    assert is_trading_day(date(2026, 9, 25), holidays) is True  # embedded table replaced


def test_malformed_override_falls_back_to_embedded_with_warning(tmp_path):
    override = tmp_path / "data" / "krx_holidays.json"
    override.parent.mkdir(parents=True)
    override.write_text("{not valid json", encoding="utf-8")
    holidays, warning = load_holidays(tmp_path)
    assert warning is not None and "HOLIDAY_OVERRIDE_INVALID" in warning
    assert date(2026, 9, 25) in holidays  # embedded fallback
