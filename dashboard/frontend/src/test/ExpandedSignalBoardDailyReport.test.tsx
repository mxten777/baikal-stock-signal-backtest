import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardApiError, dashboardApi, extractFilenameFromDisposition } from "../api/dashboardApi";
import { ExpandedSignalBoard } from "../features/expanded-shadow/ExpandedSignalBoard";
import { ExpandedSignalBoardResponse } from "../types/expandedShadow";

function board(overrides: Partial<ExpandedSignalBoardResponse> = {}): ExpandedSignalBoardResponse {
  return {
    mode: "EXPANDED_SHADOW",
    read_only: true,
    status: "READY",
    run_summary: {
      status: "READY",
      source: "output/expanded_shadow/expanded_shadow_run.json",
      run_id: "run-1",
      source_date: "2026-09-18",
      run_status: "SUCCESS",
      universe: 574,
      attempted: 574,
      ready: 574,
      failure: 0,
      signals: 38,
      candidate: 23,
      excluded: 15,
      no_signal: 536,
      started_at: "2026-09-18T14:00:00+00:00",
      finished_at: "2026-09-18T14:21:32+00:00",
      runtime_seconds: 100,
    },
    new_candidates: { status: "READY", source: "x", source_date: "2026-09-18", count: 0, records: [] },
    status_summary: null,
    performance: { status: "MISSING", source: "x", count: 0, records: [], empty_message: "no data", status_summary: null },
    warnings: [],
    ...overrides,
  };
}

describe("Expanded Signal Board Daily Report button", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // 1: Daily Report button shown
  it("shows the Daily Report button", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());

    render(<ExpandedSignalBoard />);

    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Daily Report/ })).toBeInTheDocument();
  });

  // 2: Word/PDF options shown
  it("shows Word (.docx) and PDF (.pdf) options on click", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());

    render(<ExpandedSignalBoard />);

    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Daily Report/ }));

    expect(screen.getByRole("menuitem", { name: "Word (.docx)" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "PDF (.pdf)" })).toBeInTheDocument();
  });

  // 3 & 4: current source_date passed on docx download call
  it("passes the board's run_summary.source_date to the docx download call", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());
    const download = vi.spyOn(dashboardApi, "downloadDailyReport").mockResolvedValue();

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Daily Report/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Word (.docx)" }));

    await waitFor(() => expect(download).toHaveBeenCalledWith("2026-09-18", "docx"));
  });

  // 5: pdf download call
  it("passes the board's run_summary.source_date to the pdf download call", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());
    const download = vi.spyOn(dashboardApi, "downloadDailyReport").mockResolvedValue();

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Daily Report/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "PDF (.pdf)" }));

    await waitFor(() => expect(download).toHaveBeenCalledWith("2026-09-18", "pdf"));
  });

  // 6: no system-date fallback -- button disabled and download never invoked without a source_date
  it("never falls back to a system/today date when source_date is missing", async () => {
    const missingDateBoard = board();
    missingDateBoard.run_summary = { ...missingDateBoard.run_summary, source_date: null };
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(missingDateBoard);
    const download = vi.spyOn(dashboardApi, "downloadDailyReport").mockResolvedValue();

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: /Daily Report/ })).toBeDisabled();
    expect(download).not.toHaveBeenCalled();
  });

  // 8: loading state prevents duplicate clicks
  it("prevents duplicate clicks while a download is in progress", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());
    let resolveDownload: () => void = () => {};
    const download = vi.spyOn(dashboardApi, "downloadDailyReport").mockImplementation(
      () => new Promise((resolve) => { resolveDownload = () => resolve(); })
    );

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Daily Report/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Word (.docx)" }));

    const downloadingButton = await screen.findByRole("button", { name: /Downloading/ });
    expect(downloadingButton).toBeDisabled();
    fireEvent.click(downloadingButton);
    expect(download).toHaveBeenCalledTimes(1);

    resolveDownload();
    await waitFor(() => expect(screen.getByRole("button", { name: /Daily Report/ })).not.toBeDisabled());
  });

  // 9: API failure surfaced inline
  it("shows an inline error message when the download fails", async () => {
    vi.spyOn(dashboardApi, "getExpandedSignalBoard").mockResolvedValue(board());
    vi.spyOn(dashboardApi, "downloadDailyReport").mockRejectedValue(new Error("Daily report download failed: 404"));

    render(<ExpandedSignalBoard />);
    await waitFor(() => expect(screen.getByText("Expanded Signal Board")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Daily Report/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Word (.docx)" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Daily report download failed"));
  });
});

// 7: filename handling helper
describe("extractFilenameFromDisposition", () => {
  it("extracts a quoted filename", () => {
    expect(extractFilenameFromDisposition('attachment; filename="BAIKAL_Daily_Report_2026-09-18.docx"')).toBe(
      "BAIKAL_Daily_Report_2026-09-18.docx"
    );
  });

  it("returns null when the header is missing", () => {
    expect(extractFilenameFromDisposition(null)).toBeNull();
  });
});

describe("dashboardApi.downloadDailyReport", () => {
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;

  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(document.body, "appendChild").mockImplementation((node) => node);
  });

  afterEach(() => {
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // 4/5/7: calls the STEP 15-D endpoint and uses the response's Content-Disposition filename
  it("calls the daily-report endpoint with date and format, downloading using the response filename", async () => {
    const blob = new Blob(["binary"], { type: "application/pdf" });
    const headers = new Headers({ "Content-Disposition": 'attachment; filename="BAIKAL_Daily_Report_2026-09-18.pdf"' });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, headers, blob: () => Promise.resolve(blob) });
    vi.stubGlobal("fetch", fetchMock);
    const anchor = { click: vi.fn(), remove: vi.fn(), href: "", download: "" } as unknown as HTMLAnchorElement;
    vi.spyOn(document, "createElement").mockReturnValue(anchor);

    await dashboardApi.downloadDailyReport("2026-09-18", "pdf");

    expect(fetchMock).toHaveBeenCalledWith("/api/dashboard/expanded-shadow/daily-report?date=2026-09-18&format=pdf", { method: "GET" });
    expect(anchor.download).toBe("BAIKAL_Daily_Report_2026-09-18.pdf");
    expect(anchor.click).toHaveBeenCalledTimes(1);
  });

  // 7: fallback filename when Content-Disposition is absent
  it("falls back to a constructed filename when Content-Disposition is missing", async () => {
    const blob = new Blob(["binary"], { type: "application/octet-stream" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, headers: new Headers(), blob: () => Promise.resolve(blob) });
    vi.stubGlobal("fetch", fetchMock);
    const anchor = { click: vi.fn(), remove: vi.fn(), href: "", download: "" } as unknown as HTMLAnchorElement;
    vi.spyOn(document, "createElement").mockReturnValue(anchor);

    await dashboardApi.downloadDailyReport("2026-09-18", "docx");

    expect(anchor.download).toBe("BAIKAL_Daily_Report_2026-09-18.docx");
  });

  it("throws a DashboardApiError when the response is not ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ error_code: "REPORT_DATE_NOT_FOUND" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(dashboardApi.downloadDailyReport("2099-01-01", "docx")).rejects.toThrow(DashboardApiError);
  });
});
