import React from "react";
import "./PerformanceOverview.css";
import { PerformanceData } from "../../types/dashboard";
import { MetricCard } from "../../components/MetricCard/MetricCard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";

interface PerformanceOverviewProps {
  performance?: PerformanceData | null;
}

export const PerformanceOverview: React.FC<PerformanceOverviewProps> = ({
  performance,
}) => {
  const p5 = performance?.["5d"];
  const p10 = performance?.["10d"];
  const p20 = performance?.["20d"];

  const hasHistorical =
    p5?.candidate_excess_return?.status === "AVAILABLE" ||
    p10?.candidate_excess_return?.status === "AVAILABLE" ||
    p20?.candidate_excess_return?.status === "AVAILABLE";

  return (
    <div className="panel performance-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Strategy Performance</span>
          <span className="panel-subtitle">
            Excess Return & Win Rate across Evaluation Horizons
          </span>
        </div>
        <div className="performance-header-meta">
          <DataKindBadge kind="historical_validation" />
          <StatusBadge status={hasHistorical ? "AVAILABLE" : "UNAVAILABLE"} />
        </div>
      </div>

      <div className="performance-notice-banner">
        <span className="notice-icon">ℹ</span>
        <span>
          <strong>Operational Shadow:</strong> No operational Shadow data yet. All
          metrics below reflect audited <em>Historical Validation</em> baseline results.
        </span>
      </div>

      <div className="performance-grid">
        <div className="performance-horizon-section">
          <div className="section-header">5-Day Horizon (5D)</div>
          <div className="metrics-column">
            <MetricCard
              label="Candidate Avg Return"
              metric={p5?.candidate_return}
              unit="%"
              decimals={2}
            />
            <MetricCard
              label="Candidate Avg Excess"
              metric={p5?.candidate_excess_return}
              unit="%"
              decimals={2}
            />
            <MetricCard
              label="Win Rate"
              metric={p5?.candidate_win_rate}
              format="percent"
              decimals={1}
            />
            <MetricCard
              label="Candidate vs Excluded"
              metric={p5?.candidate_vs_excluded}
              unit="%"
              decimals={2}
            />
          </div>
        </div>

        <div className="performance-horizon-section">
          <div className="section-header">10-Day Horizon (10D)</div>
          <div className="metrics-column">
            <MetricCard
              label="Candidate Avg Return"
              metric={p10?.candidate_return}
              unit="%"
              decimals={2}
            />
            <MetricCard
              label="Candidate Avg Excess"
              metric={p10?.candidate_excess_return}
              unit="%"
              decimals={2}
            />
            <MetricCard
              label="Win Rate"
              metric={p10?.candidate_win_rate}
              format="percent"
              decimals={1}
            />
            <MetricCard
              label="Candidate vs Excluded"
              metric={p10?.candidate_vs_excluded}
              unit="%"
              decimals={2}
            />
          </div>
        </div>

        <div className="performance-horizon-section">
          <div className="section-header">20-Day Horizon (20D)</div>
          <div className="metrics-column">
            <MetricCard
              label="Candidate Avg Return"
              metric={p20?.candidate_return}
              unit="%"
              decimals={2}
            />
            <MetricCard
              label="Candidate Avg Excess"
              metric={p20?.candidate_excess_return}
              unit="%"
              decimals={2}
            />
            <MetricCard
              label="Win Rate"
              metric={p20?.candidate_win_rate}
              format="percent"
              decimals={1}
            />
            <MetricCard
              label="Candidate vs Excluded"
              metric={p20?.candidate_vs_excluded}
              unit="%"
              decimals={2}
            />
          </div>
        </div>
      </div>
    </div>
  );
};
