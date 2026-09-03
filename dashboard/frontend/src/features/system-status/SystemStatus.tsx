import React from "react";
import "./SystemStatus.css";
import { SystemStatusData } from "../../types/dashboard";
import { MetricCard } from "../../components/MetricCard/MetricCard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";
import { sanitizeWarning } from "../../utils/sanitize";

interface SystemStatusProps {
  system?: SystemStatusData | null;
}

export const SystemStatus: React.FC<SystemStatusProps> = ({ system }) => {
  if (!system) {
    return (
      <div className="panel system-status-panel">
        <div className="panel-header">
          <div className="panel-title-group">
            <span className="panel-title">System Status & Environment</span>
          </div>
          <StatusBadge status="UNAVAILABLE" />
        </div>
        <div className="status-grid">
          <MetricCard label="Pipeline Status" metric={null} />
          <MetricCard label="Last Run" metric={null} />
          <MetricCard label="Data Date" metric={null} />
          <MetricCard label="Freshness" metric={null} />
        </div>
      </div>
    );
  }

  const { pipeline_status, last_run, data_date, freshness, baseline_commit, warnings } = system;

  return (
    <div className="panel system-status-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">System Status & Environment</span>
          <span className="panel-subtitle">
            Baseline: <code>{baseline_commit ? baseline_commit.substring(0, 7) : "---"}</code>
          </span>
        </div>
        <div className="system-status-header-badges">
          <DataKindBadge kind="operational" />
          <StatusBadge
            status={freshness?.status || "UNAVAILABLE"}
            label={`Freshness: ${freshness?.status || "UNAVAILABLE"}`}
          />
        </div>
      </div>

      <div className="status-grid">
        <MetricCard
          label="Pipeline Status"
          metric={pipeline_status}
          sublabel="Operational engine"
        />
        <MetricCard
          label="Last Run"
          metric={last_run}
          sublabel="Pipeline scheduler"
        />
        <MetricCard
          label="Data Date"
          metric={data_date}
          sublabel="Ledger as_of date"
        />
        <MetricCard
          label="Freshness"
          metric={freshness}
          sublabel="Ledger sync state"
        />
      </div>

      {warnings && warnings.length > 0 && (
        <div className="system-warnings-container">
          <span className="system-warning-title">System Notes:</span>
          <ul className="system-warning-list">
            {warnings.map((w, idx) => {
              const sanitized = sanitizeWarning(w);
              return <li key={idx}>{sanitized}</li>;
            })}
          </ul>
        </div>
      )}
    </div>
  );
};
