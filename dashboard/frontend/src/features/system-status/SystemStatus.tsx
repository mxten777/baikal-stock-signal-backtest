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
          <MetricCard label="Scan Base Date" metric={null} />
          <MetricCard label="Input Freshness" metric={null} />
          <MetricCard label="Ledger Status" metric={null} />
        </div>
      </div>
    );
  }

  const { pipeline_status, last_run, data_date, market_data_date, investor_data_date, input_data_freshness, ledger_status, freshness, baseline_commit, warnings } = system;
  const displayedFreshness = input_data_freshness || freshness;

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
            status={displayedFreshness?.status || "UNAVAILABLE"}
            label={`Input Freshness: ${displayedFreshness?.value || displayedFreshness?.status || "UNAVAILABLE"}`}
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
          label="Scan Base Date"
          metric={data_date}
          sublabel="Ledger as_of date"
          format="text"
        />
        <MetricCard
          label="Market Data Date"
          metric={market_data_date || null}
          sublabel="Local price source"
          format="text"
        />
        <MetricCard
          label="Investor Data Date"
          metric={investor_data_date || null}
          sublabel="Local flow source"
          format="text"
        />
        <MetricCard
          label="Input Freshness"
          metric={displayedFreshness}
          sublabel="Market and flow source age"
          format="text"
        />
        <MetricCard
          label="Ledger Status"
          metric={ledger_status || null}
          sublabel="Operational record store"
          format="text"
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
