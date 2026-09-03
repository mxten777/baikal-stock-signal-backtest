import { useEffect, useState, useCallback } from "react";
import { Header } from "./components/Header/Header";
import { ErrorBoundary } from "./components/ErrorBoundary/ErrorBoundary";
import { SystemStatus } from "./features/system-status/SystemStatus";
import { TodaysShadow } from "./features/todays-shadow/TodaysShadow";
import { MaturityMonitor } from "./features/maturity/MaturityMonitor";
import { PerformanceOverview } from "./features/performance/PerformanceOverview";
import { ForeignFlowMonitor } from "./features/foreign-flow/ForeignFlowMonitor";
import { WeaknessMonitor } from "./features/weakness/WeaknessMonitor";
import { RiskMonitor } from "./features/risk/RiskMonitor";
import { OpportunityCostMonitor } from "./features/opportunity-cost/OpportunityCostMonitor";
import { SignalLedger } from "./features/signal-ledger/SignalLedger";
import { dashboardApi, DashboardApiError } from "./api/dashboardApi";
import { DashboardOverviewResponse } from "./types/dashboard";
import "./index.css";

export function App() {
  const [data, setData] = useState<DashboardOverviewResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const overview = await dashboardApi.getOverview();
      setData(overview);
      setLastUpdated(new Date());
    } catch (err: unknown) {
      if (err instanceof DashboardApiError) {
        setError(`API Error (${err.status}): ${err.message}`);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Failed to fetch dashboard data from adapter.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const system = data?.system;
  const ledgerStatus = data?.signal_ledger?.status || "MISSING";
  const headerStatus = resolveHeaderStatus(
    system?.input_data_freshness?.value || system?.input_data_freshness?.status,
    system?.ledger_status?.value || ledgerStatus
  );

  return (
    <div className="app-container">
      <Header
        mode={system?.mode || "SHADOW"}
        isReadOnly={system?.read_only ?? true}
        baselineCommit={system?.baseline_commit || "e4d38f7"}
        dataStatus={headerStatus.status}
        dataStatusLabel={headerStatus.label}
      />

      <main className="main-content">
        {/* Top Control Bar */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "0.1rem 0",
          }}
        >
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
            <span>
              Last synced with Adapter:{" "}
              <strong style={{ color: "var(--text-secondary)", fontFamily: "var(--font-mono)" }}>
                {lastUpdated ? lastUpdated.toLocaleTimeString() : "—"}
              </strong>
            </span>
          </div>
          <div>
            <button
              onClick={loadData}
              disabled={loading}
              style={{
                backgroundColor: "var(--bg-surface-elevated)",
                color: "var(--text-secondary)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "4px",
                padding: "0.25rem 0.65rem",
                fontSize: "0.72rem",
                fontWeight: 600,
                display: "inline-flex",
                alignItems: "center",
                gap: "0.35rem",
              }}
            >
              {loading ? "Refreshing..." : "↻ Refresh View"}
            </button>
          </div>
        </div>

        {error && (
          <div
            style={{
              backgroundColor: "rgba(239, 68, 68, 0.1)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              borderRadius: "4px",
              padding: "0.6rem 0.85rem",
              color: "#fca5a5",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div>
              <strong style={{ color: "#ef4444", display: "block", fontSize: "0.75rem" }}>
                Adapter Connection Error
              </strong>
              <span style={{ fontSize: "0.72rem" }}>{error}</span>
            </div>
            <button
              onClick={loadData}
              style={{
                backgroundColor: "#ef4444",
                color: "#fff",
                border: "none",
                borderRadius: "3px",
                padding: "0.25rem 0.6rem",
                fontSize: "0.72rem",
                fontWeight: 600,
              }}
            >
              Retry
            </button>
          </div>
        )}

        <ErrorBoundary fallbackTitle="System Status Error">
          <SystemStatus system={data?.system} />
        </ErrorBoundary>

        <ErrorBoundary fallbackTitle="Today's Shadow Error">
          <TodaysShadow today={data?.today} />
        </ErrorBoundary>

        <div className="grid-2col">
          <ErrorBoundary fallbackTitle="Maturity Monitor Error">
            <MaturityMonitor maturity={data?.maturity} />
          </ErrorBoundary>

          <ErrorBoundary fallbackTitle="Performance Overview Error">
            <PerformanceOverview performance={data?.performance} />
          </ErrorBoundary>
        </div>

        <div className="grid-2col">
          <ErrorBoundary fallbackTitle="Foreign Flow Error">
            <ForeignFlowMonitor foreignFlow={data?.foreign_flow} />
          </ErrorBoundary>

          <ErrorBoundary fallbackTitle="Weakness Monitor Error">
            <WeaknessMonitor weakness={data?.weakness} />
          </ErrorBoundary>
        </div>

        <div className="grid-2col">
          <ErrorBoundary fallbackTitle="Risk Monitor Error">
            <RiskMonitor risk={data?.risk} />
          </ErrorBoundary>

          <ErrorBoundary fallbackTitle="Opportunity Cost Error">
            <OpportunityCostMonitor opportunityCost={data?.opportunity_cost} />
          </ErrorBoundary>
        </div>

        <ErrorBoundary fallbackTitle="Signal Ledger Error">
          <SignalLedger ledger={data?.signal_ledger} />
        </ErrorBoundary>
      </main>

      <footer
        style={{
          backgroundColor: "var(--bg-surface)",
          borderTop: "1px solid var(--border-subtle)",
          padding: "0.65rem 1.5rem",
          fontSize: "0.7rem",
          color: "var(--text-muted)",
          textAlign: "center",
          marginTop: "1rem",
        }}
      >
        BAIKAL Stock Signal Control Tower • READ ONLY Architecture • All signal data is decoupled from live execution.
      </footer>
    </div>
  );
}

function resolveHeaderStatus(inputFreshness?: string | null, ledgerStatus?: string | null) {
  const input = String(inputFreshness || "UNAVAILABLE").toUpperCase();
  const ledger = String(ledgerStatus || "UNAVAILABLE").toUpperCase();
  if (input === "STALE") {
    return { status: "STALE", label: "MARKET DATA STALE" };
  }
  if (input === "MISSING") {
    return { status: "MISSING", label: "MARKET DATA MISSING" };
  }
  if (input === "UNAVAILABLE") {
    return { status: "UNAVAILABLE", label: "MARKET DATA UNAVAILABLE" };
  }
  if (ledger === "MISSING") {
    return { status: "MISSING", label: "LEDGER MISSING" };
  }
  if (ledger === "AVAILABLE") {
    return { status: "AVAILABLE", label: "DATA CURRENT" };
  }
  return { status: ledger, label: `LEDGER ${ledger}` };
}

export default App;
