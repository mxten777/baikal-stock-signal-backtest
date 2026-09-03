import React from "react";
import "./EmptyState.css";
import { StatusBadge } from "../StatusBadge/StatusBadge";
import { ContractStatus } from "../../types/dashboard";

interface EmptyStateProps {
  title?: string;
  message: string;
  status?: ContractStatus | string;
  source?: string | null;
  dataKind?: string | null;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title,
  message,
  status,
  source,
  dataKind,
}) => {
  return (
    <div className="empty-state">
      <div className="empty-state-header">
        {status && <StatusBadge status={status} />}
        {title && <span className="empty-state-title">{title}</span>}
      </div>
      <p className="empty-state-message">{message}</p>
      {(source || dataKind) && (
        <div className="empty-state-meta">
          {dataKind && <span className="meta-tag">Kind: {dataKind}</span>}
          {source && <span className="meta-tag">Source: {source}</span>}
        </div>
      )}
    </div>
  );
};
