import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
        delta_score: null,
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
        benchmark_5d: 0.8,
        excess_5d: 0.4,
        return_10d: null,
        benchmark_10d: null,
        excess_10d: null,
        return_20d: null,
        benchmark_20d: null,
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
        delta_score: null,
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
      records: [{ stock_name: "Leading Zero", ticker: "000001", market: "KOSPI", signal_date: "2026-09-17", entry_price: 101000, signal_score: 81.5, signal_type: "BUY_WATCH", foreign_status: "POSITIVE" }],
    },
    status_summary: { OPEN: 1, "5D": 1, "10D": 1, "20D": 1, COMPLETE: 1 },
    performance: {
      status: "READY",
      source: "output/expanded_shadow/expanded_candidate_performance_ledger.csv",
      count: 1,
      records: [{ stock_name: "Leading Zero", ticker: "000001", signal_date: "2026-09-17", tracking_status: "OPEN", return_5d: null, benchmark_5d: null, excess_5d: null, return_10d: 3.25, benchmark_10d: 2.15, excess_10d: 1.1, return_20d: null, benchmark_20d: null, excess_20d: null }],
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

  describe("Mobile Expanded presentation", () => {
    afterEach(() => {
      vi.restoreAllMocks();
      vi.useRealTimers();
    });

    async function renderMobile(payload = board()) {
      vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(payload);
      render(<ExpandedSignalBoard />);
      return within(await screen.findByRole("region", { name: "Mobile Expanded presentation" }));
    }

    function field(element: HTMLElement, label: string) {
      const value = within(element).getAllByText(label)[0].nextElementSibling;
      if (!value) throw new Error(`Missing value for ${label}`);
      return value;
    }

    it("shows only the required summary fields and the stored completion time in Korea time", async () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2040-01-01T00:00:00Z"));
      const mobile = await renderMobile();
      const summary = mobile.getByRole("region", { name: "Mobile Summary" });
      expect(field(summary, "분석일")).toHaveTextContent("2026-09-17");
      expect(field(summary, "Universe")).toHaveTextContent("574");
      expect(field(summary, "Signals")).toHaveTextContent("26");
      expect(field(summary, "Candidates")).toHaveTextContent("14");
      expect(field(summary, "Excluded")).toHaveTextContent("12");
      expect(field(summary, "분석 완료 시각")).toHaveTextContent("2026");
      expect(field(summary, "분석 완료 시각")).toHaveTextContent(/18(?::|시 )0?6(?::|분 )15/);
      expect(field(summary, "분석 완료 시각")).toHaveTextContent(/KST|GMT\+9/);
      expect(summary).not.toHaveTextContent("2040");
      expect(summary.querySelectorAll(".expanded-fact")).toHaveLength(6);
      expect(summary).not.toHaveTextContent("Attempted");
    });

    it.each([null, "", "invalid", "2026-02-30T09:00:00Z", "2026-09-17", "2026-09-17T09:00:00", "2026-09-17T25:00:00Z"])(
      "shows a dash without a current-time fallback for finished_at=%s", async (finishedAt) => {
        vi.useFakeTimers({ toFake: ["Date"] });
        vi.setSystemTime(new Date("2040-01-01T00:00:00Z"));
        const payload = board();
        payload.run_summary.finished_at = finishedAt;
        const mobile = await renderMobile(payload);
        expect(field(mobile.getByRole("region", { name: "Mobile Summary" }), "분석 완료 시각")).toHaveTextContent(/^—$/);
      },
    );

    it("renders a card using canonical evidence and performance without mutating the payload", async () => {
      const payload = board();
      payload.signal_records![0].decision_evidence.evidence_status = "AVAILABLE";
      payload.signal_records![0].decision_evidence.delta_score = 7.3;
      const before = JSON.stringify(payload);
      const mobile = await renderMobile(payload);
      const card = mobile.getByRole("article", { name: "Leading Zero 000001" });
      expect(within(card).getByRole("heading", { name: "Leading Zero" })).toBeInTheDocument();
      expect(card).toHaveTextContent("000001 / KOSPI");
      expect(card).toHaveTextContent("CANDIDATE");
      expect(field(card, "Score movement")).toHaveTextContent("74.2 → 81.5 (+7.3)");
      expect(field(card, "Signal Price")).toHaveTextContent("101,000");
      expect(field(card, "Foreign")).toHaveTextContent("POSITIVE");
      expect(card.querySelector(".expanded-mobile-reason")).toHaveTextContent("기준 75 상향 돌파");
      const performance = within(card).getByRole("region", { name: "5D Performance" });
      expect(performance).toHaveTextContent("Return +1.20%");
      expect(performance).toHaveTextContent("Benchmark +0.80%");
      expect(performance).toHaveTextContent("Excess +0.40%p");
      expect(performance).not.toHaveTextContent("+3.25%");
      expect(card.querySelector("details")).not.toHaveAttribute("open");
      expect(JSON.stringify(payload)).toBe(before);
    });

    it.each(["OVERHEATED", "BUY_WATCH", "overheated", null])("uses the exact ledger OVERHEATED condition for %s", async (signalType) => {
      const payload = board();
      payload.new_candidates.records[0].signal_type = signalType;
      const mobile = await renderMobile(payload);
      const card = mobile.getByRole("article");
      expect(within(card).queryByText("OVERHEATED") !== null).toBe(signalType === "OVERHEATED");
    });

    it.each(["PARTIAL", "UNAVAILABLE"] as const)("preserves %s score and reason safeguards", async (status) => {
      const payload = board();
      payload.signal_records![0].decision_evidence.evidence_status = status;
      payload.signal_records![0].decision_evidence.delta_score = 7.3;
      const mobile = await renderMobile(payload);
      const card = mobile.getByRole("article");
      expect(field(card, "Score movement")).toHaveTextContent(/^74.2 → 81.5$/);
      expect(card.querySelector(".expanded-mobile-reason")).toHaveTextContent(status);
      expect(card.querySelector(".expanded-mobile-reason")).not.toHaveTextContent("상향 돌파");
    });

    it("opens and closes native details with signal fields, company information, and all horizons", async () => {
      const payload = board();
      const performance = payload.signal_records![0].performance!;
      Object.assign(performance, {
        return_10d: -2, benchmark_10d: 0, excess_10d: -1.25,
        return_20d: 8, benchmark_20d: 4, excess_20d: 3.5,
      });
      const mobile = await renderMobile(payload);
      const card = mobile.getByRole("article");
      const details = card.querySelector("details");
      if (!details) throw new Error("Missing mobile details");
      const toggle = within(details).getByText("상세보기");
      expect(details).not.toHaveAttribute("open");
      fireEvent.click(toggle);
      expect(details).toHaveAttribute("open");
      expect(field(details, "Signal Date")).toHaveTextContent("2026-09-17");
      expect(field(details, "Signal Price")).toHaveTextContent("101,000");
      expect(field(details, "점수")).toHaveTextContent("74.2 → 81.5");
      expect(field(details, "Trend")).toHaveTextContent("25.0");
      expect(field(details, "Momentum")).toHaveTextContent("13.0");
      expect(field(details, "Volume")).toHaveTextContent("15.0");
      expect(field(details, "Foreign")).toHaveTextContent("POSITIVE");
      expect(field(details, "foreign_5d_ratio")).toHaveTextContent("0.2000");
      expect(field(details, "판정 이유")).toHaveTextContent("CANDIDATE로 분류");
      expect(field(details, "업종")).toHaveTextContent("반도체");
      expect(field(details, "주요 사업/제품")).toHaveTextContent("메모리 제품");
      expect(field(details, "시가총액")).toHaveTextContent("확인 보류");
      expect(within(details).getByText("한줄 소개")).toBeInTheDocument();
      const horizons = details.querySelectorAll(".expanded-detail-performance > div");
      expect(horizons).toHaveLength(3);
      expect(horizons[0]).toHaveTextContent("5DReturn +1.20%Benchmark +0.80%Excess +0.40%p");
      expect(horizons[1]).toHaveTextContent("10DReturn -2.00%Benchmark 0.00%Excess -1.25%p");
      expect(horizons[2]).toHaveTextContent("20DReturn +8.00%Benchmark +4.00%Excess +3.50%p");
      fireEvent.click(toggle);
      expect(details).not.toHaveAttribute("open");
    });

    it("preserves immature horizons and missing benchmarks independently, without calculating excess", async () => {
      const payload = board();
      payload.signal_records![0].performance!.benchmark_5d = null;
      payload.signal_records![0].performance!.excess_5d = null;
      const mobile = await renderMobile(payload);
      const card = mobile.getByRole("article");
      const performance = within(card).getByRole("region", { name: "5D Performance" });
      expect(performance).toHaveTextContent("Return +1.20%Benchmark —Excess —");
      const horizons = card.querySelectorAll(".expanded-detail-performance > div");
      expect(horizons[1]).toHaveTextContent("10DReturn —Benchmark —Excess —");
      expect(horizons[2]).toHaveTextContent("20DReturn —Benchmark —Excess —");
    });

    it("shows absent performance and evidence explicitly without inventing values", async () => {
      const mobile = await renderMobile(board({ signal_records: [] }));
      const card = mobile.getByRole("article");
      expect(field(card, "Score movement")).toHaveTextContent("N/A");
      expect(card.querySelector(".expanded-mobile-reason")).toHaveTextContent("UNAVAILABLE · 근거 확인 불가");
      expect(within(card).getByRole("region", { name: "5D Performance" })).toHaveTextContent("Return —Benchmark —Excess —");
      expect(card).toHaveTextContent("성과 데이터 없음");
      expect(card.querySelector(".expanded-detail-unavailable")).toHaveTextContent("UNAVAILABLE · 근거 확인 불가");
      const details = card.querySelector("details");
      if (!details) throw new Error("Missing unavailable-evidence details");
      fireEvent.click(within(details).getByText("상세보기"));
      expect(details).toHaveAttribute("open");
      expect(field(details, "Signal Date")).toHaveTextContent("2026-09-17");
      expect(field(details, "Signal Price")).toHaveTextContent("101,000");
    });

    it("matches ticker AND signal date rather than choosing another cohort's record", async () => {
      const payload = board();
      const correct = payload.signal_records![0];
      payload.signal_records!.unshift({
        ...correct, signal_date: "2026-09-16",
        company_profile: { ...correct.company_profile!, company_name: "Wrong Cohort" },
        performance: { ...correct.performance!, return_5d: 99 },
      });
      const mobile = await renderMobile(payload);
      const card = mobile.getByRole("article");
      expect(card).not.toHaveTextContent("Wrong Cohort");
      expect(card).not.toHaveTextContent("+99.00%");
      expect(card).toHaveTextContent("Return +1.20%");
    });

    it("renders a normal empty state when there are no latest candidates", async () => {
      const payload = board();
      payload.new_candidates.records = [];
      payload.new_candidates.count = 0;
      const mobile = await renderMobile(payload);
      expect(mobile.queryByRole("article")).not.toBeInTheDocument();
      expect(mobile.getByText("최신 Expanded run의 신규 CANDIDATE가 없습니다.")).toBeInTheDocument();
    });
  });

  it("renders the read-only board, run summary, candidates, lifecycle, and performance", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());

    render(<ExpandedSignalBoard />);

    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    const desktop = within(screen.getByRole("region", { name: "Desktop Expanded presentation" }));
    expect(screen.getByText("EXPANDED SHADOW · READ ONLY")).toBeInTheDocument();
    expect(desktop.getByText("Run Summary")).toBeInTheDocument();
    expect(desktop.getByText("Universe")).toBeInTheDocument();
    expect(screen.getAllByText("574").length).toBeGreaterThanOrEqual(2);
    expect(desktop.getByText("New Candidates")).toBeInTheDocument();
    expect(desktop.getByText("Signal Price")).toBeInTheDocument();
    expect(screen.queryByText("Entry Price")).not.toBeInTheDocument();
    expect(desktop.getByText("101,000")).toBeInTheDocument();
    expect(screen.queryByText("OVERHEATED")).not.toBeInTheDocument();
    expect(desktop.getAllByText("Leading Zero")).toHaveLength(2);
    expect(desktop.getAllByText("000001")).toHaveLength(2);
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("성과 측정 중1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("5거래일 성과 확인1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("10거래일 성과 확인1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("20거래일 성과 확인1");
    expect(screen.getByLabelText("Performance status summary")).toHaveTextContent("성과 추적 완료1");
    expect(screen.getByText("Return +3.25%")).toBeInTheDocument();
    expect(screen.getByText("Benchmark +2.15%")).toBeInTheDocument();
    expect(screen.getByText("Excess +1.10%p")).toBeInTheDocument();
    expect(screen.getByText("EXCLUDED signals")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.queryByText("Run Daily Operation")).not.toBeInTheDocument();
  });

  it("shows candidate and excluded evidence, profile fallbacks, and tracking scope in details", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    const desktop = within(screen.getByRole("region", { name: "Desktop Expanded presentation" }));
    const detailSummaries = desktop.getAllByText("상세");
    await act(async () => { (detailSummaries[0] as HTMLElement).click(); });
    await act(async () => { (detailSummaries[1] as HTMLElement).click(); });

    expect(desktop.getAllByText("판정 이유")).toHaveLength(2);
    expect(desktop.getByText("반도체" )).toBeInTheDocument();
    expect(desktop.getAllByText("Leading Zero").length).toBeGreaterThanOrEqual(2);
    expect(desktop.getByText("성과 추적 대상 아님")).toBeInTheDocument();
    expect(desktop.getAllByText("근거 상태")).toHaveLength(2);
    expect(desktop.getByText("근거 확인 불가")).toBeInTheDocument();
    expect(desktop.getByText("일부 근거 확인")).toBeInTheDocument();
    expect(desktop.getByText("점수가 기준 75를 상향 돌파: 74.2 → 81.5")).toBeInTheDocument();
    expect(desktop.getByText("Benchmark +0.80%")).toBeInTheDocument();
    expect(desktop.getByText("Excess +0.40%p")).toBeInTheDocument();
    expect(desktop.getAllByText("Benchmark —")).toHaveLength(4);
    expect(desktop.getByText("외국인 수급이 NEGATIVE여서 EXCLUDED로 분류")).toBeInTheDocument();
    expect(desktop.getAllByText("5거래일 성과 확인").length).toBeGreaterThanOrEqual(2);
  });

  it("shows the Korean label for AVAILABLE evidence without changing its source status", async () => {
    const available = board();
    available.signal_records![0].decision_evidence.evidence_status = "AVAILABLE";
    available.signal_records![0].decision_evidence.delta_score = 7.3;
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(available);

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    await act(async () => { (screen.getAllByText("상세")[0] as HTMLElement).click(); });

    const desktop = within(screen.getByRole("region", { name: "Desktop Expanded presentation" }));
    expect(desktop.getByText("근거 확인 완료")).toBeInTheDocument();
    expect(desktop.getByText("74.2 → 81.5 (+7.3)")).toBeInTheDocument();
    expect(available.signal_records![0].decision_evidence.evidence_status).toBe("AVAILABLE");
  });

  it.each(["PARTIAL", "UNAVAILABLE"] as const)("does not show a score delta for %s details", async (status) => {
    const payload = board();
    payload.signal_records![0].decision_evidence.evidence_status = status;
    payload.signal_records![0].decision_evidence.delta_score = 7.3;
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(payload);
    const { container } = render(<ExpandedSignalBoard />);

    await screen.findByText("선정근거 요약");
    const candidateDetails = container.querySelector("details");
    await act(async () => { within(candidateDetails as HTMLElement).getByText("상세").click(); });

    const score = within(candidateDetails as HTMLElement).getByText("점수").nextElementSibling;
    expect(score).toHaveTextContent("74.2 → 81.5");
    expect(score).not.toHaveTextContent("(+7.3)");
  });

  it("shows OVERHEATED only when the candidate ledger signal type says so", async () => {
    const overheated = board();
    overheated.new_candidates.records[0].signal_type = "OVERHEATED";
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(overheated);
    const { container } = render(<ExpandedSignalBoard />);

    await screen.findAllByText("OVERHEATED");
    const decisionCell = container.querySelector(".decision-candidate")?.parentElement;
    expect(decisionCell).toHaveTextContent("CANDIDATE OVERHEATED");
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