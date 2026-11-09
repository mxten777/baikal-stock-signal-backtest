import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { dashboardApi } from "../api/dashboardApi";
import { ExpandedPartialValidation } from "../features/expanded-shadow/ExpandedPartialValidation";
import { ExpandedPartialResponse, ExpandedPartialValidationResponse } from "../types/expandedShadow";

const pending = {
  status: "UNCONFIRMED", reason: "NO_VERIFIED_FORWARD_DATA",
  return_pct: null, benchmark_pct: null, excess_pct: null,
} as const;
const response: ExpandedPartialValidationResponse = {
  read_only: true, status: "PARTIAL", view: "PARTIAL_VALIDATION", warnings: [],
  items: [{
    status: "PARTIAL", source_date: "2026-11-09", run_id: "partial-test",
    validation_status: "NOT_RUN", promoted: false, universe: 574, attempted: 572,
    ready: 572, suspended: 1, unknown: 1, unresolved: 1, signals: 4, candidates: 2,
    signal_excluded: 2, excluded_tickers: ["032580"], unknown_tickers: ["018000"],
    records: ([
      { ticker: "443670", name: "에스피소프트", market: "KOSDAQ", decision: "CANDIDATE", signal_type: "STRONG_WATCH", signal_score: 86.2 },
      { ticker: "079160", name: "CJ CGV", market: "KOSPI", decision: "CANDIDATE", signal_type: "BUY_WATCH", signal_score: 75.4 },
      { ticker: "181710", name: "NHN", market: "KOSPI", decision: "EXCLUDED", signal_type: "OVERHEATED", signal_score: 78.5 },
      { ticker: "006650", name: "대한유화", market: "KOSPI", decision: "EXCLUDED", signal_type: "BUY_WATCH", signal_score: 75.4 },
    ] satisfies ExpandedPartialResponse["items"][number]["records"])
      .map((record) => ({ ...record, forward_evaluation: { "5D": pending, "10D": pending, "20D": pending } })),
  }],
};

describe("PARTIAL Validation read-only view", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  it("shows all four signals, two candidates and unconfirmed horizons without zero returns", async () => {
    vi.spyOn(dashboardApi, "getExpandedPartialValidation").mockResolvedValue(response);
    const completed = vi.spyOn(dashboardApi, "getExpandedSignalBoard");
    render(<ExpandedPartialValidation />);
    expect(await screen.findByText("2026-11-09 · PARTIAL")).toBeInTheDocument();
    expect(screen.getByText("처리 572/574 · 신호 4 · 후보 2")).toBeInTheDocument();
    expect(screen.getByText(/Validation NOT_RUN/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(5);
    expect(within(table).getAllByText("후보 · CANDIDATE")).toHaveLength(2);
    expect(within(table).getAllByText("신호단계 제외 · EXCLUDED")).toHaveLength(2);
    expect(within(table).getAllByText("UNCONFIRMED · 미확정")).toHaveLength(12);
    for (const name of ["에스피소프트", "CJ CGV", "NHN", "대한유화", "079160"]) {
      expect(within(table).getByText(name)).toBeInTheDocument();
    }
    expect(table).not.toHaveTextContent("0%");
    expect(within(table).getAllByText(/Return — \/ Benchmark — \/ Excess —/)).toHaveLength(12);
    expect(completed).not.toHaveBeenCalled();
  });

  it("reports invalid inputs without showing signals", async () => {
    vi.spyOn(dashboardApi, "getExpandedPartialValidation").mockRejectedValue(new Error("Signal date mismatch"));
    render(<ExpandedPartialValidation />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Signal date mismatch");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("reports missing inputs", async () => {
    vi.spyOn(dashboardApi, "getExpandedPartialValidation").mockResolvedValue({ ...response, status: "MISSING", items: [] });
    render(<ExpandedPartialValidation />);
    expect(await screen.findByText("저장된 PARTIAL Validation 대상 없음")).toBeInTheDocument();
  });

  it("keeps PARTIAL Validation navigation separate from completed and PARTIAL signals", async () => {
    window.history.replaceState({}, "", "/partial-validation");
    vi.spyOn(dashboardApi, "getExpandedPartialValidation").mockResolvedValue(response);
    const completed = vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockRejectedValue(new Error("completed fixture"));
    const partial = vi.spyOn(dashboardApi, "getExpandedPartialBoard").mockResolvedValue(response);
    render(<App />);
    await screen.findByText("2026-11-09 · PARTIAL");
    expect(completed).not.toHaveBeenCalled();
    expect(partial).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Expanded 574" }));
    await waitFor(() => expect(completed).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Expanded PARTIAL" }));
    await waitFor(() => expect(partial).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "PARTIAL Validation" }));
    expect(await screen.findByRole("region", { name: "PARTIAL Validation results" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/partial-validation");
  });
});
