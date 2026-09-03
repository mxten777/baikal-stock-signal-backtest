import React from "react";
import "./MaturityMonitor.css";
import { MaturityData } from "../../types/dashboard";
import { MetricCard } from "../../components/MetricCard/MetricCard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";
import { EmptyState } from "../../components/EmptyState/EmptyState";

interface MaturityMonitorProps {
  maturity?: MaturityData | null;
}

export const MaturityMonitor: React.FC<MaturityMonitorProps> = ({ maturity }) => {
  const isAvailable =
    maturity?.["5d"]?.matured?.status === "AVAILABLE" &&
    maturity?.["5d"]?.matured?.value !== null;

  const status = maturity?.["5d"]?.matured?.status || "UNAVAILABLE";

  return (
    <div className="panel maturity-monitor-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Maturity Monitor</span>
          <span className="panel-subtitle">
            Horizons (5D / 10D / 20D)
          </span>
        </div>
        <div className="maturity-header-badges">
          <DataKindBadge kind="operational" />
          <StatusBadge status={status} />
        </div>
      </div>

      {!isAvailable && (status === "MISSING" || status === "EMPTY" || status === "UNAVAILABLE") ? (
        <EmptyState
          status={status}
          title="No Operational Horizon Data"
          message="Operational Shadow ledger is not yet active or populated. Evaluation horizons (5D, 10D, 20D) will update once live shadow signals mature."
          dataKind="operational"
        />
      ) : (
        <div className="maturity-grid">
          <div className="horizon-card">
            <div className="horizon-title">5-Day Horizon</div>
            <div className="horizon-metrics">
              <MetricCard label="5D Matured" metric={maturity?.["5d"]?.matured} />
              <MetricCard label="5D Pending" metric={maturity?.["5d"]?.pending} />
            </div>
          </div>

          <div className="horizon-card">
            <div className="horizon-title">10-Day Horizon</div>
            <div className="horizon-metrics">
              <MetricCard label="10D Matured" metric={maturity?.["10d"]?.matured} />
              <MetricCard label="10D Pending" metric={maturity?.["10d"]?.pending} />
            </div>
          </div>

          <div className="horizon-card">
            <div className="horizon-title">20-Day Horizon</div>
            <div className="horizon-metrics">
              <MetricCard label="20D Matured" metric={maturity?.["20d"]?.matured} />
              <MetricCard label="20D Pending" metric={maturity?.["20d"]?.pending} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
