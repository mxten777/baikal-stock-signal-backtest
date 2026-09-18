import { useEffect, useState } from "react";
import { dashboardApi } from "../../api/dashboardApi";
import { ExpandedSignalBoardResponse, ExpandedStatusSummary } from "../../types/expandedShadow";
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
  const performance = board.performance;

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
          <button type="button" onClick={refresh} aria-label="Refresh Expanded Signal Board">Refresh</button>
        </div>
      </header>

      {board.warnings.length > 0 && (
        <div className="expanded-state expanded-warning">
          {board.warnings.map((warning) => <div key={warning}>{warning}</div>)}
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
              <thead><tr><th>종목명</th><th>Ticker</th><th>Market</th><th>Signal Date</th><th>Entry Price</th><th>Score</th><th>Foreign</th></tr></thead>
              <tbody>{candidates.map((record) => (
                <tr key={`${record.signal_date}-${record.ticker}`}>
                  <td><strong>{display(record.stock_name)}</strong></td><td className="mono">{record.ticker}</td><td>{record.market}</td><td>{record.signal_date}</td>
                  <td className="number">{formatNumber(record.entry_price)}</td><td className="number">{formatNumber(record.signal_score)}</td><td>{record.foreign_status}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel expanded-section">
        <SectionHeading title="Status Summary" subtitle="Candidate performance lifecycle" status={performance.status} />
        {board.status_summary ? (
          <div className="expanded-lifecycle" aria-label="Performance status summary">
            {STATUS_ORDER.map((status) => <div key={status}><span>{status}</span><strong>{board.status_summary?.[status] ?? 0}</strong></div>)}
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
              <thead><tr><th>종목명</th><th>Ticker</th><th>Signal Date</th><th>Status</th><th>5D Return / Excess</th><th>10D Return / Excess</th><th>20D Return / Excess</th></tr></thead>
              <tbody>{performance.records.map((record) => (
                <tr key={`${record.signal_date}-${record.ticker}`}>
                  <td><strong>{record.stock_name}</strong></td><td className="mono">{record.ticker}</td><td>{record.signal_date}</td>
                  <td><span className={`tracking-chip tracking-${record.tracking_status.toLowerCase()}`}>{record.tracking_status}</span></td>
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