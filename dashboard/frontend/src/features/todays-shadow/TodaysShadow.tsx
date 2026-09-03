import React from "react";
import "./TodaysShadow.css";
import { TodaysShadowData } from "../../types/dashboard";
import { MetricCard } from "../../components/MetricCard/MetricCard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";

interface TodaysShadowProps {
  today?: TodaysShadowData | null;
}

export const TodaysShadow: React.FC<TodaysShadowProps> = ({ today }) => {
  const overallStatus = today?.new_signals?.status || "UNAVAILABLE";

  return (
    <div className="panel todays-shadow-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Today's Shadow Monitor</span>
          <span className="panel-subtitle">
            Operational Signal Classification Breakdown
          </span>
        </div>
        <div className="todays-shadow-header-badges">
          <DataKindBadge kind="operational" />
          <StatusBadge status={overallStatus} />
        </div>
      </div>

      <div className="today-grid">
        <MetricCard
          label="New Signals"
          metric={today?.new_signals}
          sublabel="Total evaluated signals"
        />
        <MetricCard
          label="Candidate"
          metric={today?.candidates}
          sublabel="Passed shadow filters"
        />
        <MetricCard
          label="Excluded"
          metric={today?.excluded}
          sublabel="Filtered by risk/flow criteria"
        />
        <MetricCard
          label="KOSDAQ"
          metric={today?.kosdaq}
          sublabel="KQ market count"
        />
        <MetricCard
          label="HIGH Classification"
          metric={today?.high}
          sublabel="Operational ledger contract"
        />
      </div>
    </div>
  );
};
