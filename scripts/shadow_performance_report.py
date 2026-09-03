"""
Shadow STEP 5 — CANDIDATE / EXCLUDED 누적 성과 리포트 (수동 실행 entry point).

이번 STEP은 READ-ONLY 분석이다.
  - output/shadow_signal_records.csv를 읽기만 하고 절대 수정하지 않는다.
  - Signal / Foreign / Candidate 판정 / threshold / weight를 변경하지 않는다.
  - 통계적 유의성 검정이나 새로운 threshold 탐색을 하지 않는다.
  - 자동 GO/STOP 판정을 만들지 않는다 (데이터 상태 문구만 표시).

실행: python scripts/shadow_performance_report.py
출력:
  - 콘솔 요약
  - output/shadow_performance_report.md
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.config import OUTPUT_DIR
from src.shadow_performance import render_report
from src.shadow_tracking import ShadowStore

DEFAULT_REPORT_PATH = OUTPUT_DIR / "shadow_performance_report.md"


def run_report(store: ShadowStore | None = None, report_path: Path = DEFAULT_REPORT_PATH) -> str:
    """Shadow Record를 읽어 리포트 텍스트를 생성하고 Markdown 파일로 저장한다 (CSV는 건드리지 않음)."""
    store = store or ShadowStore()
    records = store.load()
    generated_at = datetime.now().isoformat(timespec="seconds")

    text = render_report(records, generated_at=generated_at)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    text = run_report()
    print(text)
    print(f"저장 파일: {DEFAULT_REPORT_PATH}")


if __name__ == "__main__":
    main()
