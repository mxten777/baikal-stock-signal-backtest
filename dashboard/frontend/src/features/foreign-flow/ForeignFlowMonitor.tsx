import React from "react";
import "./ForeignFlowMonitor.css";
import { DatasetPayload } from "../../types/dashboard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";
import { EmptyState } from "../../components/EmptyState/EmptyState";

interface ForeignFlowMonitorProps {
  foreignFlow?: DatasetPayload | null;
}

export const ForeignFlowMonitor: React.FC<ForeignFlowMonitorProps> = ({
  foreignFlow,
}) => {
  const status = foreignFlow?.status || "UNAVAILABLE";
  const rows = foreignFlow?.rows || [];
  const isAvailable = status === "AVAILABLE" && rows.length > 0;

  return (
    <div className="panel foreign-flow-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Foreign Flow Validation</span>
          <span className="panel-subtitle">
            Capital Flow Classification
          </span>
        </div>
        <div className="panel-header-badges">
          <DataKindBadge kind="historical_validation" />
          <StatusBadge status={status} />
        </div>
      </div>

      <div className="flow-classes-summary">
        <div className="flow-class-item flow-positive">
          <span className="flow-badge">POSITIVE</span>
          <span className="flow-desc">Accumulation (Candidate Core)</span>
        </div>
        <div className="flow-class-item flow-neutral">
          <span className="flow-badge">NEUTRAL</span>
          <span className="flow-desc">Balanced (Selective Candidate)</span>
        </div>
        <div className="flow-class-item flow-negative">
          <span className="flow-badge">NEGATIVE</span>
          <span className="flow-desc">Outflow (Excluded Filter)</span>
        </div>
      </div>

      {!isAvailable ? (
        <EmptyState
          status={status}
          title="No Flow Validation Records"
          message="Foreign flow historical validation data is unavailable or missing from the adapter source."
          source={foreignFlow?.source}
          dataKind={foreignFlow?.data_kind}
        />
      ) : (
        <div className="flow-table-container">
          <table className="flow-table">
            <thead>
              <tr>
                {Object.keys(rows[0]).map((col) => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 10).map((row, idx) => (
                <tr key={idx}>
                  {Object.keys(rows[0]).map((col) => {
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
          {rows.length > 10 && (
            <div className="table-footnote">
              Showing first 10 of {rows.length} records.
            </div>
          )}
        </div>
      )}
    </div>
  );
};
