import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { dashboardApi } from "../api/dashboardApi";
import { ExpandedPartialBoard } from "../features/expanded-shadow/ExpandedPartialBoard";
import { ExpandedPartialResponse } from "../types/expandedShadow";

const response: ExpandedPartialResponse = {
  read_only: true, status: "PARTIAL", warnings: [],
  items: [{
    status: "PARTIAL", source_date: "2026-11-09", run_id: "partial-test",
    validation_status: "NOT_RUN", promoted: false, universe: 574, attempted: 572,
    ready: 572, suspended: 1, unknown: 1, unresolved: 1, signals: 4, candidates: 2,
    signal_excluded: 2, excluded_tickers: ["032580"], unknown_tickers: ["018000"],
    records: [
      { ticker: "443670", name: "에스피소프트", market: "KOSDAQ", decision: "CANDIDATE", signal_type: "STRONG_WATCH", signal_score: 86.2 },
      { ticker: "079160", name: "CJ CGV", market: "KOSPI", decision: "CANDIDATE", signal_type: "BUY_WATCH", signal_score: 75.4 },
    ],
  }],
};

describe("Isolated PARTIAL results", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  it("shows partial counts and never claims complete or validated", async () => {
    vi.spyOn(dashboardApi, "getExpandedPartialBoard").mockResolvedValue(response);
    const completed = vi.spyOn(dashboardApi, "getExpandedSignalBoard");
    render(<ExpandedPartialBoard />);
    expect(await screen.findByText("2026-11-09 · PARTIAL")).toBeInTheDocument();
    expect(screen.getByText("처리 572/574 · READY 572 · 공식 정지 제외 1 · UNKNOWN 1")).toBeInTheDocument();
    expect(screen.getByText("신호 4 · 후보 2 · 신호단계 제외 2")).toBeInTheDocument();
    expect(screen.getByText(/전체 완료 아님 · Validation 미실행 · 운영 반영 없음/)).toBeInTheDocument();
    expect(screen.getByText("에스피소프트")).toBeInTheDocument();
    expect(screen.getByText("CJ CGV")).toBeInTheDocument();
    expect(screen.getByText("079160")).toBeInTheDocument();
    expect(completed).not.toHaveBeenCalled();
  });

  it("surfaces API validation failure without displaying results", async () => {
    vi.spyOn(dashboardApi, "getExpandedPartialBoard").mockRejectedValue(new Error("Signal date mismatch"));
    render(<ExpandedPartialBoard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Signal date mismatch");
    expect(screen.queryByText("CJ CGV")).not.toBeInTheDocument();
  });

  it("shows missing data explicitly", async () => {
    vi.spyOn(dashboardApi, "getExpandedPartialBoard").mockResolvedValue({ ...response, status: "MISSING", items: [] });
    render(<ExpandedPartialBoard />);
    expect(await screen.findByText("저장된 PARTIAL 결과 없음")).toBeInTheDocument();
  });

  it("opens a separate route and retains completed navigation", async () => {
    window.history.replaceState({}, "", "/expanded-partial");
    vi.spyOn(dashboardApi, "getExpandedPartialBoard").mockResolvedValue(response);
    const completed = vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockRejectedValue(new Error("completed fixture"));
    render(<App />);
    await screen.findByText("2026-11-09 · PARTIAL");
    expect(completed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Expanded 574" }));
    await waitFor(() => expect(completed).toHaveBeenCalledTimes(1));
    expect(window.location.pathname).toBe("/expanded-shadow");
    fireEvent.click(screen.getByRole("button", { name: "Expanded PARTIAL" }));
    expect(await screen.findByText("2026-11-09 · PARTIAL")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/expanded-partial");
  });
});
