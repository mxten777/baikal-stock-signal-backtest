import React from "react";
import "./OpportunityCostMonitor.css";
import { OpportunityCostData } from "../../types/dashboard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";
import { EmptyState } from "../../components/EmptyState/EmptyState";

interface OpportunityCostMonitorProps {
  opportunityCost?: OpportunityCostData | null;
}

export const OpportunityCostMonitor: React.FC<OpportunityCostMonitorProps> = ({
  opportunityCost,
}) => {
  const filtered = opportunityCost?.filtered_opportunity_cost;
  const filterSummary = opportunityCost?.filter_summary;

  const filteredStatus = filtered?.status || "UNAVAILABLE";
  const summaryStatus = filterSummary?.status || "UNAVAILABLE";

  const filteredRows = filtered?.rows || [];
  const summaryRows = filterSummary?.rows || [];

  return (
    <div className="panel opportunity-cost-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Opportunity Cost & Exclusion Analysis</span>
          <span className="panel-subtitle">
            Excluded Signals Impact vs Missed Opportunities
          </span>
        </div>
        <div className="opp-header-badges">
          <DataKindBadge kind="historical_validation" />
          <StatusBadge status={filteredStatus} />
        </div>
      </div>

      <div className="opp-grid">
        {/* Filtered Opportunity Cost Subpanel */}
        <div className="opp-subpanel">
          <div className="opp-subpanel-header">
            <span className="opp-subpanel-title">Filtered Group Opportunity Cost</span>
            <StatusBadge status={filteredStatus} />
          </div>

          {filteredStatus !== "AVAILABLE" || filteredRows.length === 0 ? (
            <EmptyState
              status={filteredStatus}
              title="No Filtered Opportunity Data"
              message="No filtered opportunity cost records found in allowlisted historical validation sources."
              source={filtered?.source}
              dataKind={filtered?.data_kind}
            />
          ) : (
            <div className="opp-table-container">
              <table className="opp-table">
                <thead>
                  <tr>
                    {Object.keys(filteredRows[0]).map((col) => (
                      <th key={col}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.slice(0, 8).map((row, idx) => (
                    <tr key={idx}>
                      {Object.keys(filteredRows[0]).map((col) => {
                        const val = (row as Record<string, unknown>)[col];
                        return (
                          <td key={col}>
                            {val !== null && val !== undefined ? String(val) : "—"}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Filter Summary Subpanel */}
        <div className="opp-subpanel">
          <div className="opp-subpanel-header">
            <span className="opp-subpanel-title">Filter Summary by Class</span>
            <StatusBadge status={summaryStatus} />
          </div>

          {summaryStatus !== "AVAILABLE" || summaryRows.length === 0 ? (
            <EmptyState
              status={summaryStatus}
              title="No Filter Summary Data"
              message="Filter class summary records are not available."
              source={filterSummary?.source}
              dataKind={filterSummary?.data_kind}
            />
          ) : (
            <div className="opp-table-container">
              <table className="opp-table">
                <thead>
                  <tr>
                    {Object.keys(summaryRows[0]).map((col) => (
                      <th key={col}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {summaryRows.slice(0, 8).map((row, idx) => (
                    <tr key={idx}>
                      {Object.keys(summaryRows[0]).map((col) => {
                        const val = (row as Record<string, unknown>)[col];
                        return (
                          <td key={col}>
                            {val !== null && val !== undefined ? String(val) : "—"}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
