import React from "react";
import "./DailySignalBoard.css";
import { DailySignalBoardResponse, Metric } from "../../types/dashboard";
import { MetricCard } from "../../components/MetricCard/MetricCard";
import { StatusBadge, DataKindBadge } from "../../components/StatusBadge/StatusBadge";
import { EmptyState } from "../../components/EmptyState/EmptyState";

interface DailySignalBoardProps {
  board?: DailySignalBoardResponse | null;
}

function toMetric(value: string | number | null | undefined): Metric<string | number | null> {
  return {
    value: value ?? null,
    status: value === null || value === undefined ? "UNAVAILABLE" : "AVAILABLE",
    data_kind: "operational",
  };
}

function formatCoverage(coverage: DailySignalBoardResponse["status"]["coverage"] | undefined): string {
  if (!coverage || coverage.status !== "AVAILABLE") return "—";
  const market = coverage.market ? `${coverage.market.found}/${coverage.market.expected}` : "—";
  const investor = coverage.investor ? `${coverage.investor.found}/${coverage.investor.expected}` : "—";
  return `M ${market} · I ${investor}`;
}

function formatNumber(value: number | null | undefined, unit = ""): string {
  if (value === null || value === undefined) return "—";
  return `${value.toLocaleString()}${unit}`;
}

function formatChange(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export const DailySignalBoard: React.FC<DailySignalBoardProps> = ({ board }) => {
  if (!board) {
    return (
      <div className="panel daily-signal-board-panel">
        <div className="panel-header">
          <div className="panel-title-group">
            <span className="panel-title">Daily Signal Board</span>
          </div>
          <StatusBadge status="UNAVAILABLE" />
        </div>
        <EmptyState status="UNAVAILABLE" title="Daily Signal Board Unavailable" message="Daily Signal Board data could not be loaded." dataKind="operational" />
      </div>
    );
  }

  const { status, new_signals, watch_list, candidate_tracking, dual_comparison } = board;

  return (
    <div className="panel daily-signal-board-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <span className="panel-title">Daily Signal Board</span>
          <span className="panel-subtitle">
            {status.is_today ? `분석 기준일 ${status.analysis_date}` : `최종 분석일 ${status.analysis_date || "—"} · 오늘 데이터 대기중`}
          </span>
        </div>
        <div className="daily-signal-board-header-badges">
          <DataKindBadge kind="operational" />
          <StatusBadge status={status.is_today ? "AVAILABLE" : "STALE"} label={status.is_today ? "TODAY" : "WAITING_FOR_TODAY"} />
        </div>
      </div>

      {!status.is_today && (
        <div className="board-waiting-banner">
          최종 분석일 {status.analysis_date || "—"} / 오늘 데이터 대기중
        </div>
      )}

      <div className="board-status-grid">
        <MetricCard label="Market Data Date" metric={toMetric(status.market_data_date)} format="text" />
        <MetricCard label="Investor Data Date" metric={toMetric(status.investor_data_date)} format="text" />
        <MetricCard label="Coverage" metric={toMetric(formatCoverage(status.coverage))} format="text" />
        <MetricCard label="Production Status" metric={toMetric(status.production_status)} format="text" />
      </div>

      <div className="board-section">
        <span className="board-section-title">오늘 신규 Signal</span>
        {new_signals.count === 0 ? (
          <EmptyState status="EMPTY" message={new_signals.empty_message || "신규 매수 후보 없음"} dataKind="operational" />
        ) : (
          <div className="board-table-wrapper">
            <table className="board-table">
              <thead>
                <tr>
                  <th>종목명</th>
                  <th>Score</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {new_signals.records.map((rec, idx) => (
                  <tr key={idx}>
                    <td>{rec.stock_name || "—"}</td>
                    <td className="cell-num">{formatNumber(rec.signal_score)}</td>
                    <td>
                      <span className={`decision-badge ${rec.decision === "CANDIDATE" ? "decision-candidate" : "decision-excluded"}`}>
                        {rec.decision || "—"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="board-section">
        <span className="board-section-title">
          오늘 WATCH 종목 {watch_list.trade_date ? `(as of ${watch_list.trade_date})` : ""}
        </span>
        {watch_list.records.length === 0 ? (
          <EmptyState status="EMPTY" message="WATCH 종목 없음" dataKind="operational" />
        ) : (
          <div className="board-table-wrapper">
            <table className="board-table">
              <thead>
                <tr>
                  <th>종목명</th>
                  <th>Evaluation Close</th>
                  <th>Baseline Score</th>
                </tr>
              </thead>
              <tbody>
                {watch_list.records.map((rec, idx) => (
                  <tr key={idx}>
                    <td>{rec.stock_name || "—"}</td>
                    <td className="cell-num">{formatNumber(rec.evaluation_close)}</td>
                    <td className="cell-num">{formatNumber(rec.baseline_score)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="board-section">
        <span className="board-section-title">
          기존 CANDIDATE 추적 {candidate_tracking.as_of ? `(as of ${candidate_tracking.as_of})` : ""}
        </span>
        {candidate_tracking.records.length === 0 ? (
          <EmptyState status="EMPTY" message="추적 중인 CANDIDATE 없음" dataKind="operational" />
        ) : (
          <div className="board-table-wrapper">
            <table className="board-table">
              <thead>
                <tr>
                  <th>종목명</th>
                  <th>Signal Date</th>
                  <th>Signal Price</th>
                  <th>Signal Score</th>
                  <th>Current Close</th>
                  <th>Current Score</th>
                  <th>Current Signal</th>
                  <th>가격 변화율</th>
                </tr>
              </thead>
              <tbody>
                {candidate_tracking.records.map((rec, idx) => (
                  <tr key={idx}>
                    <td>{rec.stock_name || "—"}</td>
                    <td>{rec.signal_date || "—"}</td>
                    <td className="cell-num">{formatNumber(rec.signal_price)}</td>
                    <td className="cell-num">{formatNumber(rec.signal_score)}</td>
                    <td className="cell-num">{rec.dual_match_found ? formatNumber(rec.current_evaluation_close) : "—"}</td>
                    <td className="cell-num">{rec.dual_match_found ? formatNumber(rec.current_baseline_score) : "—"}</td>
                    <td>{rec.dual_match_found ? rec.current_baseline_signal_type || "—" : "—"}</td>
                    <td className="cell-num">{rec.dual_match_found ? formatChange(rec.price_change_pct) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="board-section">
        <span className="board-section-title">
          DUAL 비교 {dual_comparison.trade_date ? `(as of ${dual_comparison.trade_date})` : ""}
        </span>
        <div className="board-dual-grid">
          {(["BOTH_YES", "BASELINE_ONLY", "CHALLENGER_ONLY", "BOTH_NO", "NOT_EVALUABLE"] as const).map((group) => (
            <MetricCard key={group} label={group} metric={toMetric(dual_comparison.counts ? dual_comparison.counts[group] ?? 0 : null)} format="number" />
          ))}
        </div>
      </div>
    </div>
  );
};
