import React from "react";
import "./Header.css";
import { StatusBadge } from "../StatusBadge/StatusBadge";
import { ContractStatus } from "../../types/dashboard";

interface HeaderProps {
  mode?: string;
  isReadOnly?: boolean;
  baselineCommit?: string;
  dataStatus?: ContractStatus | string;
}

export const Header: React.FC<HeaderProps> = ({
  mode = "SHADOW",
  isReadOnly = true,
  baselineCommit = "38e56c5",
  dataStatus = "MISSING",
}) => {
  const commitStr = baselineCommit
    ? `BASELINE ${baselineCommit.substring(0, 7).toUpperCase()}`
    : "BASELINE —";
  const dataStatusLabel = `DATA ${String(dataStatus).toUpperCase()}`;

  return (
    <header className="control-tower-header">
      <div className="header-brand-section">
        <div className="header-brand">
          <h1 className="header-title">BAIKAL Stock Signal</h1>
          <span className="header-divider">/</span>
          <p className="header-subtitle">Shadow Validation Control Tower</p>
        </div>
        <div className="header-badges">
          <span className="badge badge-shadow">{mode.toUpperCase()}</span>
          {isReadOnly && <span className="badge badge-readonly">READ ONLY</span>}
          <span className="badge badge-commit">{commitStr}</span>
          <StatusBadge status={dataStatus} label={dataStatusLabel} />
        </div>
      </div>
      <div className="header-meta-section">
        <div className="header-meta">
          <span className="meta-item">
            System Mode: <strong className="meta-val">SHADOW VALIDATION</strong>
          </span>
          <span className="meta-separator">|</span>
          <span className="meta-item">
            Trading / Execution: <strong className="meta-val meta-disabled">DISABLED</strong>
          </span>
        </div>
      </div>
    </header>
  );
};
