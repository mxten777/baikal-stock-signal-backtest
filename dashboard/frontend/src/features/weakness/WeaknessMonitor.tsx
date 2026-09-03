import React from "react";
import "./WeaknessMonitor.css";
import { WeaknessData } from "../../types/dashboard";
import { MetricCard } from "../../components/MetricCard/MetricCard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";

interface WeaknessMonitorProps {
  weakness?: WeaknessData | null;
}

export const WeaknessMonitor: React.FC<WeaknessMonitorProps> = ({ weakness }) => {
  return (
    <div className="panel weakness-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Weakness Segment Monitor</span>
          <span className="panel-subtitle">
            Market Segment Vulnerability Analysis
          </span>
        </div>
        <div className="weakness-header-meta">
          <DataKindBadge kind="historical_validation" />
          <StatusBadge status="UNAVAILABLE" />
        </div>
      </div>

      <div className="weakness-grid">
        <MetricCard
          label="HIGH Segment"
          metric={weakness?.HIGH}
          sublabel="Mapping not available"
        />
        <MetricCard
          label="KOSDAQ Segment"
          metric={weakness?.KOSDAQ}
          sublabel="Normalization pending"
        />
        <MetricCard
          label="HIGH × KOSDAQ Cross"
          metric={weakness?.HIGH_x_KOSDAQ}
          sublabel="Cross-interaction unmapped"
        />
      </div>

      <div className="weakness-notice">
        <span>
          <strong>Audit Note:</strong> Weakness segments are evaluated via historical validation matrices and do not execute live calculations in the frontend.
        </span>
      </div>
    </div>
  );
};
