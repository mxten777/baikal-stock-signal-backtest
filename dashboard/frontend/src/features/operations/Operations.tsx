import { useEffect, useState } from "react";
import { dashboardApi } from "../../api/dashboardApi";
import { ManualRunResult, OperationsAttempt, OperationsException, OperationsStatus, OperationsSummary } from "../../types/operations";
import "./Operations.css";

const statusLabel: Record<string, string> = {
  SUCCESS: "Success",
  SUCCESS_WITH_WARNING: "Success with warning",
  RETRY_PENDING: "Retry pending",
  BLOCKED: "Blocked",
  FAILED: "Failed",
  NON_TRADING_DAY: "Non-trading day",
  NO_DATA: "No scheduler run",
};

const display = (value: string | number | null | undefined) => value ?? "Not available";

export function Operations() {
  const [status, setStatus] = useState<OperationsStatus | null>(null);
  const [history, setHistory] = useState<OperationsSummary[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [attempts, setAttempts] = useState<OperationsAttempt[]>([]);
  const [exception, setException] = useState<OperationsException | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [manualRunning, setManualRunning] = useState(false);
  const [manualResult, setManualResult] = useState<ManualRunResult | null>(null);
  const [manualError, setManualError] = useState<string | null>(null);

  const refreshOperations = () => {
    setLoading(true);
    Promise.all([dashboardApi.getOperationsStatus(), dashboardApi.getOperationsHistory()])
      .then(([nextStatus, nextHistory]) => {
        setStatus(nextStatus);
        setHistory(nextHistory);
        setError(null);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Operations API unavailable"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { refreshOperations(); }, []);

  const runManualOperation = async () => {
    const capability = status?.manual_run ?? { allowed: false, reason_code: "MANUAL_RUN_NOT_ALLOWED", reason: "Manual execution is unavailable.", requires_confirmation: true };
    if (!capability.allowed || manualRunning) return;
    if (capability.requires_confirmation && !window.confirm("현재 Daily Operation을 수동으로 1회 실행합니다.\n기존 Integrity Gate와 Lock이 그대로 적용됩니다.\n계속하시겠습니까?")) return;
    setManualRunning(true);
    setManualResult(null);
    setManualError(null);
    try {
      const result = await dashboardApi.runManualDailyOperation();
      setManualResult(result);
      refreshOperations();
    } catch (reason: unknown) {
      setManualError(reason instanceof Error ? reason.message : "Manual operation failed");
      refreshOperations();
    } finally {
      setManualRunning(false);
    }
  };

  const selectDate = (tradeDate: string) => {
    setSelectedDate(tradeDate);
    Promise.all([dashboardApi.getOperationsDetail(tradeDate), dashboardApi.getOperationsException(tradeDate)])
      .then(([nextAttempts, nextException]) => { setAttempts(nextAttempts); setException(nextException); })
      .catch(() => { setAttempts([]); setException(null); });
  };

  if (loading) return <section className="operations-page"><p className="operations-state">Loading daily operations...</p></section>;
  if (error) return <section className="operations-page"><div className="operations-error"><strong>Operations unavailable</strong><span>{error}</span></div></section>;

  const currentStatus = status?.current_status || "NO_DATA";
  const manualCapability = status?.manual_run ?? { allowed: false, reason_code: "MANUAL_RUN_NOT_ALLOWED", reason: "Manual execution is unavailable.", requires_confirmation: true };
  return (
    <section className="operations-page">
      <div className="operations-heading">
        <div><p className="eyebrow">Control plane</p><h2>Daily Operations</h2><p className="operations-muted">Read-only scheduler, pipeline, and recovery visibility.</p></div>
        <div className={`operations-status status-${currentStatus.toLowerCase()}`}><span aria-hidden="true">●</span><div><small>Current status</small><strong>{statusLabel[currentStatus] || currentStatus}</strong></div></div>
      </div>
      <div className="manual-operations-panel">
        <div><p className="eyebrow">Manual Operations</p><h3>Run Daily Operation</h3><p className="operations-muted">{manualRunning ? "Running Daily Operation..." : manualCapability.reason}</p></div>
        <button className="manual-run-button" disabled={!manualCapability.allowed || manualRunning} onClick={runManualOperation}>{manualRunning ? "Running..." : "Run Daily Operation"}</button>
        {manualResult && <div className={`manual-result result-${manualResult.overall_status.toLowerCase()}`}><strong>{statusLabel[manualResult.overall_status] || manualResult.overall_status}</strong><span>Run ID: {manualResult.run_id}</span>{manualResult.warning && <span>{manualResult.warning}</span>}</div>}
        {manualError && <div className="manual-result result-failed"><strong>Manual operation failed</strong><span>{manualError}</span></div>}
      </div>
      <div className="operations-facts">
        <Fact label="Target trade date" value={display(status?.target_trade_date)} />
        <Fact label="Latest market" value={display(status?.latest_market_date)} />
        <Fact label="Latest investor" value={display(status?.latest_investor_date)} />
        <Fact label="Last attempt" value={display(status?.last_attempt_at)} />
        <Fact label="Next retry" value={display(status?.next_retry_at)} />
      </div>
      {status?.operator_action_required && <div className="operator-panel"><strong>Operator action required</strong><span>Code: {display(status.operator_action_code)}</span><span>{display(status.error_message || status.failed_phase)}</span></div>}
      {(status?.error_code || status?.error_message || status?.failed_phase) && <div className="operations-alert"><strong>{currentStatus === "SUCCESS_WITH_WARNING" ? "Warning" : "Run issue"}</strong><span>{display(status.error_code)} · {display(status.error_message || status.failed_phase)}</span></div>}
      <div className="operations-card-grid">
        <Metric title="Data" rows={[["Market", status?.latest_market_date], ["Investor", status?.latest_investor_date]]} />
        <Metric title="Integrity" rows={[["Gate", status?.integrity_status]]} />
        <Metric title="Pipeline" rows={[["Daily status", status?.pipeline_status || status?.last_daily_status]]} />
        <Metric title="Health" rows={[["Overall", status?.health_status]]} />
      </div>
      <div className="operations-section"><div className="section-heading"><h3>Recent history</h3><span>{history.length ? `${history.length} trade dates` : "No operational history yet."}</span></div>
        {history.length > 0 && <div className="history-list">{history.map((item) => <button className="history-row" key={item.trade_date} onClick={() => selectDate(item.trade_date)}><span className="history-date">{item.trade_date}</span><span className={`status-chip status-${(item.final_status || "unknown").toLowerCase()}`}>{statusLabel[item.final_status || ""] || display(item.final_status)}</span><span>{item.attempts} attempt{item.attempts === 1 ? "" : "s"}</span><span>{display(item.last_attempt_at)}</span><span>{display(item.error_code)}</span><span>{item.operator_action_required ? "Action required" : "No action"}</span></button>)}</div>}
      </div>
      {selectedDate && <div className="operations-section"><div className="section-heading"><h3>{selectedDate} operational detail</h3><button className="close-detail" onClick={() => { setSelectedDate(null); setException(null); }}>Close</button></div>
        {exception ? <ExceptionDetail exception={exception} /> : <p className="operations-state">No exception recorded for this trade date.</p>}
        <h3 className="detail-subheading">Attempt timeline</h3>
        {attempts.length ? <div className="timeline">{attempts.map((attempt, index) => <div className="timeline-item" key={`${attempt.attempt}-${index}`}><span className="timeline-dot" /><div><strong>Attempt {display(attempt.attempt)}</strong><span>{display(attempt.started_at)} · {display(attempt.orchestration_status)}</span><small>{display(attempt.error_code || attempt.daily_status || attempt.finished_at)}</small></div></div>)}</div> : <p className="operations-state">No attempt detail recorded for this date.</p>}
      </div>}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string | number }) { return <div><small>{label}</small><strong>{value}</strong></div>; }
function Metric({ title, rows }: { title: string; rows: [string, string | null | undefined][] }) { return <div className="metric-card"><h3>{title}</h3>{rows.map(([label, value]) => <div className="metric-row" key={label}><span>{label}</span><strong>{display(value)}</strong></div>)}</div>; }

function ExceptionDetail({ exception }: { exception: OperationsException }) {
  return <div className={`exception-detail severity-${exception.severity.toLowerCase()}`}>
    <div className="exception-overview"><div><small>Severity</small><strong>{exception.severity}</strong></div><div><small>Status</small><strong>{display(exception.status)}</strong></div><div><small>Summary</small><strong>{exception.summary}</strong></div></div>
    <div className="exception-grid">
      <DetailValue label="Failed phase" value={exception.failed_phase} />
      <DetailValue label="Error code" value={exception.error_code} />
      <DetailValue label="Operator action" value={exception.operator_action_code || (exception.operator_action_required ? "Required" : "Not required")} />
      <DetailValue label="Retryable" value={exception.retryable ? "Yes" : "No"} />
      <DetailValue label="Manual rerun" value={exception.manual_rerun_allowed === null ? "Unknown" : exception.manual_rerun_allowed ? "Allowed" : "Prohibited"} />
      <DetailValue label="Affected components" value={exception.affected_components.length ? exception.affected_components.join(", ") : "Not available"} />
    </div>
    {exception.details && <p className="exception-details">{exception.details}</p>}
    {exception.operator_guidance && <div className="guidance"><small>Operator guidance</small><strong>{exception.operator_guidance}</strong></div>}
    <div className="context-grid"><Context title="Data context" values={exception.data_context} /><Context title="Run context" values={exception.run_context} /></div>
  </div>;
}

function DetailValue({ label, value }: { label: string; value: string | null }) { return <div><small>{label}</small><strong>{display(value)}</strong></div>; }
function Context({ title, values }: { title: string; values: Record<string, string | number | null> }) { return <div className="context-block"><h4>{title}</h4>{Object.entries(values).map(([label, value]) => <div className="context-row" key={label}><span>{label.split("_").join(" ")}</span><strong>{display(value)}</strong></div>)}</div>; }