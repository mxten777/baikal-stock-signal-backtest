"""
STEP 11-A — Samsung 실제 DART API 원본 숫자 출력 (진단용)

실행: set DART_API_KEY=<key> && python -m scripts.step11a_samsung_raw_check

출력 항목 (Q2·Q3 각 계정):
  account_nm / account_id / fs_div / sj_div / thstrm_amount / thstrm_add_amount
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("[ERROR] DART_API_KEY 환경변수 미설정")
        sys.exit(1)

    try:
        import OpenDartReader  # type: ignore
        import pandas as pd
    except ImportError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    dart = OpenDartReader(key)
    TICKER = "005930"
    YEAR = 2024
    ACCOUNTS = ["매출액", "영업이익", "당기순이익"]
    WANT_COLS = ["account_nm", "account_id", "fs_div", "sj_div",
                 "thstrm_amount", "thstrm_add_amount"]

    for reprt_code, q_label in [("11012", "Q2(반기보고서)"), ("11014", "Q3(3분기보고서)")]:
        print("=" * 72)
        print(f"삼성전자 {YEAR} {q_label}  (reprt_code={reprt_code}, fs_div=CFS)")
        print("=" * 72)
        try:
            raw = dart.finstate_all(TICKER, YEAR, reprt_code=reprt_code, fs_div="CFS")
        except Exception as e:
            print(f"[FAIL] {e}")
            continue
        if raw is None or raw.empty:
            print("[FAIL] 데이터 없음")
            continue

        # 실제 컬럼 목록 출력 (fs_div 존재 여부 포함)
        print(f"  실제 컬럼: {list(raw.columns)}")
        for c in ["fs_div", "sj_div", "account_id", "thstrm_add_amount"]:
            status = "✓" if c in raw.columns else "✗ 없음"
            print(f"    {c}: {status}")
        print()

        # 존재하는 컬럼만 선택
        show_cols = [c for c in WANT_COLS if c in raw.columns]
        for acct in ACCOUNTS:
            rows = raw[raw["account_nm"] == acct]
            if rows.empty:
                print(f"  [{acct}] 행 없음")
            else:
                print(f"  [{acct}]  (총 {len(rows)}개 행)")
                print(rows[show_cols].to_string(index=False))
        print()

    # 교차검증: Q2 thstrm_amount + Q1 thstrm_amount vs Q2 thstrm_add_amount
    print("=" * 72)
    print("교차검증: Q1+Q2 thstrm_amount vs Q2 thstrm_add_amount")
    print("=" * 72)
    try:
        from src.data_provider.dart_fundamental_provider import (
            _ACCOUNT_ALIASES, _PNL_SJ_DIVS, _extract_amount, _extract_add_amount,
        )
        raw_q1 = dart.finstate_all(TICKER, YEAR, reprt_code="11013", fs_div="CFS")
        raw_q2 = dart.finstate_all(TICKER, YEAR, reprt_code="11012", fs_div="CFS")
        raw_q3 = dart.finstate_all(TICKER, YEAR, reprt_code="11014", fs_div="CFS")
        import pandas as pd
        rows_out = []
        for field, aliases in _ACCOUNT_ALIASES.items():
            if field not in ("revenue", "operating_income", "net_income"):
                continue
            q1_v  = _extract_amount(raw_q1, aliases, sj_div_filter=_PNL_SJ_DIVS)
            q2_v  = _extract_amount(raw_q2, aliases, sj_div_filter=_PNL_SJ_DIVS)
            q2_h1 = _extract_add_amount(raw_q2, aliases)
            q3_v  = _extract_amount(raw_q3, aliases, sj_div_filter=_PNL_SJ_DIVS)
            q3_9m = _extract_add_amount(raw_q3, aliases)
            fmt = lambda v: f"{v/1e12:.3f}조" if v else "N/A"
            rows_out.append({
                "field": field,
                "Q1_thstrm": fmt(q1_v),
                "Q2_thstrm(단독)": fmt(q2_v),
                "Q2_add(H1누적)": fmt(q2_h1),
                "cross_ok(Q1+Q2=H1)?": "✓" if q1_v and q2_v and q2_h1 and abs((q1_v+q2_v)-q2_h1) < 1e9 else "✗",
                "Q3_thstrm(단독)": fmt(q3_v),
                "Q3_add(9M누적)": fmt(q3_9m),
                "cross_ok(Q1+Q2+Q3=9M)?": "✓" if q1_v and q2_v and q3_v and q3_9m and abs((q1_v+q2_v+q3_v)-q3_9m) < 1e9 else "✗",
            })
        df_out = pd.DataFrame(rows_out)
        print(df_out.to_string(index=False))
    except Exception as e:
        print(f"교차검증 오류: {e}")


if __name__ == "__main__":
    main()
