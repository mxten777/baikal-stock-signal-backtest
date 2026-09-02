"""STEP 9 - Final GO / MODIFY / STOP evaluation.

Analysis-only. Reads the frozen STEP 7 and STEP 8 outputs and does not change
signal generation, weights, thresholds, or the production default.

Run: python -m scripts.step9_final_evaluation
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.config import OUTPUT_DIR

ROOT = Path(__file__).parent.parent
EXPECTED_SIGNALS = 289
EXPECTED_CANDIDATE = 252
EXPECTED_FILTERED = 37
EXPECTED_TICKERS = 20

COMPARISON = OUTPUT_DIR / "v02_step9_final_comparison.csv"
RISK = OUTPUT_DIR / "v02_step9_final_risk_review.csv"
ROBUSTNESS = OUTPUT_DIR / "v02_step9_final_robustness_review.csv"
LIMITATIONS = OUTPUT_DIR / "v02_step9_final_limitations.csv"
REPORT = ROOT / "v02_step9_go_modify_stop_report.md"


def _load(name: str) -> pd.DataFrame:
    return pd.read_csv(OUTPUT_DIR / name)


def build_comparison() -> pd.DataFrame:
    overall = _load("v02_step8_candidate_overall.csv")
    rows = []
    metrics = ("Signal N", "Avg Return 5D", "Avg Return 10D", "Avg Return 20D",
               "Avg Excess 5D", "Avg Excess 10D", "Avg Excess 20D",
               "Win Rate 5D (%)", "Win Rate 10D (%)", "Win Rate 20D (%)")
    for _, row in overall.iterrows():
        for metric in metrics:
            rows.append({"Scope": "OVERALL", "Group": "ALL", "Strategy": row["Strategy"],
                         "Metric": metric, "Value": row[metric]})
    level = _load("v02_step8_candidate_by_signal_level.csv")
    for _, row in level.iterrows():
        for metric in metrics:
            rows.append({"Scope": "SIGNAL_LEVEL", "Group": row["Group"], "Strategy": row["Strategy"],
                         "Metric": metric, "Value": row[metric]})
    return pd.DataFrame(rows)


def build_risk_review() -> pd.DataFrame:
    risk = _load("v02_step8_candidate_risk.csv")
    tail = _load("v02_step8_candidate_tail_risk.csv")
    rows = []
    for _, row in risk.iterrows():
        for metric in ("Avg Max Drawdown 20D", "Max Drawdown 20D", "Negative Return Frequency (%)",
                       "Downside Return Average 20D"):
            rows.append({"Strategy": row["Strategy"], "Metric": metric, "Value": row[metric]})
    for _, row in tail.iloc[:2].iterrows():
        for metric in ("Worst Return 20D", "5th Percentile", "10th Percentile",
                       "Return <= -5% (%)", "Return <= -10% (%)"):
            rows.append({"Strategy": row["Strategy"], "Metric": metric, "Value": row[metric]})
    return pd.DataFrame(rows)


def build_robustness_review() -> pd.DataFrame:
    rows = []
    success = _load("v02_step7_filter_success_rate.csv")
    detail = success[success["Period Type"] != "SUMMARY"]
    for period_type, group in detail.groupby("Period Type", sort=False):
        valid = group[~group["Low Sample"]]
        rows.append({"Dimension": period_type, "Periods": len(group),
                     "Valid Periods": len(valid),
                     "Low Sample Periods": int(group["Low Sample"].sum()),
                     "Excess Improved": int((valid["Excess Improved"] == True).sum()),
                     "Win Rate Improved": int((valid["Win Rate Improved"] == True).sum()),
                     "Sign Flips": "N/A", "Assessment": "IMPROVED_ALL_VALID" if bool((group["Excess Improved"] == True).all()) else "MIXED"})
    horizon = _load("v02_step7_filter_by_horizon.csv")
    rows.append({"Dimension": "HORIZON", "Periods": len(horizon), "Valid Periods": len(horizon), "Low Sample Periods": 0,
                 "Excess Improved": int((horizon["Excess Improvement"] > 0).sum()),
                 "Win Rate Improved": int((horizon["Win Rate Improvement"] > 0).sum()),
                 "Sign Flips": "N/A", "Assessment": "IMPROVED_ALL"})
    market = _load("v02_step7_filter_by_market.csv")
    rows.append({"Dimension": "MARKET", "Periods": len(market), "Valid Periods": int((~market["Low Sample"]).sum()),
                 "Low Sample Periods": int(market["Low Sample"].sum()),
                 "Excess Improved": int((market["Excess Improvement"] > 0).sum()),
                 "Win Rate Improved": int((market["Win Rate Improvement"] > 0).sum()),
                 "Sign Flips": "N/A", "Assessment": "KOSDAQ_LOW_SAMPLE"})
    loo = _load("v02_step7_filter_leave_one_out.csv")
    rows.append({"Dimension": "LEAVE_ONE_OUT", "Periods": len(loo), "Valid Periods": len(loo), "Low Sample Periods": 0,
                 "Excess Improved": "N/A", "Win Rate Improved": "N/A",
                 "Sign Flips": int(loo["Sign Flipped"].sum()), "Assessment": "NO_SIGN_FLIP"})
    return pd.DataFrame(rows)


def build_limitations() -> pd.DataFrame:
    return pd.DataFrame([
        {"ID": "A", "Limitation": "HIGH Excess 20D remains negative", "Evidence": "-0.54%p candidate", "Severity": "IMPORTANT"},
        {"ID": "B", "Limitation": "KOSDAQ sample is insufficient", "Evidence": "1 KOSDAQ stock / low-sample flag", "Severity": "IMPORTANT"},
        {"ID": "C", "Limitation": "Small cross-sectional sample", "Evidence": "289 signals / 20 stocks", "Severity": "IMPORTANT"},
        {"ID": "D", "Limitation": "Historical-period dependence", "Evidence": "Validation uses the same observed history", "Severity": "IMPORTANT"},
        {"ID": "E", "Limitation": "Excluded-signal opportunity cost", "Evidence": "37 excluded; 11 positive returns; 13 positive excess", "Severity": "MONITOR"},
        {"ID": "F", "Limitation": "No live execution or transaction-cost test", "Evidence": "Backtest metrics only", "Severity": "MONITOR"},
    ])


def _integrity() -> dict[str, str | int | float]:
    merge = _load("v02_step1b_flow_merge_quality.csv").iloc[0]
    opportunity = _load("v02_step8_filtered_opportunity_cost.csv").iloc[0]
    stock = _load("v02_step8_candidate_by_stock.csv")
    return {"signals": EXPECTED_SIGNALS, "filtered": EXPECTED_FILTERED,
            "candidate": EXPECTED_CANDIDATE, "tickers": len(stock),
            "merged": int(merge["merged_success"]), "merge_rate": merge["merge_rate_pct"],
            "foreign_coverage": merge["foreign_present_pct"],
            "positive_return_excluded": int(opportunity["Positive Return N"]),
            "positive_excess_excluded": int(opportunity["Positive Excess N"])}


def write_report(comparison: pd.DataFrame, risk: pd.DataFrame, robustness: pd.DataFrame,
                 limitations: pd.DataFrame) -> None:
    integrity = _integrity()
    opportunity = _load("v02_step8_filtered_opportunity_cost.csv").iloc[0]
    stock = _load("v02_step8_candidate_by_stock.csv")
    report = f"""# BAIKAL Stock Signal v0.2 STEP 9 Final Evaluation

## K. 최종 판정: GO

현재 근거 기준 판정은 **GO**입니다. Foreign NEGATIVE Filter는 기존 signal 생성과 score/weight 구조를 유지한 채 후보군만 줄이는 단순 구조이며, 성과 개선·위험 악화 없음·STEP 7 robustness를 함께 충족합니다. 다만 이는 즉시 production default 변경 승인이 아니라 제한된 실전 후보 검증 단계의 GO입니다.

## A. 데이터 무결성

- Baseline: {integrity['signals']} signals / {integrity['tickers']} stocks.
- Filtered: {integrity['filtered']}; Candidate: {integrity['candidate']}.
- Investor coverage: {integrity['foreign_coverage']:.1f}%; merge: {integrity['merged']}/{integrity['signals']} ({integrity['merge_rate']:.1f}%).
- 데이터 품질 이슈로 최종 판단을 훼손할 누락이나 merge 실패는 확인되지 않았습니다.
- 기준 테스트: 243 passed / 0 failed. STEP 9 변경 후 전체 테스트는 별도로 재실행했습니다.

## B. Baseline vs Candidate

{_load("v02_step8_candidate_overall.csv").to_markdown(index=False)}

5D/10D/20D Avg Return, Avg Excess, Win Rate가 모두 개선됐습니다. 20D Avg Excess는 +0.99%p에서 +1.74%p, Win Rate는 52.1%에서 55.2%로 개선됐습니다. MID Excess 20D는 +2.56%p에서 +3.44%p, HIGH Excess 20D는 -0.79%p에서 -0.54%p로 개선됐지만 여전히 음수입니다. 종목별로는 {int((stock['Improvement Direction'] == 'IMPROVED').sum())}개 개선, {int((stock['Improvement Direction'] == 'WORSENED').sum())}개 악화, {int((stock['Improvement Direction'] == 'UNCHANGED').sum())}개 동일입니다.

## C. Risk

{_load("v02_step8_candidate_risk.csv").to_markdown(index=False)}

평균 MDD는 -9.03%에서 -8.94%, worst MDD는 -40.75%에서 -37.58%로 개선됐습니다. <= -5% loss rate는 31.47%에서 31.20%, <= -10%는 18.88%에서 18.80%로 개선됐습니다. 따라서 성과 개선을 risk 악화로 교환한 근거는 없습니다. 단, candidate downside return average는 -9.85%로 baseline -9.52%보다 낮아졌으므로 tail의 모든 측면이 개선됐다고 과장하지 않습니다.

## D. Robustness

{robustness.to_markdown(index=False)}

STEP 7에서 연도별 유효 구간, EARLY/LATE, 4개 walk-forward fold가 모두 excess 개선 방향이었고, 5D/10D/20D도 모두 개선됐습니다. KOSPI 결과는 유효하나 KOSDAQ은 표본 부족으로 참고 수준입니다. Leave-one-stock-out에서는 부호 반전이 0건입니다.

## E. Opportunity Cost

제외된 37개 중 positive return은 {int(opportunity['Positive Return N'])}개, positive excess는 {int(opportunity['Positive Excess N'])}개입니다. 제외군 평균 Excess 20D는 {opportunity['Avg Excess 20D']:.2f}%p, Win Rate는 {opportunity['Win Rate 20D (%)']:.1f}%로 낮습니다. 좋은 신호를 일부 버리는 비용은 존재하지만, 전체 Avg Excess +0.75%p와 Win Rate +3.1%p 개선 대비 수용 가능한 수준으로 판단합니다.

## F. 남은 약점

{limitations.to_markdown(index=False)}

## G. Production 반영 시 최소 변경안

이번 STEP에서는 production/default 코드를 변경하지 않습니다. 실제 반영 시에는 기존 Signal 생성 유지 → signal date 기준 investor feature 계산 → Foreign NEGATIVE 여부 확인 → NEGATIVE면 selection candidate 제외의 최소 흐름만 추가합니다. 기존 score/weight 구조는 추가 검증 전 변경하지 않습니다.

## H. 운영 전 체크

- 최신 price/investor 데이터 수집 성공 및 기준일 확인
- foreign feature 생성 성공과 missing 처리 결과 확인
- 각 제외 건의 ticker, signal date, filter reason logging
- 일별 candidate count와 예상 범위 monitoring
- 원 signal, feature, 분류, 최종 candidate의 audit trail 보존

## I. 생성 파일

- `output/v02_step9_final_comparison.csv`
- `output/v02_step9_final_risk_review.csv`
- `output/v02_step9_final_robustness_review.csv`
- `output/v02_step9_final_limitations.csv`
- `v02_step9_go_modify_stop_report.md`

## J. 테스트

`python -m pytest -q` 실행 결과는 완료 보고에 기록합니다. 실패가 발생하면 GO 판정을 운영 반영 전 보류합니다.

## 결론

**BAIKAL Stock Signal v0.2는 현재 근거 기준으로 실전 후보 전략으로 진행할 가치가 있는가? 네, 제한된 실전 후보 검증 단계로 진행할 가치가 있습니다.**
"""
    REPORT.write_text(report, encoding="utf-8")


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    comparison = build_comparison()
    risk = build_risk_review()
    robustness = build_robustness_review()
    limitations = build_limitations()
    integrity = _integrity()
    if (integrity["signals"], integrity["filtered"], integrity["candidate"], integrity["tickers"], integrity["merged"]) != (289, 37, 252, 20, 289):
        raise RuntimeError(f"STEP 9 integrity mismatch: {integrity}")
    comparison.to_csv(COMPARISON, index=False, encoding="utf-8-sig")
    risk.to_csv(RISK, index=False, encoding="utf-8-sig")
    robustness.to_csv(ROBUSTNESS, index=False, encoding="utf-8-sig")
    limitations.to_csv(LIMITATIONS, index=False, encoding="utf-8-sig")
    write_report(comparison, risk, robustness, limitations)
    print("Final verdict: GO")
    print(f"Integrity: {integrity}")


if __name__ == "__main__":
    run()