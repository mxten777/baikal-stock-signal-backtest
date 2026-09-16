import { render, screen, waitFor, act } from "@testing-library/react";
import { describe, it, expect, afterEach, vi } from "vitest";
import { App } from "../App";
import { defaultMissingOverviewFixture } from "./fixtures";
import { dashboardApi, DashboardApiError } from "../api/dashboardApi";

describe("App Root Integration Test", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders full overview structure correctly when API succeeds", async () => {
    vi.spyOn(dashboardApi, "getOverview").mockResolvedValue(
      defaultMissingOverviewFixture
    );
    vi.spyOn(dashboardApi, "getDailySignalBoard").mockResolvedValue({
      status: {
        analysis_date: null, market_data_date: null, investor_data_date: null,
        coverage: { status: "UNAVAILABLE" }, production_status: null, is_today: false, waiting_for_today: true,
      },
      new_signals: { count: 0, records: [], empty_message: "신규 매수 후보 없음" },
      new_candidates: { count: 0, records: [], empty_message: "신규 매수후보 없음" },
      wait_list: { trade_date: null, as_of_is_current: false, stale_note: null, total_wait_count: 0, records: [], empty_message: "신호 임박 종목 없음" },
      watch_list: { trade_date: null, as_of_is_current: false, stale_note: null, total_watch_count: 0, records: [] },
      candidate_tracking: { as_of: null, records: [] },
      dual_comparison: { trade_date: null, status: "NO_DATA", counts: null },
      production_vs_dual: {
        production_status: null, production_date: null, dual_status: "NO_DATA", dual_date: null,
        date_mismatch: false, mismatch_note: null, counts: null,
      },
      summary: {
        data_status: "WAITING", analysis_date: null, new_candidate_count: 0, wait_count: 0, watch_count: 0,
        tracked_candidate_count: 0, dual_latest_trade_date: null,
      },
    });

    render(<App />);

    // Header
    expect(screen.getByText("BAIKAL Stock Signal")).toBeInTheDocument();
    expect(screen.getByText("READ ONLY")).toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByText("System Status & Environment")
      ).toBeInTheDocument();
    });

    // Panels
    expect(screen.getByText("Daily Signal Board")).toBeInTheDocument();
    expect(screen.getByText("Today's Shadow Monitor")).toBeInTheDocument();
    expect(screen.getByText("Maturity Monitor")).toBeInTheDocument();
    expect(screen.getByText("Strategy Performance")).toBeInTheDocument();
    expect(screen.getByText("Foreign Flow Validation")).toBeInTheDocument();
    expect(screen.getByText("Weakness Segment Monitor")).toBeInTheDocument();
    expect(screen.getByText("Risk & Drawdown Monitor")).toBeInTheDocument();
    expect(
      screen.getByText("Opportunity Cost & Exclusion Analysis")
    ).toBeInTheDocument();
    expect(screen.getByText("Shadow Signal Ledger")).toBeInTheDocument();

    // Verify "No operational Shadow data yet" notice is displayed
    expect(
      screen.getByText(/No operational Shadow data yet/i)
    ).toBeInTheDocument();
  });

  it("renders error banner when API call fails", async () => {
    vi.spyOn(dashboardApi, "getOverview").mockRejectedValue(
      new DashboardApiError(500, "Internal Server Error", "Failed to connect")
    );

    render(<App />);

    await waitFor(() => {
      expect(
        screen.getByText("Adapter Connection Error")
      ).toBeInTheDocument();
    });

    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("renders DUAL Shadow route without regressing production dashboard navigation", async () => {
    vi.spyOn(dashboardApi, "getOverview").mockResolvedValue(defaultMissingOverviewFixture);
    vi.spyOn(dashboardApi, "getDualShadowStatus").mockResolvedValue({ mode: "DUAL_SHADOW", read_only: true, baseline_label: "current reference model", challenger_label: "experimental model", latest_trade_date: "2026-09-10", last_dual_run: "2026-09-10T09:00:03+00:00", pipeline_status: "SUCCESS_NO_NEW_EVIDENCE", baseline_engine_version: "v0.1", challenger_engine_version: "v0.2", ledger_status: "AVAILABLE", ledger_row_count: 20, forward_return_status: "NO_AVAILABLE_EVIDENCE", forward_return_evidence_count: 0, performance_status: "NO_AVAILABLE_EVIDENCE", warnings: [] });
    vi.spyOn(dashboardApi, "getDualShadowLatest").mockResolvedValue({ status: "AVAILABLE", source: "output/dual_shadow_signal_ledger.csv", trade_date: "2026-09-10", total_stocks: 20, counts: { BOTH_YES: 0, BASELINE_ONLY: 1, CHALLENGER_ONLY: 0, BOTH_NO: 19, NOT_EVALUABLE: 0 }, records: [{ stock_name: "SK Innovation", stock_code: "096770", baseline_score: 81.5, baseline_signal: "BUY_WATCH", challenger_score: 66.2, challenger_signal: "WAIT", comparison_group: "BASELINE_ONLY", evaluation_status: "OK", challenger_volume_penalty: 0, challenger_pre_return_penalty: 10, challenger_rsi_penalty: 0, challenger_total_penalty: 10 }], evidence_maturity: { "5D": { available: 0, pending: 20, status: "NO_AVAILABLE_EVIDENCE" }, "10D": { available: 0, pending: 20, status: "NO_AVAILABLE_EVIDENCE" }, "20D": { available: 0, pending: 20, status: "NO_AVAILABLE_EVIDENCE" } }, warnings: [] });
    vi.spyOn(dashboardApi, "getDualShadowPerformance").mockResolvedValue({ status: "NO_AVAILABLE_EVIDENCE", source: "output/dual_shadow_performance_summary.json", engine_versions: { baseline: "v0.1", challenger: "v0.2" }, total_evidence_rows: 0, horizons: { "5D": { baseline: { signal_count: 0, avg_return: null, median_return: null, win_rate: null, best_return: null, worst_return: null }, challenger: { signal_count: 0, avg_return: null, median_return: null, win_rate: null, best_return: null, worst_return: null }, delta: { avg_return_delta: null, median_return_delta: null, win_rate_delta: null, signal_count_delta: 0 } }, "10D": { baseline: { signal_count: 0, avg_return: null, median_return: null, win_rate: null, best_return: null, worst_return: null }, challenger: { signal_count: 0, avg_return: null, median_return: null, win_rate: null, best_return: null, worst_return: null }, delta: { avg_return_delta: null, median_return_delta: null, win_rate_delta: null, signal_count_delta: 0 } }, "20D": { baseline: { signal_count: 0, avg_return: null, median_return: null, win_rate: null, best_return: null, worst_return: null }, challenger: { signal_count: 0, avg_return: null, median_return: null, win_rate: null, best_return: null, worst_return: null }, delta: { avg_return_delta: null, median_return_delta: null, win_rate_delta: null, signal_count_delta: 0 } } }, warnings: [] });
    vi.spyOn(dashboardApi, "getDualShadowRuns").mockResolvedValue({ status: "AVAILABLE", source: "output/dual_shadow_run_registry.jsonl", items: [{ trade_date: "2026-09-10", started_at: "2026-09-10T09:00:00+00:00", finished_at: "2026-09-10T09:00:03+00:00", status: "SUCCESS_NO_NEW_EVIDENCE", ledger_saved: 20, forward_return_saved: 0, performance_status: "NO_AVAILABLE_EVIDENCE", error_code: null, error_message: null }], warnings: [] });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Today's Shadow Monitor")).toBeInTheDocument());
    await act(async () => { screen.getByRole("button", { name: "DUAL Shadow" }).click(); });

    await waitFor(() => expect(screen.getByText("DUAL Shadow Monitor")).toBeInTheDocument());
    expect(screen.getByText("READ-ONLY")).toBeInTheDocument();
    expect(screen.queryByText("Run Daily Operation")).not.toBeInTheDocument();
    await act(async () => { screen.getByRole("button", { name: "Dashboard" }).click(); });
    await waitFor(() => expect(screen.getByText("Today's Shadow Monitor")).toBeInTheDocument());
  });
});
