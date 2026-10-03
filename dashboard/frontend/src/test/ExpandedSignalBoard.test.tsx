import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { dashboardApi } from "../api/dashboardApi";
import { ExpandedSignalBoard } from "../features/expanded-shadow/ExpandedSignalBoard";
import { ExpandedSignalBoardResponse, ExpandedSignalRecord } from "../types/expandedShadow";
import { defaultMissingOverviewFixture } from "./fixtures";


function board(overrides: Partial<ExpandedSignalBoardResponse> = {}): ExpandedSignalBoardResponse {
  const signalRecords: ExpandedSignalRecord[] = [
    {
      basDd: "2026-09-17",
      ticker: "000001",
      stock_name: "Leading Zero",
      market: "KOSPI",
      signal_date: "2026-09-17",
      signal_price: 101000,
      raw_score: 52,
      signal_score: 81.5,
      signal_type: "BUY_WATCH",
      company_profile: {
        company_name: "Leading Zero",
        sector: "반도체",
        main_business_products: "메모리 제품",
        one_line_description: "반도체와 메모리 제품을 생산하는 회사입니다.",
        market_cap: null,
        market_cap_date: null,
        profile_as_of: "2026-09-17",
        source: "KRX_KIND_LISTING; one_line_description=GENERATED_TEMPLATE",
      },
      decision_evidence: {
        signal_reason: "Score crossed threshold: 74.2 -> 81.5 (threshold 75)",
        prev_score: 74.2,
        current_score: 81.5,
        trend_score: 25,
        volume_score: 15,
        momentum_score: 13,
        foreign_status: "POSITIVE",
        foreign_5d_ratio: 0.2,
        decision: "CANDIDATE",
        decision_reason: "Foreign status POSITIVE is not NEGATIVE; existing rule classifies as CANDIDATE.",
        evidence_status: "PARTIAL",
      },
      performance: {
        tracking_status: "5D",
        return_5d: 1.2,
        excess_5d: 0.4,
        return_10d: null,
        excess_10d: null,
        return_20d: null,
        excess_20d: null,
      },
    },
    {
      basDd: "2026-09-17",
      ticker: "000002",
      stock_name: "Excluded Co",
      market: "KOSDAQ",
      signal_date: "2026-09-17",
      signal_price: 5000,
      raw_score: 52,
      signal_score: 80,
      signal_type: "BUY_WATCH",
      company_profile: null,
      decision_evidence: {
        signal_reason: "Score crossed threshold: 73.8 -> 80.0 (threshold 75)",
        prev_score: 73.8,
        current_score: 80,
        trend_score: null,
        volume_score: null,
        momentum_score: null,
        foreign_status: "NEGATIVE",
        foreign_5d_ratio: -0.2,
        decision: "EXCLUDED",
        decision_reason: "FOREIGN_NEGATIVE",
        evidence_status: "UNAVAILABLE",
      },
      performance: null,
    },
  ];
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
    signal_records: signalRecords,
    signal_records_warnings: [],
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
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("성과 측정 중1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("5거래일 성과 확인1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("10거래일 성과 확인1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("20거래일 성과 확인1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("성과 추적 완료1");
    expect(screen.getByText("+3.25%")).toBeInTheDocument();
    expect(screen.getByText("excess +1.10%")).toBeInTheDocument();
    expect(screen.getByText("EXCLUDED signals")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.queryByText("Run Daily Operation")).not.toBeInTheDocument();
  });

  it("shows candidate and excluded evidence, profile fallbacks, and tracking scope in details", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    const detailSummaries = screen.getAllByText("상세");
    await act(async () => { (detailSummaries[0] as HTMLElement).click(); });
    await act(async () => { (detailSummaries[1] as HTMLElement).click(); });

    expect(screen.getAllByText("판정 이유")).toHaveLength(2);
    expect(screen.getByText("반도체" )).toBeInTheDocument();
    expect(screen.getAllByText("Leading Zero").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("성과 추적 대상 아님")).toBeInTheDocument();
    expect(screen.getAllByText("근거 상태")).toHaveLength(2);
    expect(screen.getByText("근거 확인 불가")).toBeInTheDocument();
    expect(screen.getByText("일부 근거 확인")).toBeInTheDocument();
    expect(screen.getByText("점수가 기준 75를 상향 돌파: 74.2 → 81.5")).toBeInTheDocument();
    expect(screen.getByText("외국인 수급이 NEGATIVE여서 EXCLUDED로 분류")).toBeInTheDocument();
    expect(screen.getAllByText("5거래일 성과 확인").length).toBeGreaterThanOrEqual(2);
  });

  it("shows the Korean label for AVAILABLE evidence without changing its source status", async () => {
    const available = board();
    available.signal_records![0].decision_evidence.evidence_status = "AVAILABLE";
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(available);

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    await act(async () => { (screen.getAllByText("상세")[0] as HTMLElement).click(); });

    expect(screen.getByText("근거 확인 완료")).toBeInTheDocument();
    expect(available.signal_records![0].decision_evidence.evidence_status).toBe("AVAILABLE");
  });

  it("shows company information and verified evidence without opening details", async () => {
    const available = board();
    available.signal_records![0].decision_evidence.evidence_status = "AVAILABLE";
    const before = JSON.stringify(available);
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(available);
    const { container } = render(<ExpandedSignalBoard />);

    await screen.findByText("선정근거 요약");
    const summary = container.querySelector(".expanded-candidate-summary");
    expect(summary).toHaveTextContent("점수 74.2 → 81.5, 기준 75 상향 돌파 · 외국인 POSITIVE");
    const company = container.querySelector(".expanded-candidate-company");
    expect(company).toHaveTextContent("업종: 반도체");
    expect(company).toHaveTextContent("주요사업: 메모리 제품");
    expect(container.querySelector("details")).not.toHaveAttribute("open");
    expect(JSON.stringify(available)).toBe(before);
  });

  it.each(["PARTIAL", "UNAVAILABLE"] as const)("does not assert a threshold crossing for %s summary evidence", async (status) => {
    const payload = board();
    payload.signal_records![0].decision_evidence.evidence_status = status;
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(payload);
    const { container } = render(<ExpandedSignalBoard />);

    await screen.findByText("선정근거 요약");
    const summary = container.querySelector(".expanded-candidate-summary");
    expect(summary).toHaveTextContent(status);
    expect(summary).toHaveTextContent(status === "PARTIAL" ? "일부 근거 확인" : "근거 확인 불가");
    expect(summary).toHaveTextContent("외국인 POSITIVE");
    expect(summary).not.toHaveTextContent("상향 돌파");
  });

  it("shortens long business text but preserves it in details", async () => {
    const payload = board();
    const business = "반도체 메모리 및 시스템 솔루션 ".repeat(20).trim();
    payload.signal_records![0].company_profile!.main_business_products = business;
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(payload);
    const { container } = render(<ExpandedSignalBoard />);

    await screen.findByText("선정근거 요약");
    expect(container.querySelector(".expanded-candidate-company")).toHaveTextContent(
      `주요사업: ${Array.from(business).slice(0, 60).join("")}…`,
    );
    const detail = container.querySelector("details");
    expect(detail).not.toBeNull();
    if (!detail) throw new Error("Candidate details missing");
    await act(async () => { within(detail).getByText("상세").click(); });
    expect(within(detail).getByText(business, { exact: true })).toBeInTheDocument();
  });

  it("handles absent profiles and evidence without inventing company or score data", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board({ signal_records: [] }));
    const { container } = render(<ExpandedSignalBoard />);

    await screen.findByText("선정근거 요약");
    expect(container.querySelector(".expanded-candidate-company")).toHaveTextContent("업종: —");
    expect(container.querySelector(".expanded-candidate-company")).toHaveTextContent("주요사업: —");
    expect(container.querySelector(".expanded-candidate-summary")).toHaveTextContent("UNAVAILABLE · 근거 확인 불가");
    expect(container.querySelector(".expanded-candidate-summary")).not.toHaveTextContent("상향 돌파");
  });

  it("does not invent a threshold when AVAILABLE evidence has no signal reason", async () => {
    const payload = board();
    payload.signal_records![0].decision_evidence.evidence_status = "AVAILABLE";
    payload.signal_records![0].decision_evidence.signal_reason = null;
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(payload);
    const { container } = render(<ExpandedSignalBoard />);

    await screen.findByText("선정근거 요약");
    expect(container.querySelector(".expanded-candidate-summary")).toHaveTextContent("AVAILABLE · 근거 확인 완료 · 외국인 POSITIVE");
    expect(container.querySelector(".expanded-candidate-summary")).not.toHaveTextContent("상향 돌파");
  });

  it("shows null market cap as pending while preserving a supplied value", async () => {
    const payload = board();
    payload.signal_records![1].company_profile = {
      ...payload.signal_records![0].company_profile!,
      company_name: "Excluded Co",
      market_cap: 825_000_000_000,
    };
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(payload);
    const { container } = render(<ExpandedSignalBoard />);

    await screen.findByText("선정근거 요약");
    const details = container.querySelectorAll("details");
    await act(async () => { within(details[0]).getByText("상세").click(); });
    expect(within(details[0]).getByText("확인 보류")).toBeInTheDocument();
    expect(within(details[0]).getByText("시가총액").nextElementSibling).not.toHaveTextContent(/^0$/);
    expect(within(details[1]).getByText((825_000_000_000).toLocaleString())).toBeInTheDocument();
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