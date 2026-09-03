import {
  DashboardHealthResponse,
  DashboardOverviewResponse,
  SignalLedgerData,
} from "../types/dashboard";

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
};
