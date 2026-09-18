import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { dashboardApi } from "../api/dashboardApi";
import { ExpandedSignalBoard } from "../features/expanded-shadow/ExpandedSignalBoard";
import { ExpandedSignalBoardResponse } from "../types/expandedShadow";
import { defaultMissingOverviewFixture } from "./fixtures";


function board(overrides: Partial<ExpandedSignalBoardResponse> = {}): ExpandedSignalBoardResponse {
  return {
    mode: "EXPANDED_SHADOW",
    read_only: true,
    status: "READY",
    run_summary: {
      status: "READY",
      source: "output/expanded_shadow/expanded_shadow_run.json",
      run_id: "run-1",
      source_date: "2026-09-17",
      run_status: "SUCCESS",
      universe: 574,
      attempted: 574,
      ready: 574,
      failure: 0,
      signals: 26,
      candidate: 14,
      excluded: 12,
      no_signal: 548,
      started_at: "2026-09-17T09:00:00+00:00",
      finished_at: "2026-09-17T09:06:15+00:00",
      runtime_seconds: 375,
    },
    new_candidates: {
      status: "READY",
      source: "output/expanded_shadow/expanded_shadow_signal_ledger.csv",
      source_date: "2026-09-17",
      count: 1,
      records: [{ stock_name: "Leading Zero", ticker: "000001", market: "KOSPI", signal_date: "2026-09-17", entry_price: 101000, signal_score: 81.5, foreign_status: "POSITIVE" }],
    },
    status_summary: { OPEN: 1, "5D": 1, "10D": 1, "20D": 1, COMPLETE: 1 },
    performance: {
      status: "READY",
      source: "output/expanded_shadow/expanded_candidate_performance_ledger.csv",
      count: 1,
      records: [{ stock_name: "Leading Zero", ticker: "000001", signal_date: "2026-09-17", tracking_status: "OPEN", return_5d: null, excess_5d: null, return_10d: 3.25, excess_10d: 1.1, return_20d: null, excess_20d: null }],
      empty_message: null,
      status_summary: { OPEN: 1, "5D": 1, "10D": 1, "20D": 1, COMPLETE: 1 },
    },
    warnings: [],
    ...overrides,
  };
}


describe("ExpandedSignalBoard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.pushState({}, "", "/");
  });

  it("renders the read-only board, run summary, candidates, lifecycle, and performance", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());

    render(<ExpandedSignalBoard />);

    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    expect(screen.getByText("EXPANDED SHADOW · READ ONLY")).toBeInTheDocument();
    expect(screen.getByText("Run Summary")).toBeInTheDocument();
    expect(screen.getByText("Universe")).toBeInTheDocument();
    expect(screen.getAllByText("574").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("New Candidates")).toBeInTheDocument();
    expect(screen.getAllByText("Leading Zero")).toHaveLength(2);
    expect(screen.getAllByText("000001")).toHaveLength(2);
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("OPEN1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("5D1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("10D1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("20D1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("COMPLETE1");
    expect(screen.getByText("+3.25%")).toBeInTheDocument();
    expect(screen.getByText("excess +1.10%")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.queryByText("Run Daily Operation")).not.toBeInTheDocument();
  });

  it("renders missing performance as a normal empty state", async () => {
    const missing = board({
      status_summary: null,
      performance: {
        status: "MISSING",
        source: "output/expanded_shadow/expanded_candidate_performance_ledger.csv",
        count: 0,
        records: [],
        empty_message: "성과 추적 데이터가 아직 생성되지 않았습니다.",
        status_summary: null,
      },
    });
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(missing);

    render(<ExpandedSignalBoard />);

    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    expect(screen.getAllByText("성과 추적 데이터가 아직 생성되지 않았습니다.")).toHaveLength(2);
    expect(screen.getAllByText("MISSING").length).toBeGreaterThan(0);
  });

  it("keeps malformed artifact warnings inside the Expanded view", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board({ status: "MALFORMED", warnings: ["Expanded performance ledger malformed"] }));

    render(<ExpandedSignalBoard />);

    await waitFor(() => expect(screen.getByText("Expanded performance ledger malformed")).toBeInTheDocument());
    expect(screen.getAllByText("MALFORMED").length).toBeGreaterThan(0);
  });

  it("navigates to the independent /expanded-shadow route without write controls", async () => {
    window.history.pushState({}, "", "/");
    vi.spyOn(dashboardApi, "getOverview").mockResolvedValue(defaultMissingOverviewFixture);
    vi.spyOn(dashboardApi, "getDailySignalBoard").mockRejectedValue(new Error("not needed"));
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());

    render(<App />);
    await waitFor(() => expect(screen.getByText("Today's Shadow Monitor")).toBeInTheDocument());
    await act(async () => { screen.getByRole("button", { name: "Expanded 574" }).click(); });

    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    expect(window.location.pathname).toBe("/expanded-shadow");
    expect(screen.getByText("EXPANDED SHADOW · READ ONLY")).toBeInTheDocument();
    expect(screen.queryByText("Daily Signal Board")).not.toBeInTheDocument();
    expect(screen.queryByText("Run Daily Operation")).not.toBeInTheDocument();
  });
});