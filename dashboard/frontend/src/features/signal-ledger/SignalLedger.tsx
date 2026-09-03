import React from "react";
import "./SignalLedger.css";
import { SignalLedgerData } from "../../types/dashboard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";
import { EmptyState } from "../../components/EmptyState/EmptyState";

interface SignalLedgerProps {
  ledger?: SignalLedgerData | null;
}

export const SignalLedger: React.FC<SignalLedgerProps> = ({ ledger }) => {
  const status = ledger?.status || "UNAVAILABLE";
  const records = ledger?.records || [];
  const isAvailable =
    (status === "AVAILABLE" || status === "STALE") && records.length > 0;

  const formatReturn = (val: number | string | null | undefined) => {
    if (val === null || val === undefined || val === "") return "—";
    const num = typeof val === "number" ? val : parseFloat(String(val));
    if (isNaN(num)) return "—";
    return `${num > 0 ? "+" : ""}${num.toFixed(2)}%`;
  };

  return (
    <div className="panel signal-ledger-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Shadow Signal Ledger</span>
          <span className="panel-subtitle">
            Operational Signal Execution Log & Multi-Horizon Tracking
          </span>
        </div>
        <div className="ledger-header-badges">
          <DataKindBadge kind="operational" />
          <StatusBadge status={status} />
        </div>
      </div>

      {!isAvailable ? (
        <EmptyState
          status={status}
          title="No Shadow Records Available"
          message="No Shadow records available. The operational shadow signal ledger has not been generated or contains 0 records."
          dataKind="operational"
          source={ledger?.source}
        />
      ) : (
        <div className="ledger-table-wrapper">
          <table className="ledger-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Stock</th>
                <th>Market</th>
                <th>Signal Score</th>
                <th>Decision</th>
                <th>Foreign Flow</th>
                <th>5D Return (Excess)</th>
                <th>10D Return (Excess)</th>
                <th>20D Return (Excess)</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {records.map((rec, idx) => {
                const decisionClass =
                  rec.decision === "CANDIDATE"
                    ? "decision-candidate"
                    : "decision-excluded";

                return (
                  <tr key={idx}>
                    <td className="cell-date">{rec.signal_date || "—"}</td>
                    <td className="cell-stock">
                      <span className="stock-name">{rec.stock_name}</span>
                      <span className="stock-code">{rec.stock_code}</span>
                    </td>
                    <td>
                      <span className="market-tag">{rec.market || "—"}</span>
                    </td>
                    <td className="cell-num">
                      {rec.signal_score !== undefined && rec.signal_score !== null
                        ? rec.signal_score
                        : "—"}
                    </td>
                    <td>
                      <span className={`decision-badge ${decisionClass}`}>
                        {rec.decision || "—"}
                      </span>
                      {rec.exclusion_reason && (
                        <span className="reason-hint" title={rec.exclusion_reason}>
                          {rec.exclusion_reason}
                        </span>
                      )}
                    </td>
                    <td>
                      <span className="flow-text">{rec.foreign_status || "—"}</span>
                    </td>
                    <td className="cell-num">
                      {formatReturn(rec.return_5d)}
                      {rec.excess_5d !== undefined && rec.excess_5d !== null && rec.excess_5d !== "" && (
                        <span className="excess-sub"> ({formatReturn(rec.excess_5d)})</span>
                      )}
                    </td>
                    <td className="cell-num">
                      {formatReturn(rec.return_10d)}
                      {rec.excess_10d !== undefined && rec.excess_10d !== null && rec.excess_10d !== "" && (
                        <span className="excess-sub"> ({formatReturn(rec.excess_10d)})</span>
                      )}
                    </td>
                    <td className="cell-num">
                      {formatReturn(rec.return_20d)}
                      {rec.excess_20d !== undefined && rec.excess_20d !== null && rec.excess_20d !== "" && (
                        <span className="excess-sub"> ({formatReturn(rec.excess_20d)})</span>
                      )}
                    </td>
                    <td>
                      <span className="ledger-status-pill">
                        {rec.status || "PENDING"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
