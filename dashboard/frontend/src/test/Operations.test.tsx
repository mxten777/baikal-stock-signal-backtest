import { render, screen, waitFor, act } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { Operations } from "../features/operations/Operations";
import { dashboardApi } from "../api/dashboardApi";

const status = (overrides = {}) => ({
  target_trade_date: "2026-09-04", current_status: "SUCCESS", attempt: 1,
  next_retry_at: null, last_attempt_at: "19:00", completed_at: "19:01",
  latest_market_date: "2026-09-04", latest_investor_date: "2026-09-04",
  integrity_status: "PASS", pipeline_status: "SUCCESS", health_status: "HEALTHY",
  failed_phase: null, error_code: null, error_message: null, operator_action_required: false,
  operator_action_code: null, last_run_id: "run-1", last_daily_status: "SUCCESS",
  last_successful_run_at: "19:01", last_successful_trade_date: "2026-09-04", timezone: "Asia/Seoul",
  manual_run: { allowed: false, reason_code: "MANUAL_RUN_NOT_ALLOWED", reason: "Manual execution is unavailable.", requires_confirmation: true }, ...overrides,
});

describe("Operations view", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("renders status, data readiness, and history detail", async () => {
    vi.spyOn(dashboardApi, "getOperationsStatus").mockResolvedValue(status());
    vi.spyOn(dashboardApi, "getOperationsHistory").mockResolvedValue([{ trade_date: "2026-09-04", final_status: "SUCCESS", attempts: 1, first_attempt_at: "18:30", last_attempt_at: "19:01", last_run_id: "run-1", error_code: null, operator_action_required: false }]);
    vi.spyOn(dashboardApi, "getOperationsDetail").mockResolvedValue([{ slot: 0, attempt: 1, orchestration_status: "SUCCESS", daily_status: "SUCCESS", started_at: "18:30", finished_at: "19:01", next_retry_at: null, error_code: null, error_message: null, failed_phase: null, operator_action_required: false, operator_action_code: null, last_run_id: "run-1" }]);
    vi.spyOn(dashboardApi, "getOperationsException").mockResolvedValue(null);
    render(<Operations />);
    await waitFor(() => expect(screen.getAllByText("Success").length).toBeGreaterThanOrEqual(1));
    expect(screen.getAllByText("2026-09-04").length).toBeGreaterThan(0);
    await act(async () => { screen.getByRole("button", { name: /2026-09-04/ }).click(); });
    await waitFor(() => expect(screen.getByText("2026-09-04 operational detail")).toBeInTheDocument());
  });

  it("renders retry and operator action states", async () => {
    vi.spyOn(dashboardApi, "getOperationsStatus").mockResolvedValue(status({ current_status: "RETRY_PENDING", next_retry_at: "19:30", operator_action_required: true, operator_action_code: "CHECK_INTEGRITY", error_code: "SOURCE_LAG", error_message: "Input is late" }));
    vi.spyOn(dashboardApi, "getOperationsHistory").mockResolvedValue([]);
    render(<Operations />);
    await waitFor(() => expect(screen.getByText("Retry pending")).toBeInTheDocument());
    expect(screen.getByText(/Operator action required/)).toBeInTheDocument();
    expect(screen.getByText(/CHECK_INTEGRITY/)).toBeInTheDocument();
    expect(screen.getByText(/19:30/)).toBeInTheDocument();
  });

  it("renders warning exception guidance and affected components", async () => {
    vi.spyOn(dashboardApi, "getOperationsStatus").mockResolvedValue(status({ current_status: "SUCCESS_WITH_WARNING", last_daily_status: "SUCCESS_WITH_WARNING" }));
    vi.spyOn(dashboardApi, "getOperationsHistory").mockResolvedValue([{ trade_date: "2026-09-04", final_status: "SUCCESS_WITH_WARNING", attempts: 1, first_attempt_at: "18:30", last_attempt_at: "19:01", last_run_id: "run-1", error_code: null, operator_action_required: true }]);
    vi.spyOn(dashboardApi, "getOperationsDetail").mockResolvedValue([]);
    vi.spyOn(dashboardApi, "getOperationsException").mockResolvedValue({ trade_date: "2026-09-04", status: "SUCCESS_WITH_WARNING", severity: "WARNING", failed_phase: null, error_code: null, summary: "Operation completed with warning", details: null, retryable: false, operator_action_required: true, operator_action_code: "CHECK_INTEGRITY", operator_guidance: "Integrity Gate 실패 원인을 확인한 뒤 강제 진행하지 마십시오.", manual_rerun_allowed: false, affected_components: ["INTEGRITY"], data_context: { target_trade_date: "2026-09-04", latest_market_date: "2026-09-04", latest_investor_date: "2026-09-04", integrity_status: "PASS_WITH_WARNING", pipeline_status: "SUCCESS_WITH_WARNING" }, run_context: { last_run_id: "run-1", attempt: 1, last_attempt_at: "19:01", next_retry_at: null } });
    render(<Operations />);
    await waitFor(() => expect(screen.getAllByText("Success with warning").length).toBeGreaterThanOrEqual(1));
    await act(async () => { screen.getByRole("button", { name: /2026-09-04/ }).click(); });
    await waitFor(() => expect(screen.getByText("WARNING")).toBeInTheDocument());
    expect(screen.getByText("INTEGRITY", { exact: true })).toBeInTheDocument();
    expect(screen.getByText(/Integrity Gate 실패/)).toBeInTheDocument();
  });

  it("requires confirmation, prevents duplicate clicks, and refreshes after a manual run", async () => {
    const capability = { allowed: true, reason_code: "MANUAL_RERUN_ALLOWED", reason: "Manual rerun allowed", requires_confirmation: true };
    const getStatus = vi.spyOn(dashboardApi, "getOperationsStatus").mockResolvedValue(status({ current_status: "FAILED", operator_action_code: "MANUAL_RERUN_ALLOWED", manual_run: capability }));
    vi.spyOn(dashboardApi, "getOperationsHistory").mockResolvedValue([]);
    const run = vi.spyOn(dashboardApi, "runManualDailyOperation").mockResolvedValue({ accepted: true, executed: true, run_id: "manual-1", daily_status: "SUCCESS", overall_status: "SUCCESS", started_at: "start", completed_at: "done", error_code: null, error_message: null, warning: null, scheduler_reconciliation_required: true });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Operations />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Run Daily Operation" })).toBeEnabled());
    await act(async () => { screen.getByRole("button", { name: "Run Daily Operation" }).click(); });
    expect(run).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByText("Run ID: manual-1")).toBeInTheDocument());
    expect(getStatus).toHaveBeenCalledTimes(2);
  });

  it("does not execute when confirmation is cancelled", async () => {
    vi.spyOn(dashboardApi, "getOperationsStatus").mockResolvedValue(status({ current_status: "FAILED", operator_action_code: "MANUAL_RERUN_ALLOWED", manual_run: { allowed: true, reason_code: "MANUAL_RERUN_ALLOWED", reason: "Manual rerun allowed", requires_confirmation: true } }));
    vi.spyOn(dashboardApi, "getOperationsHistory").mockResolvedValue([]);
    const run = vi.spyOn(dashboardApi, "runManualDailyOperation");
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<Operations />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Run Daily Operation" })).toBeEnabled());
    await act(async () => { screen.getByRole("button", { name: "Run Daily Operation" }).click(); });
    expect(run).not.toHaveBeenCalled();
  });
});