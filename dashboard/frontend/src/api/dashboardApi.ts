import {
  DashboardHealthResponse,
  DashboardOverviewResponse,
  DailySignalBoardResponse,
  SignalLedgerData,
} from "../types/dashboard";
import { DualShadowLatest, DualShadowPerformance, DualShadowRuns, DualShadowStatus } from "../types/dualShadow";
import { ExpandedSignalBoardResponse } from "../types/expandedShadow";
import { ManualRunResult, OperationsAttempt, OperationsException, OperationsStatus, OperationsSummary } from "../types/operations";

const API_BASE = "";

export class DashboardApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    message?: string
  ) {
    super(message || `API Error: ${status} ${statusText}`);
    this.name = "DashboardApiError";
  }
}

async function getJson<T>(endpoint: string): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    let errorDetail = "";
    try {
      const errBody = await response.json();
      errorDetail = errBody.error ? ` (${errBody.error})` : "";
    } catch {
      // Ignore parse failure
    }
    throw new DashboardApiError(
      response.status,
      response.statusText,
      `Request to ${endpoint} failed: ${response.status} ${response.statusText}${errorDetail}`
    );
  }

  return response.json() as Promise<T>;
}

async function postJson<T>(endpoint: string, body: object): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, { method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) {
    let detail = "";
    try { const error = await response.json(); detail = error.error_code ? ` (${error.error_code})` : ""; } catch { /* Ignore parse failure */ }
    throw new DashboardApiError(response.status, response.statusText, `Request to ${endpoint} failed: ${response.status} ${response.statusText}${detail}`);
  }
  return response.json() as Promise<T>;
}

export type DailyReportFormat = "docx" | "pdf";

export function extractFilenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const match = header.match(/filename="?([^";]+)"?/i);
  return match ? match[1] : null;
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function downloadDailyReport(sourceDate: string, format: DailyReportFormat): Promise<void> {
  const endpoint = `/api/dashboard/expanded-shadow/daily-report?date=${encodeURIComponent(sourceDate)}&format=${format}`;
  const response = await fetch(`${API_BASE}${endpoint}`, { method: "GET" });
  if (!response.ok) {
    let detail = "";
    try { const error = await response.json(); detail = error.error_code ? ` (${error.error_code})` : ""; } catch { /* Ignore parse failure */ }
    throw new DashboardApiError(response.status, response.statusText, `Request to ${endpoint} failed: ${response.status} ${response.statusText}${detail}`);
  }
  const blob = await response.blob();
  const filename = extractFilenameFromDisposition(response.headers.get("Content-Disposition")) ?? `BAIKAL_Daily_Report_${sourceDate}.${format}`;
  triggerBrowserDownload(blob, filename);
}

export const dashboardApi = {
  getOverview: (): Promise<DashboardOverviewResponse> => {
    return getJson<DashboardOverviewResponse>("/api/dashboard/overview");
  },
  getSignals: (): Promise<SignalLedgerData> => {
    return getJson<SignalLedgerData>("/api/dashboard/signals");
  },
  getHealth: (): Promise<DashboardHealthResponse> => {
    return getJson<DashboardHealthResponse>("/api/dashboard/health");
  },
  getDailySignalBoard: (): Promise<DailySignalBoardResponse> => {
    return getJson<DailySignalBoardResponse>("/api/dashboard/daily-signal-board");
  },
  getExpandedSignalBoard: (): Promise<ExpandedSignalBoardResponse> => {
    return getJson<ExpandedSignalBoardResponse>("/api/dashboard/expanded-shadow");
  },
  getOperationsStatus: (): Promise<OperationsStatus> => getJson<OperationsStatus>("/api/operations/status"),
  getOperationsHistory: async (): Promise<OperationsSummary[]> => {
    const response = await getJson<{ items: OperationsSummary[] }>("/api/operations/history");
    return response.items;
  },
  getOperationsDetail: async (tradeDate: string): Promise<OperationsAttempt[]> => {
    const response = await getJson<{ attempts: OperationsAttempt[] }>(`/api/operations/history/${tradeDate}`);
    return response.attempts;
  },
  getOperationsException: async (tradeDate: string): Promise<OperationsException | null> => {
    const response = await getJson<{ exception: OperationsException | null }>(`/api/operations/exceptions/${tradeDate}`);
    return response.exception;
  },
  getDualShadowStatus: (): Promise<DualShadowStatus> => getJson<DualShadowStatus>("/api/dual-shadow/status"),
  getDualShadowLatest: (): Promise<DualShadowLatest> => getJson<DualShadowLatest>("/api/dual-shadow/latest"),
  getDualShadowPerformance: (): Promise<DualShadowPerformance> => getJson<DualShadowPerformance>("/api/dual-shadow/performance"),
  getDualShadowRuns: (): Promise<DualShadowRuns> => getJson<DualShadowRuns>("/api/dual-shadow/runs"),
  runManualDailyOperation: (): Promise<ManualRunResult> => postJson<ManualRunResult>("/api/operations/manual-run", {}),
  downloadDailyReport,
};
