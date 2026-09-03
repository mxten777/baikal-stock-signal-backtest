import React from "react";
import "./MetricCard.css";
import { Metric } from "../../types/dashboard";
import { StatusBadge } from "../StatusBadge/StatusBadge";
import { sanitizeWarning } from "../../utils/sanitize";

interface MetricCardProps {
  label: string;
  metric?: Metric<any> | null;
  unit?: string;
  format?: "number" | "percent" | "text";
  decimals?: number;
  sublabel?: string;
}

export const MetricCard: React.FC<MetricCardProps> = ({
  label,
  metric,
  unit = "",
  format = "number",
  decimals = 2,
  sublabel,
}) => {
  if (!metric) {
    return (
      <div className="metric-card">
        <div className="metric-header">
          <span className="metric-label">{label}</span>
          <StatusBadge status="UNAVAILABLE" variant="tag" />
        </div>
        <div className="metric-value-container">
          <span className="metric-value-muted">—</span>
        </div>
      </div>
    );
  }

  const { value, status, data_kind, warnings } = metric;
  const isAvailable = status === "AVAILABLE" && value !== null && value !== undefined;

  let formattedValue: string = "—";
  if (isAvailable) {
    if (typeof value === "number") {
      if (format === "percent") {
        formattedValue = `${value.toFixed(decimals)}%`;
      } else {
        formattedValue = `${value.toLocaleString()}${unit}`;
      }
    } else {
      formattedValue = String(value);
    }
  }

  const isOperational = data_kind === "operational";

  return (
    <div className="metric-card">
      <div className="metric-header">
        <span className="metric-label">{label}</span>
        <div className="metric-header-tags">
          {data_kind && (
            <span className={`metric-kind-tag ${isOperational ? "kind-op" : "kind-hist"}`}>
              {isOperational ? "OP" : "HIST"}
            </span>
          )}
          <StatusBadge status={status} variant="tag" />
        </div>
      </div>

      <div className="metric-value-container">
        {isAvailable ? (
          <span className="metric-value">{formattedValue}</span>
        ) : (
          <span className="metric-value-muted">—</span>
        )}
      </div>

      {sublabel && (
        <div className="metric-footer">
          <span className="metric-sublabel">{sublabel}</span>
        </div>
      )}

      {warnings && warnings.length > 0 && (
        <div className="metric-warnings">
          {warnings.map((w, idx) => {
            const sanitized = sanitizeWarning(w);
            return (
              <span key={idx} className="metric-warning-item" title={sanitized}>
                {sanitized}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
};
