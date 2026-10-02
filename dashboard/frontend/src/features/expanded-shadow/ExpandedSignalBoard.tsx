import { useEffect, useState } from "react";
import { dashboardApi } from "../../api/dashboardApi";
import { ExpandedSignalBoardResponse, ExpandedSignalRecord, ExpandedStatusSummary } from "../../types/expandedShadow";
import "./ExpandedSignalBoard.css";

const STATUS_ORDER: Array<keyof ExpandedStatusSummary> = ["OPEN", "5D", "10D", "20D", "COMPLETE"];

function display(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString();
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? "N/A" : value.toFixed(1);
}

function formatRatio(value: number | null | undefined): string {
  return value === null || value === undefined ? "N/A" : value.toFixed(4);
}

function formatMarketCap(value: number | null | undefined): string {
  return value === null || value === undefined ? "N/A" : value.toLocaleString();
}

function formatSignalReason(record: ExpandedSignalRecord): string {
  const reason = record.decision_evidence.signal_reason;
  if (!reason) return "N/A";
  const match = reason.match(/^Score crossed threshold: ([\d.]+) -> ([\d.]+) \(threshold ([\d.]+)\)$/);
  if (!match) return reason;
  return `점수가 기준 ${Number(match[3]).toString()}를 상향 돌파: ${record.decision_evidence.prev_score?.toFixed(1) ?? "N/A"} → ${record.decision_evidence.current_score?.toFixed(1) ?? "N/A"}`;
}

function formatDecisionReason(reason: string | null): string {
  if (!reason) return "N/A";
  if (/^Foreign status .+ is not NEGATIVE; existing rule classifies as CANDIDATE\.$/.test(reason)) {
    return "외국인 수급이 NEGATIVE가 아니므로 CANDIDATE로 분류";
  }
  if (reason === "FOREIGN_NEGATIVE") return "외국인 수급이 NEGATIVE여서 EXCLUDED로 분류";
  return reason;
}

function formatEvidenceStatus(status: string): string {
  const labels: Record<string, string> = {
    AVAILABLE: "근거 확인 완료",
    PARTIAL: "일부 근거 확인",
    UNAVAILABLE: "근거 확인 불가",
  };
  return labels[status] ?? status;
}

function formatTrackingStatus(status: string): string {
  const labels: Record<string, string> = {
    OPEN: "성과 측정 중",
    "5D": "5거래일 성과 확인",
    "10D": "10거래일 성과 확인",
    "20D": "20거래일 성과 확인",
    COMPLETE: "성과 추적 완료",
  };
  return labels[status] ?? status;
}

function PerformanceValue({ value, excess }: { value: number | null; excess: number | null }) {
  return (
    <span className="expanded-performance-value">
      <strong>{formatPercent(value)}</strong>
      <small>{excess === null ? "excess —" : `excess ${formatPercent(excess)}`}</small>
    </span>
  );
}

export function ExpandedSignalBoard() {
  const [board, setBoard] = useState<ExpandedSignalBoardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reportMenuOpen, setReportMenuOpen] = useState(false);
  const [reportDownloading, setReportDownloading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  const refresh = () => {
    setLoading(true);
    dashboardApi.getExpandedSignalBoard()
      .then((payload) => {
        setBoard(payload);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Expanded Shadow API unavailable");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { refresh(); }, []);

  if (loading) {
    return <section className="expanded-page"><p className="expanded-state">Loading Expanded Signal Board...</p></section>;
  }

  if (!board) {
    return <section className="expanded-page"><p className="expanded-state expanded-error">Expanded Shadow API error: {error}</p></section>;
  }

  const run = board.run_summary;
  const candidates = board.new_candidates.records;
  const signalRecords = board.signal_records ?? [];
  const excludedSignals = signalRecords.filter((record) => record.decision_evidence.decision === "EXCLUDED");
  const performance = board.performance;
  const sourceDate = run.source_date;

  const downloadReport = async (format: "docx" | "pdf") => {
    if (reportDownloading || !sourceDate) return;
    setReportMenuOpen(false);
    setReportDownloading(true);
    setReportError(null);
    try {
      await dashboardApi.downloadDailyReport(sourceDate, format);
    } catch (reason: unknown) {
      setReportError(reason instanceof Error ? reason.message : "Daily Report download failed");
    } finally {
      setReportDownloading(false);
    }
  };

  return (
    <section className="expanded-page">
      <header className="expanded-header">
        <div>
          <span className="expanded-kicker">EXPANDED SHADOW · READ ONLY</span>
          <h2>Expanded Signal Board</h2>
          <p>574종목 Expanded Shadow의 최신 signal과 candidate 성과 추적</p>
        </div>
        <div className="expanded-header-actions">
          <span className={`expanded-status status-${board.status.toLowerCase()}`}>{board.status}</span>
          <div className="expanded-report-menu">
            <button
              type="button"
              onClick={() => setReportMenuOpen((open) => !open)}
              disabled={reportDownloading || !sourceDate}
              aria-haspopup="true"
              aria-expanded={reportMenuOpen}
            >
              {reportDownloading ? "Downloading..." : "\uD83D\uDCC4 Daily Report \u25BE"}
            </button>
            {reportMenuOpen && (
              <div className="expanded-report-options" role="menu">
                <button type="button" role="menuitem" onClick={() => downloadReport("docx")} disabled={reportDownloading}>Word (.docx)</button>
                <button type="button" role="menuitem" onClick={() => downloadReport("pdf")} disabled={reportDownloading}>PDF (.pdf)</button>
              </div>
            )}
          </div>
          <button type="button" onClick={refresh} aria-label="Refresh Expanded Signal Board">Refresh</button>
        </div>
      </header>

      {reportError && (
        <div className="expanded-state expanded-error" role="alert">{reportError}</div>
      )}

      {board.warnings.length > 0 && (
        <div className="expanded-state expanded-warning">
          {board.warnings.map((warning) => <div key={warning}>{warning}</div>)}
        </div>
      )}
      {(board.signal_records_warnings ?? []).length > 0 && (
        <div className="expanded-state expanded-warning" role="status">
          {(board.signal_records_warnings ?? []).map((warning) => <div key={warning}>{warning}</div>)}
        </div>
      )}

      <section className="panel expanded-section">
        <SectionHeading title="Run Summary" subtitle={`Source date ${display(run.source_date)}`} status={run.status} />
        <div className="expanded-summary-grid">
          <Fact label="Source Date" value={run.source_date} />
          <Fact label="Universe" value={run.universe} />
          <Fact label="Attempted" value={run.attempted} />
          <Fact label="READY" value={run.ready} />
          <Fact label="Failure" value={run.failure} />
          <Fact label="Signals" value={run.signals} />
          <Fact label="CANDIDATE" value={run.candidate} />
          <Fact label="EXCLUDED" value={run.excluded} />
          <Fact label="NO_SIGNAL" value={run.no_signal} />
        </div>
      </section>

      <section className="panel expanded-section">
        <SectionHeading title="New Candidates" subtitle={`Latest completed cohort · ${display(board.new_candidates.source_date)}`} status={board.new_candidates.status} />
        {candidates.length === 0 ? (
          <p className="expanded-state">최신 Expanded run의 신규 CANDIDATE가 없습니다.</p>
        ) : (
          <div className="expanded-table-wrap">
            <table className="expanded-table">
              <thead><tr><th>종목명</th><th>Ticker</th><th>Decision</th><th>Market</th><th>Signal Date</th><th>Entry Price</th><th>Score</th><th>외국인 수급</th><th>Details</th></tr></thead>
              <tbody>{candidates.map((record) => (
                <tr key={`${record.signal_date}-${record.ticker}`}>
                  <td><strong>{display(findSignalRecord(signalRecords, record.ticker, record.signal_date)?.company_profile?.company_name ?? record.stock_name)}</strong></td>
                  <td className="mono">{record.ticker}</td><td><DecisionBadge decision="CANDIDATE" /></td><td>{record.market}</td><td>{record.signal_date}</td>
                  <td className="number">{formatNumber(record.entry_price)}</td><td className="number">{formatScore(record.signal_score)}</td><td>{record.foreign_status}</td>
                  <td><SignalDetails record={findSignalRecord(signalRecords, record.ticker, record.signal_date)} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {excludedSignals.length > 0 && (
          <div className="expanded-excluded-list">
            <h3>EXCLUDED signals</h3>
            <div className="expanded-table-wrap">
              <table className="expanded-table">
                <thead><tr><th>종목명</th><th>Ticker</th><th>Decision</th><th>Signal Date</th><th>Score</th><th>외국인 수급</th><th>Details</th></tr></thead>
                <tbody>{excludedSignals.map((record) => (
                  <tr key={`${record.signal_date}-${record.ticker}`}>
                    <td><strong>{display(record.company_profile?.company_name ?? record.stock_name)}</strong></td>
                    <td className="mono">{record.ticker}</td><td><DecisionBadge decision="EXCLUDED" /></td><td>{record.signal_date}</td>
                    <td className="number">{formatScore(record.signal_score)}</td><td>{display(record.decision_evidence.foreign_status)}</td>
                    <td><SignalDetails record={record} /></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      <section className="panel expanded-section">
        <SectionHeading title="Status Summary" subtitle="Candidate performance lifecycle" status={performance.status} />
        {board.status_summary ? (
          <div className="expanded-lifecycle" aria-label="Performance status summary">
            {STATUS_ORDER.map((status) => <div key={status}><span>{formatTrackingStatus(status)}</span><strong>{board.status_summary?.[status] ?? 0}</strong></div>)}
          </div>
        ) : <p className="expanded-state">{performance.empty_message || "성과 추적 상태가 없습니다."}</p>}
      </section>

      <section className="panel expanded-section">
        <SectionHeading title="Performance Tracking" subtitle={`${performance.count} candidate records`} status={performance.status} />
        {performance.records.length === 0 ? (
          <p className="expanded-state">{performance.empty_message || "성과 추적 데이터가 없습니다."}</p>
        ) : (
          <div className="expanded-table-wrap">
            <table className="expanded-table performance-table">
              <thead><tr><th>종목명</th><th>Ticker</th><th>Signal Date</th><th>성과 상태</th><th>5D Return / Excess</th><th>10D Return / Excess</th><th>20D Return / Excess</th></tr></thead>
              <tbody>{performance.records.map((record) => (
                <tr key={`${record.signal_date}-${record.ticker}`}>
                  <td><strong>{record.stock_name}</strong></td><td className="mono">{record.ticker}</td><td>{record.signal_date}</td>
                  <td><span className={`tracking-chip tracking-${record.tracking_status.toLowerCase()}`}>{formatTrackingStatus(record.tracking_status)}</span></td>
                  <td><PerformanceValue value={record.return_5d} excess={record.excess_5d} /></td>
                  <td><PerformanceValue value={record.return_10d} excess={record.excess_10d} /></td>
                  <td><PerformanceValue value={record.return_20d} excess={record.excess_20d} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}

function SectionHeading({ title, subtitle, status }: { title: string; subtitle: string; status: string }) {
  return <div className="panel-header"><div className="panel-title-group"><span className="panel-title">{title}</span><span className="panel-subtitle">{subtitle}</span></div><span className={`expanded-status status-${status.toLowerCase()}`}>{status}</span></div>;
}

function Fact({ label, value }: { label: string; value: string | number | null }) {
  return <div className="expanded-fact"><span>{label}</span><strong>{display(value)}</strong></div>;
}

function findSignalRecord(records: ExpandedSignalRecord[], ticker: string, signalDate: string): ExpandedSignalRecord | undefined {
  return records.find((record) => record.ticker === ticker && record.signal_date === signalDate);
}

function DecisionBadge({ decision }: { decision: string }) {
  return <span className={`expanded-decision decision-${decision.toLowerCase()}`}>{decision}</span>;
}

function SignalDetails({ record }: { record: ExpandedSignalRecord | undefined }) {
  if (!record) return <span className="expanded-detail-unavailable">N/A</span>;
  const profile = record.company_profile;
  const evidence = record.decision_evidence;
  const performance = record.performance;

  return (
    <details className="expanded-details">
      <summary>상세</summary>
      <div className="expanded-detail-content">
        <section>
          <h4>회사정보</h4>
          <dl className="expanded-detail-grid">
            <div className="wide"><dt>한줄 소개</dt><dd>{display(profile?.one_line_description)}</dd></div>
            <div><dt>업종</dt><dd>{display(profile?.sector)}</dd></div>
            <div><dt>주요 사업/제품</dt><dd>{display(profile?.main_business_products)}</dd></div>
            <div><dt>시가총액</dt><dd>{formatMarketCap(profile?.market_cap)}</dd></div>
            <div><dt>정보 기준일</dt><dd>{display(profile?.profile_as_of)}</dd></div>
          </dl>
        </section>
        <section>
          <h4>Signal 근거</h4>
          <dl className="expanded-detail-grid">
            <div><dt>점수</dt><dd>{formatScore(evidence.prev_score)} → {formatScore(evidence.current_score)}</dd></div>
            <div><dt>Signal 발생 이유</dt><dd>{formatSignalReason(record)}</dd></div>
            <div><dt>추세 / 거래량 / 모멘텀</dt><dd>{formatScore(evidence.trend_score)} / {formatScore(evidence.volume_score)} / {formatScore(evidence.momentum_score)}</dd></div>
            <div><dt>외국인 수급</dt><dd>{display(evidence.foreign_status)} · {formatRatio(evidence.foreign_5d_ratio)}</dd></div>
            <div><dt>판정 이유</dt><dd>{formatDecisionReason(evidence.decision_reason)}</dd></div>
            <div><dt>근거 상태</dt><dd><span className={`expanded-evidence-status evidence-${evidence.evidence_status.toLowerCase()}`}>{formatEvidenceStatus(evidence.evidence_status)}</span></dd></div>
          </dl>
        </section>
        <section>
          <h4>성과</h4>
          {performance ? (
            <div className="expanded-detail-performance">
              {([5, 10, 20] as const).map((horizon) => (
                <div key={horizon}>
                  <strong>{horizon}D</strong>
                  <span>Return {formatPercent(performance[`return_${horizon}d`])}</span>
                  <span>Excess {formatPercent(performance[`excess_${horizon}d`])}</span>
                </div>
              ))}
                  <small>{formatTrackingStatus(performance.tracking_status)}</small>
            </div>
          ) : (
            <p className="expanded-detail-note">{evidence.decision === "EXCLUDED" ? "성과 추적 대상 아님" : "성과 데이터 없음"}</p>
          )}
        </section>
      </div>
    </details>
  );
}