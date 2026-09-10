import { useEffect, useState } from "react";
import { dashboardApi } from "../../api/dashboardApi";
import { DualShadowLatest, DualShadowPerformance, DualShadowRuns, DualShadowStatus } from "../../types/dualShadow";
import "./DualShadowMonitor.css";

const GROUPS = ["BOTH_YES", "BASELINE_ONLY", "CHALLENGER_ONLY", "BOTH_NO", "NOT_EVALUABLE"];
const HORIZONS = ["5D", "10D", "20D"];

const display = (value: string | number | null | undefined) => value ?? "N/A";
const formatNumber = (value: number | null | undefined, suffix = "") => value === null || value === undefined ? "N/A" : `${Number(value).toFixed(2)}${suffix}`;

export function DualShadowMonitor() {
  const [status, setStatus] = useState<DualShadowStatus | null>(null);
  const [latest, setLatest] = useState<DualShadowLatest | null>(null);
  const [performance, setPerformance] = useState<DualShadowPerformance | null>(null);
  const [runs, setRuns] = useState<DualShadowRuns | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    setLoading(true);
    Promise.all([
      dashboardApi.getDualShadowStatus(),
      dashboardApi.getDualShadowLatest(),
      dashboardApi.getDualShadowPerformance(),
      dashboardApi.getDualShadowRuns(),
    ])
      .then(([nextStatus, nextLatest, nextPerformance, nextRuns]) => {
        setStatus(nextStatus);
        setLatest(nextLatest);
        setPerformance(nextPerformance);
        setRuns(nextRuns);
        setError(null);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "DUAL Shadow API unavailable"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { refresh(); }, []);

  if (loading) return <section className="dual-shadow-page"><p className="dual-state">Loading DUAL Shadow Monitor...</p></section>;

  return (
    <section className="dual-shadow-page">
      <div className="dual-hero">
        <div>
          <span className="dual-badge">DUAL SHADOW</span>
          <h2>DUAL Shadow Monitor</h2>
          <p>Baseline v0.1 vs Challenger v0.2</p>
          <p>Baseline = current reference model. Challenger = experimental model. Dashboard output is not an order or automated trading signal.</p>
        </div>
        <div className="dual-badges">
          <span className="dual-badge readonly">READ-ONLY</span>
          <span className="dual-badge">SHADOW</span>
          <button className="dual-badge" onClick={refresh}>Refresh</button>
        </div>
      </div>
      {error && <div className="dual-state dual-error"><strong>DUAL Shadow API error</strong><br />{error}</div>}
      <CurrentStatus status={status} />
      <TodaysComparison latest={latest} />
      <Performance performance={performance} />
      <EvidenceMaturity latest={latest} />
      <RecentRuns runs={runs} />
    </section>
  );
}

function CurrentStatus({ status }: { status: DualShadowStatus | null }) {
  return <div className="panel">
    <PanelHeading title="Current Status" subtitle="Read-only DUAL daily pipeline and evidence state" status={status?.pipeline_status || "UNKNOWN"} />
    <div className="dual-grid">
      <Fact label="Latest Trade Date" value={status?.latest_trade_date} />
      <Fact label="Last DUAL Run" value={status?.last_dual_run} />
      <Fact label="Pipeline Status" value={status?.pipeline_status || "UNKNOWN"} />
      <Fact label="Baseline Engine Version" value={status?.baseline_engine_version} />
      <Fact label="Challenger Engine Version" value={status?.challenger_engine_version} />
      <Fact label="Ledger Row Count" value={status?.ledger_row_count} />
      <Fact label="Forward Return Evidence Count" value={status?.forward_return_evidence_count} />
      <Fact label="Performance Status" value={status?.performance_status || "UNKNOWN"} />
    </div>
  </div>;
}

function TodaysComparison({ latest }: { latest: DualShadowLatest | null }) {
  const records = latest?.records || [];
  return <div className="panel">
    <PanelHeading title="Today's Comparison" subtitle={`Latest trade date: ${display(latest?.trade_date)}`} status={latest?.status || "UNKNOWN"} />
    <div className="dual-grid">
      <Fact label="Total Stocks" value={latest?.total_stocks} />
      {GROUPS.map((group) => <Fact key={group} label={group} value={latest?.counts?.[group] ?? 0} />)}
    </div>
    {records.length === 0 ? <p className="dual-state">No DUAL comparison ledger rows available.</p> : <div className="dual-table-wrap"><table className="dual-table"><thead><tr><th>Stock</th><th>Code</th><th>Baseline Score</th><th>Baseline Signal</th><th>Challenger Score</th><th>Challenger Signal</th><th>Comparison Group</th><th>Penalties V/P/R/T</th></tr></thead><tbody>{records.map((record) => <tr key={`${record.stock_code}-${record.comparison_group}`}><td><strong>{display(record.stock_name)}</strong></td><td>{display(record.stock_code)}</td><td>{formatNumber(record.baseline_score)}</td><td>{display(record.baseline_signal)}</td><td>{formatNumber(record.challenger_score)}</td><td>{display(record.challenger_signal)}</td><td><span className={`group-chip group-${String(record.comparison_group || "unknown").toLowerCase()}`}>{display(record.comparison_group)}</span></td><td>{display(record.challenger_volume_penalty)} / {display(record.challenger_pre_return_penalty)} / {display(record.challenger_rsi_penalty)} / {display(record.challenger_total_penalty)}</td></tr>)}</tbody></table></div>}
  </div>;
}

function Performance({ performance }: { performance: DualShadowPerformance | null }) {
  const noEvidence = performance?.status === "NO_AVAILABLE_EVIDENCE";
  return <div className="panel">
    <PanelHeading title="Performance" subtitle="5D / 10D / 20D Baseline vs Challenger evidence" status={performance?.status || "UNKNOWN"} />
    {noEvidence && <p className="dual-state">NO_AVAILABLE_EVIDENCE</p>}
    <div className="dual-table-wrap"><table className="dual-table"><thead><tr><th>Horizon</th><th>Engine</th><th>Signal Count</th><th>Avg Return</th><th>Median Return</th><th>Win Rate</th><th>Best</th><th>Worst</th><th>Delta Avg / Median / Win / Count</th></tr></thead><tbody>{HORIZONS.map((horizon) => {
      const row = performance?.horizons?.[horizon];
      return ["baseline", "challenger"].map((engine) => <tr key={`${horizon}-${engine}`}><td><strong>{horizon}</strong></td><td>{engine === "baseline" ? "Baseline" : "Challenger"}</td><td>{display(row?.[engine as "baseline" | "challenger"]?.signal_count)}</td><td>{formatNumber(row?.[engine as "baseline" | "challenger"]?.avg_return, "%")}</td><td>{formatNumber(row?.[engine as "baseline" | "challenger"]?.median_return, "%")}</td><td>{formatNumber(row?.[engine as "baseline" | "challenger"]?.win_rate, "%")}</td><td>{formatNumber(row?.[engine as "baseline" | "challenger"]?.best_return, "%")}</td><td>{formatNumber(row?.[engine as "baseline" | "challenger"]?.worst_return, "%")}</td><td>{engine === "baseline" ? "N/A" : `${formatNumber(row?.delta?.avg_return_delta, "%")} / ${formatNumber(row?.delta?.median_return_delta, "%")} / ${formatNumber(row?.delta?.win_rate_delta, "%")} / ${display(row?.delta?.signal_count_delta)}`}</td></tr>);
    })}</tbody></table></div>
  </div>;
}

function EvidenceMaturity({ latest }: { latest: DualShadowLatest | null }) {
  const maturity = latest?.evidence_maturity || {};
  const totalPending = HORIZONS.reduce((sum, horizon) => sum + Number(maturity[horizon]?.pending || 0), 0);
  return <div className="panel">
    <PanelHeading title="Evidence Maturity" subtitle={`${totalPending} pending forward-return observations`} status={latest?.status || "UNKNOWN"} />
    <div className="dual-grid">{HORIZONS.map((horizon) => <div className="dual-maturity-row" key={horizon}><small>{horizon}</small><strong>Available {maturity[horizon]?.available ?? 0}</strong><span className="dual-muted">Pending {maturity[horizon]?.pending ?? 0}</span></div>)}</div>
  </div>;
}

function RecentRuns({ runs }: { runs: DualShadowRuns | null }) {
  const items = runs?.items || [];
  return <div className="panel">
    <PanelHeading title="Recent Runs" subtitle="DUAL registry only" status={runs?.status || "UNKNOWN"} />
    {items.length === 0 ? <p className="dual-state">No DUAL run registry records available.</p> : <div className="dual-table-wrap"><table className="dual-table"><thead><tr><th>Trade Date</th><th>Run Time</th><th>Status</th><th>Ledger Saved</th><th>Forward Return Saved</th><th>Performance Status</th><th>Error</th></tr></thead><tbody>{items.map((run, index) => <tr key={`${run.trade_date}-${run.finished_at}-${index}`}><td><strong>{display(run.trade_date)}</strong></td><td>{display(run.finished_at || run.started_at)}</td><td>{display(run.status)}</td><td>{display(run.ledger_saved)}</td><td>{display(run.forward_return_saved)}</td><td>{display(run.performance_status)}</td><td>{display(run.error_code || run.error_message)}</td></tr>)}</tbody></table></div>}
  </div>;
}

function PanelHeading({ title, subtitle, status }: { title: string; subtitle: string; status: string }) {
  return <div className="panel-header"><div className="panel-title-group"><span className="panel-title">{title}</span><span className="panel-subtitle">{subtitle}</span></div><div className="dual-panel-badges"><span className="dual-badge">{status}</span></div></div>;
}

function Fact({ label, value }: { label: string; value: string | number | null | undefined }) {
  return <div className="dual-fact"><small>{label}</small><strong>{display(value)}</strong></div>;
}