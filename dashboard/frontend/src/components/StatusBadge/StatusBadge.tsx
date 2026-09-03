import React from "react";
import "./StatusBadge.css";
import { ContractStatus } from "../../types/dashboard";

interface StatusBadgeProps {
  status?: ContractStatus | string | null;
  label?: string;
  variant?: "badge" | "pill" | "tag" | "dot";
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status = "UNAVAILABLE",
  label,
  variant = "badge",
}) => {
  const normStatus = (status || "UNAVAILABLE").toUpperCase();
  const displayLabel = label || normStatus;

  let colorClass = "status-unavailable";
  if (normStatus === "AVAILABLE") {
    colorClass = "status-available";
  } else if (normStatus === "MISSING") {
    colorClass = "status-missing";
  } else if (normStatus === "STALE") {
    colorClass = "status-stale";
  } else if (normStatus === "EMPTY") {
    colorClass = "status-empty";
  }

  return (
    <span className={`status-badge ${variant} ${colorClass}`}>
      {variant === "dot" && <span className="status-dot" />}
      {displayLabel}
    </span>
  );
};

interface DataKindBadgeProps {
  kind?: string | null;
}

export const DataKindBadge: React.FC<DataKindBadgeProps> = ({ kind }) => {
  const norm = (kind || "").toLowerCase();
  const isOperational = norm === "operational";
  const label = isOperational ? "OPERATIONAL SHADOW" : "HISTORICAL VALIDATION";
  const cls = isOperational ? "kind-operational" : "kind-historical";

  return <span className={`data-kind-badge ${cls}`}>{label}</span>;
};

