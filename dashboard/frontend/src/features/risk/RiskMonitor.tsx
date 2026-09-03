import React from "react";
import "./RiskMonitor.css";
import { RiskData } from "../../types/dashboard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";
import { EmptyState } from "../../components/EmptyState/EmptyState";

interface RiskMonitorProps {
  risk?: RiskData | null;
}

export const RiskMonitor: React.FC<RiskMonitorProps> = ({ risk }) => {
  const operational = risk?.operational;
  const historical = risk?.historical_validation;

  const operationalStatus = operational?.status || "UNAVAILABLE";
  const historicalStatus = historical?.status || "UNAVAILABLE";
  const historicalRows = historical?.rows || [];

  return (
    <div className="panel risk-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Risk & Drawdown Monitor</span>
          <span className="panel-subtitle">
            Operational Shadow vs Historical Risk Measures
          </span>
        </div>
        <div className="risk-header-badges">
          <StatusBadge status={historicalStatus} label={`Validation: ${historicalStatus}`} />
        </div>
      </div>

      <div className="risk-sections-grid">
        {/* Operational Shadow Risk Section */}
        <div className="risk-subpanel">
          <div className="risk-subpanel-header">
            <div className="subpanel-title-group">
              <span className="subpanel-title">Operational Shadow Risk</span>
              <DataKindBadge kind="operational" />
            </div>
            <StatusBadge status={operationalStatus} />
          </div>
          <EmptyState
            status={operationalStatus}
            title="Operational Risk Pending"
            message="Operational Shadow ledger has not accumulated mature risk outcomes. Live MDD & Tail Risk will compute as signals mature."
            dataKind="operational"
            source={operational?.source}
          />
        </div>

        {/* Historical Validation Risk Section */}
        <div className="risk-subpanel">
          <div className="risk-subpanel-header">
            <div className="subpanel-title-group">
              <span className="subpanel-title">Historical Validation Risk</span>
              <DataKindBadge kind="historical_validation" />
            </div>
            <StatusBadge status={historicalStatus} />
          </div>

          {historicalStatus !== "AVAILABLE" || historicalRows.length === 0 ? (
            <EmptyState
              status={historicalStatus}
              title="Historical Risk Unavailable"
              message="No audited historical validation risk metrics found in the current allowlisted source."
              dataKind="historical_validation"
              source={historical?.source}
            />
          ) : (
            <div className="risk-table-container">
              <table className="risk-table">
                <thead>
                  <tr>
                    {Object.keys(historicalRows[0]).map((col) => (
                      <th key={col}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {historicalRows.map((row, idx) => (
                    <tr key={idx}>
                      {Object.keys(historicalRows[0]).map((col) => {
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
