import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DailySignalBoard } from "../features/daily-signal-board/DailySignalBoard";
import { DailySignalBoardResponse } from "../types/dashboard";

const baseBoard: DailySignalBoardResponse = {
  status: {
    analysis_date: "2026-09-14",
    market_data_date: "2026-09-14",
    investor_data_date: "2026-09-14",
    coverage: { status: "AVAILABLE", ticker_count: 20, market: { expected: 20, found: 20, missing: [] }, investor: { expected: 20, found: 20, missing: [] } },
    production_status: "SUCCESS_WITH_WARNING",
    is_today: true,
    waiting_for_today: false,
  },
  new_signals: { count: 0, records: [], empty_message: "신규 매수 후보 없음" },
  watch_list: {
    trade_date: "2026-09-14",
    total_watch_count: 2,
    records: [
      { stock_name: "KB금융", stock_code: "105560", evaluation_close: 179100, baseline_score: 55.4 },
      { stock_name: "SK이노베이션", stock_code: "096770", evaluation_close: 133800, baseline_score: 50.8 },
    ],
  },
  candidate_tracking: {
    as_of: "2026-09-14",
    records: [
      {
        stock_name: "SK이노베이션",
        stock_code: "096770",
        signal_date: "2026-09-10",
        signal_price: 153100,
        signal_score: 81.5,
        current_evaluation_close: 133800,
        current_baseline_score: 50.8,
        current_baseline_signal_type: "WATCH",
        price_change_pct: -12.61,
        dual_match_found: true,
      },
    ],
  },
  dual_comparison: {
    trade_date: "2026-09-14",
    status: "AVAILABLE",
    counts: { BOTH_YES: 0, BASELINE_ONLY: 0, CHALLENGER_ONLY: 0, BOTH_NO: 20, NOT_EVALUABLE: 0 },
  },
};

describe("DailySignalBoard Feature", () => {
  it("renders the empty new-signal message when count is 0", () => {
    render(<DailySignalBoard board={baseBoard} />);
    expect(screen.getByText("Daily Signal Board")).toBeInTheDocument();
    expect(screen.getByText("신규 매수 후보 없음")).toBeInTheDocument();
  });

  it("renders WATCH list and candidate tracking rows", () => {
    render(<DailySignalBoard board={baseBoard} />);
    expect(screen.getByText("KB금융")).toBeInTheDocument();
    expect(screen.getAllByText("SK이노베이션").length).toBeGreaterThan(0);
  });

  it("shows waiting-for-today banner when data is stale", () => {
    const staleBoard: DailySignalBoardResponse = {
      ...baseBoard,
      status: { ...baseBoard.status, is_today: false, waiting_for_today: true, analysis_date: "2026-09-10" },
    };
    render(<DailySignalBoard board={staleBoard} />);
    expect(screen.getAllByText(/오늘 데이터 대기중/).length).toBeGreaterThan(0);
    // 마지막 유효 데이터(WATCH/CANDIDATE)는 계속 표시된다
    expect(screen.getByText("KB금융")).toBeInTheDocument();
  });

  it("renders an unavailable state when board is not loaded", () => {
    render(<DailySignalBoard board={null} />);
    expect(screen.getByText("Daily Signal Board Unavailable")).toBeInTheDocument();
  });
});
