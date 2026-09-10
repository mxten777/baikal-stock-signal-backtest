export interface DualShadowStatus {
  mode: "DUAL_SHADOW" | string;
  read_only: boolean;
  baseline_label: string;
  challenger_label: string;
  latest_trade_date: string | null;
  last_dual_run: string | null;
  pipeline_status: string;
  baseline_engine_version: string;
  challenger_engine_version: string;
  ledger_status: string;
  ledger_row_count: number;
  forward_return_status: string;
  forward_return_evidence_count: number;
  performance_status: string;
  warnings: string[];
}

export interface DualShadowRecord {
  stock_name: string | null;
  stock_code: string | null;
  baseline_score: number | null;
  baseline_signal: string | null;
  challenger_score: number | null;
  challenger_signal: string | null;
  comparison_group: string | null;
  evaluation_status: string | null;
  challenger_volume_penalty: number | null;
  challenger_pre_return_penalty: number | null;
  challenger_rsi_penalty: number | null;
  challenger_total_penalty: number | null;
}

export interface EvidenceMaturityRow {
  available: number;
  pending: number;
  status: string;
}

export interface DualShadowLatest {
  status: string;
  source: string;
  trade_date: string | null;
  total_stocks: number;
  counts: Record<string, number>;
  records: DualShadowRecord[];
  evidence_maturity: Record<string, EvidenceMaturityRow>;
  warnings: string[];
}

export interface DualEnginePerformance {
  signal_count: number;
  avg_return: number | null;
  median_return: number | null;
  win_rate: number | null;
  best_return: number | null;
  worst_return: number | null;
}

export interface DualPerformanceDelta {
  avg_return_delta: number | null;
  median_return_delta: number | null;
  win_rate_delta: number | null;
  signal_count_delta: number;
}

export interface DualHorizonPerformance {
  baseline: DualEnginePerformance;
  challenger: DualEnginePerformance;
  delta: DualPerformanceDelta;
}

export interface DualShadowPerformance {
  status: string;
  source: string;
  generated_at?: string | null;
  win_definition?: string | null;
  engine_versions: Record<string, string>;
  total_evidence_rows: number | null;
  horizons: Record<string, DualHorizonPerformance>;
  warnings: string[];
}

export interface DualShadowRun {
  trade_date: string | null;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  ledger_saved: number;
  forward_return_saved: number;
  performance_status: string | null;
  error_code?: string | null;
  error_message?: string | null;
}

export interface DualShadowRuns {
  status: string;
  source: string;
  items: DualShadowRun[];
  warnings: string[];
}