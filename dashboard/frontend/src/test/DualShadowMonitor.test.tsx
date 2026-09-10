import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DualShadowMonitor } from "../features/dual-shadow/DualShadowMonitor";
import { dashboardApi } from "../api/dashboardApi";
import { DualShadowLatest, DualShadowPerformance, DualShadowRuns, DualShadowStatus } from "../types/dualShadow";

const status = (overrides: Partial<DualShadowStatus> = {}): DualShadowStatus => ({
  mode: "DUAL_SHADOW",
  read_only: true,
  baseline_label: "current reference model",
  challenger_label: "experimental model",
  latest_trade_date: "2026-09-10",
  last_dual_run: "2026-09-10T09:00:03+00:00",
  pipeline_status: "SUCCESS_NO_NEW_EVIDENCE",
  baseline_engine_version: "v0.1",
  challenger_engine_version: "v0.2",
  ledger_status: "AVAILABLE",
  ledger_row_count: 20,
  forward_return_status: "NO_AVAILABLE_EVIDENCE",
  forward_return_evidence_count: 0,
  performance_status: "NO_AVAILABLE_EVIDENCE",
  warnings: [],
  ...overrides,
});

const latest = (overrides: Partial<DualShadowLatest> = {}): DualShadowLatest => ({
  status: "AVAILABLE",
  source: "output/dual_shadow_signal_ledger.csv",
  trade_date: "2026-09-10",
  total_stocks: 20,
  counts: { BOTH_YES: 0, BASELINE_ONLY: 1, CHALLENGER_ONLY: 1, BOTH_NO: 18, NOT_EVALUABLE: 0 },
  records: [
    { stock_name: "SK Innovation", stock_code: "096770", baseline_score: 81.5, baseline_signal: "BUY_WATCH", challenger_score: 66.2, challenger_signal: "WAIT", comparison_group: "BASELINE_ONLY", evaluation_status: "OK", challenger_volume_penalty: 0, challenger_pre_return_penalty: 10, challenger_rsi_penalty: 0, challenger_total_penalty: 10 },
    { stock_name: "Challenger Inc", stock_code: "000001", baseline_score: 60, baseline_signal: "WAIT", challenger_score: 82, challenger_signal: "BUY_WATCH", comparison_group: "CHALLENGER_ONLY", evaluation_status: "OK", challenger_volume_penalty: 0, challenger_pre_return_penalty: 0, challenger_rsi_penalty: 0, challenger_total_penalty: 0 },
  ],
  evidence_maturity: { "5D": { available: 0, pending: 20, status: "NO_AVAILABLE_EVIDENCE" }, "10D": { available: 0, pending: 20, status: "NO_AVAILABLE_EVIDENCE" }, "20D": { available: 0, pending: 20, status: "NO_AVAILABLE_EVIDENCE" } },
  warnings: [],
  ...overrides,
});

const performance = (overrides: Partial<DualShadowPerformance> = {}): DualShadowPerformance => {
  const empty = { signal_count: 0, avg_return: null, median_return: null, win_rate: null, best_return: null, worst_return: null };
  const delta = { avg_return_delta: null, median_return_delta: null, win_rate_delta: null, signal_count_delta: 0 };
  return {
    status: "NO_AVAILABLE_EVIDENCE",
    source: "output/dual_shadow_performance_summary.json",
    generated_at: "2026-09-10T09:00:03+00:00",
    win_definition: "forward_return > 0",
    engine_versions: { baseline: "v0.1", challenger: "v0.2" },
    total_evidence_rows: 0,
    horizons: { "5D": { baseline: empty, challenger: empty, delta }, "10D": { baseline: empty, challenger: empty, delta }, "20D": { baseline: empty, challenger: empty, delta } },
    warnings: [],
    ...overrides,
  };
};

const runs = (overrides: Partial<DualShadowRuns> = {}): DualShadowRuns => ({
  status: "AVAILABLE",
  source: "output/dual_shadow_run_registry.jsonl",
  items: [{ trade_date: "2026-09-10", started_at: "2026-09-10T09:00:00+00:00", finished_at: "2026-09-10T09:00:03+00:00", status: "SUCCESS_NO_NEW_EVIDENCE", ledger_saved: 20, forward_return_saved: 0, performance_status: "NO_AVAILABLE_EVIDENCE", error_code: null, error_message: null }],
  warnings: [],
  ...overrides,
});

function mockDualApi(overrides: { status?: Partial<DualShadowStatus>; latest?: Partial<DualShadowLatest>; performance?: Partial<DualShadowPerformance>; runs?: Partial<DualShadowRuns> } = {}) {
  vi.spyOn(dashboardApi, "getDualShadowStatus").mockResolvedValue(status(overrides.status));
  vi.spyOn(dashboardApi, "getDualShadowLatest").mockResolvedValue(latest(overrides.latest));
  vi.spyOn(dashboardApi, "getDualShadowPerformance").mockResolvedValue(performance(overrides.performance));
  vi.spyOn(dashboardApi, "getDualShadowRuns").mockResolvedValue(runs(overrides.runs));
}

describe("DualShadowMonitor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders route content, read-only labels, status, comparison, performance, maturity, and runs", async () => {
    mockDualApi();

    render(<DualShadowMonitor />);

    await waitFor(() => expect(screen.getByText("DUAL Shadow Monitor")).toBeInTheDocument());
    expect(screen.getByText("READ-ONLY")).toBeInTheDocument();
    expect(screen.getByText("Current Status")).toBeInTheDocument();
    expect(screen.getAllByText("SUCCESS_NO_NEW_EVIDENCE").length).toBeGreaterThan(0);
    expect(screen.getByText("Today's Comparison")).toBeInTheDocument();
    expect(screen.getByText("SK Innovation")).toBeInTheDocument();
    expect(screen.getAllByText("BASELINE_ONLY").length).toBeGreaterThan(0);
    expect(screen.getAllByText("CHALLENGER_ONLY").length).toBeGreaterThan(0);
    expect(screen.getByText("Performance")).toBeInTheDocument();
    expect(screen.getAllByText("NO_AVAILABLE_EVIDENCE").length).toBeGreaterThan(0);
    expect(screen.getByText("Evidence Maturity")).toBeInTheDocument();
    expect(screen.getAllByText("Available 0")).toHaveLength(3);
    expect(screen.getAllByText("Pending 20")).toHaveLength(3);
    expect(screen.getByText("Recent Runs")).toBeInTheDocument();
  });

  it("renders N/A for unavailable performance values", async () => {
    mockDualApi();

    render(<DualShadowMonitor />);

    await waitFor(() => expect(screen.getByText("Performance")).toBeInTheDocument());
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
  });

  it("handles empty API states safely", async () => {
    mockDualApi({
      status: { latest_trade_date: null, pipeline_status: "NO_RUN", ledger_row_count: 0, forward_return_evidence_count: 0, performance_status: "NO_SUMMARY" },
      latest: { status: "NO_DATA", trade_date: null, total_stocks: 0, counts: {}, records: [], evidence_maturity: { "5D": { available: 0, pending: 0, status: "NO_AVAILABLE_EVIDENCE" }, "10D": { available: 0, pending: 0, status: "NO_AVAILABLE_EVIDENCE" }, "20D": { available: 0, pending: 0, status: "NO_AVAILABLE_EVIDENCE" } } },
      performance: { status: "NO_SUMMARY" },
      runs: { status: "NO_RUN", items: [] },
    });

    render(<DualShadowMonitor />);

    await waitFor(() => expect(screen.getAllByText("NO_RUN").length).toBeGreaterThan(0));
    expect(screen.getByText("No DUAL comparison ledger rows available.")).toBeInTheDocument();
    expect(screen.getByText("No DUAL run registry records available.")).toBeInTheDocument();
  });

  it("shows API error without write controls", async () => {
    vi.spyOn(dashboardApi, "getDualShadowStatus").mockRejectedValue(new Error("network down"));
    vi.spyOn(dashboardApi, "getDualShadowLatest").mockResolvedValue(latest());
    vi.spyOn(dashboardApi, "getDualShadowPerformance").mockResolvedValue(performance());
    vi.spyOn(dashboardApi, "getDualShadowRuns").mockResolvedValue(runs());

    render(<DualShadowMonitor />);

    await waitFor(() => expect(screen.getByText("DUAL Shadow API error")).toBeInTheDocument());
    expect(screen.queryByText("Run Daily Operation")).not.toBeInTheDocument();
  });
});