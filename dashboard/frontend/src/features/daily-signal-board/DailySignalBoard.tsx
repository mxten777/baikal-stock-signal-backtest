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

function formatScoreChange(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

function formatReturn(value: number | null | undefined): string {
  if (value === null || value === undefined) return "미도래";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatReturnExcessCell(returnValue: number | null | undefined, excessValue: number | null | undefined): string {
  if (returnValue === null || returnValue === undefined) return "미도래";
  const excessText = excessValue === null || excessValue === undefined ? "" : ` (${formatReturn(excessValue)})`;
  return `${formatReturn(returnValue)}${excessText}`;
}

function formatScoreTransition(signalScore: number | null | undefined, currentScore: number | null | undefined): string {
  const from = formatNumber(signalScore);
  const to = currentScore === null || currentScore === undefined ? "—" : formatNumber(currentScore);
  return `${from} → ${to}`;
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

  const { status, new_signals, new_candidates, watch_list, candidate_tracking, dual_comparison, production_vs_dual, summary } = board;

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
        <MetricCard label="Data Status" metric={toMetric(summary.data_status)} format="text" />
        <MetricCard label="신규 매수후보" metric={toMetric(summary.new_candidate_count)} format="number" />
        <MetricCard label="WATCH" metric={toMetric(summary.watch_count)} format="number" />
        <MetricCard label="추적 Candidate" metric={toMetric(summary.tracked_candidate_count)} format="number" />
        <MetricCard label="DUAL 최신 기준일" metric={toMetric(summary.dual_latest_trade_date)} format="text" />
      </div>

      <div className="board-status-grid">
        <MetricCard label="Market Data Date" metric={toMetric(status.market_data_date)} format="text" />
        <MetricCard label="Investor Data Date" metric={toMetric(status.investor_data_date)} format="text" />
        <MetricCard label="Coverage" metric={toMetric(formatCoverage(status.coverage))} format="text" />
        <MetricCard label="Production Status" metric={toMetric(status.production_status)} format="text" />
      </div>

      <div className="board-section board-section-primary">
        <span className="board-section-title">① 오늘 신규 매수후보</span>
        {new_candidates.count === 0 ? (
          <EmptyState status="EMPTY" message={new_candidates.empty_message || "신규 매수후보 없음"} dataKind="operational" />
        ) : (
          <div className="board-table-wrapper">
            <table className="board-table">
              <thead>
                <tr>
                  <th>종목명</th>
                  <th className="cell-num">발생가격</th>
                  <th className="cell-num">Score</th>
                </tr>
              </thead>
              <tbody>
                {new_candidates.records.map((rec, idx) => (
                  <tr key={idx}>
                    <td>{rec.stock_name || "—"}</td>
                    <td className="cell-num">{formatNumber(rec.signal_price)}</td>
                    <td className="cell-num">{formatNumber(rec.signal_score)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="board-subnote">
          {new_signals.count === 0
            ? new_signals.empty_message || "신규 매수 후보 없음"
            : `전체 신규 Signal ${new_signals.count}건 (매수후보 ${new_candidates.count}건 포함)`}
        </div>
      </div>

      <div className="board-section board-section-primary">
        <span className="board-section-title">
          ② WATCH {watch_list.trade_date ? `(as of ${watch_list.trade_date})` : ""}
        </span>
        {watch_list.stale_note && (
          <div className="board-waiting-banner">{watch_list.stale_note} (DUAL 최신 기준일 {watch_list.trade_date || "—"})</div>
        )}
        {watch_list.records.length === 0 ? (
          <EmptyState status="EMPTY" message="WATCH 종목 없음" dataKind="operational" />
        ) : (
          <div className="board-table-wrapper">
            <table className="board-table">
              <thead>
                <tr>
                  <th>종목명</th>
                  <th className="cell-num">현재 Score</th>
                  <th className="cell-num">전 거래일 Score</th>
                  <th className="cell-num">Score 변화</th>
                </tr>
              </thead>
              <tbody>
                {watch_list.records.map((rec, idx) => (
                  <tr key={idx}>
                    <td>{rec.stock_name || "—"}</td>
                    <td className="cell-num">{formatNumber(rec.baseline_score)}</td>
                    <td className="cell-num">{formatNumber(rec.previous_score)}</td>
                    <td className="cell-num">{formatScoreChange(rec.score_change)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="board-section board-section-primary">
        <span className="board-section-title">
          ③ 기존 CANDIDATE 추적 {candidate_tracking.as_of ? `(as of ${candidate_tracking.as_of})` : ""}
        </span>
        {candidate_tracking.records.length === 0 ? (
          <EmptyState status="EMPTY" message="추적 중인 CANDIDATE 없음" dataKind="operational" />
        ) : (
          <div className="board-table-wrapper">
            <table className="board-table">
              <thead>
                <tr>
                  <th>종목명</th>
                  <th>발생일</th>
                  <th className="cell-num">발생가</th>
                  <th className="cell-num">현재가</th>
                  <th className="cell-num">Score (발생→현재)</th>
                  <th>상태</th>
                  <th className="cell-num">현재수익률</th>
                  <th className="cell-num">5D</th>
                  <th className="cell-num">10D</th>
                  <th className="cell-num">20D</th>
                </tr>
              </thead>
              <tbody>
                {candidate_tracking.records.map((rec, idx) => (
                  <tr key={idx}>
                    <td>{rec.stock_name || "—"}</td>
                    <td>{rec.signal_date || "—"}</td>
                    <td className="cell-num">{formatNumber(rec.signal_price)}</td>
                    <td className="cell-num">{rec.dual_match_found ? formatNumber(rec.current_evaluation_close) : "—"}</td>
                    <td className="cell-num">{formatScoreTransition(rec.signal_score, rec.dual_match_found ? rec.current_baseline_score : null)}</td>
                    <td>
                      {rec.tracking_status || "—"}
                      {rec.dual_match_found && rec.current_baseline_signal_type ? ` · ${rec.current_baseline_signal_type}` : ""}
                    </td>
                    <td className="cell-num">{rec.dual_match_found ? formatChange(rec.price_change_pct) : "—"}</td>
                    <td className="cell-num">{formatReturnExcessCell(rec.return_5d, rec.excess_5d)}</td>
                    <td className="cell-num">{formatReturnExcessCell(rec.return_10d, rec.excess_10d)}</td>
                    <td className="cell-num">{formatReturnExcessCell(rec.return_20d, rec.excess_20d)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="board-section board-section-primary">
        <span className="board-section-title">
          ④ Production vs DUAL {production_vs_dual.production_date ? `(Production ${production_vs_dual.production_date} · DUAL ${production_vs_dual.dual_date || "—"})` : ""}
        </span>
        {production_vs_dual.date_mismatch && (
          <div className="board-waiting-banner">
            {production_vs_dual.mismatch_note} — Production {production_vs_dual.production_date}, DUAL {production_vs_dual.dual_date}
          </div>
        )}
        <div className="board-status-grid">
          <MetricCard label="Production Status" metric={toMetric(production_vs_dual.production_status)} format="text" />
          <MetricCard label="DUAL Status" metric={toMetric(production_vs_dual.dual_status)} format="text" />
        </div>
        <div className="board-dual-grid">
          {(["BOTH_YES", "BASELINE_ONLY", "CHALLENGER_ONLY", "BOTH_NO", "NOT_EVALUABLE"] as const).map((group) => (
            <MetricCard key={group} label={group} metric={toMetric(dual_comparison.counts ? dual_comparison.counts[group] ?? 0 : null)} format="number" />
          ))}
        </div>
      </div>
    </div>
  );
};
