import {
  DashboardHealthResponse,
  DashboardOverviewResponse,
  SignalLedgerData,
} from "../types/dashboard";
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
  runManualDailyOperation: (): Promise<ManualRunResult> => postJson<ManualRunResult>("/api/operations/manual-run", {}),
};
