export type ExpandedSurfaceStatus = "READY" | "MISSING" | "EMPTY" | "STALE" | "MALFORMED";

export interface ExpandedRunSummary {
  status: ExpandedSurfaceStatus;
  source: string;
  run_id: string | null;
  source_date: string | null;
  run_status: string | null;
  universe: number | null;
  attempted: number | null;
  ready: number | null;
  failure: number | null;
  signals: number | null;
  candidate: number | null;
  excluded: number | null;
  no_signal: number | null;
  started_at: string | null;
  finished_at: string | null;
  runtime_seconds: number | null;
}

export interface ExpandedCandidateRecord {
  stock_name: string;
  ticker: string;
  market: string;
  signal_date: string;
  entry_price: number | null;
  signal_score: number | null;
  foreign_status: string;
}

export interface ExpandedNewCandidates {
  status: ExpandedSurfaceStatus;
  source: string;
  source_date: string | null;
  count: number;
  records: ExpandedCandidateRecord[];
}

export interface ExpandedPerformanceRecord {
  stock_name: string;
  ticker: string;
  signal_date: string;
  tracking_status: string;
  return_5d: number | null;
  excess_5d: number | null;
  return_10d: number | null;
  excess_10d: number | null;
  return_20d: number | null;
  excess_20d: number | null;
}

export interface ExpandedStatusSummary {
  OPEN: number;
  "5D": number;
  "10D": number;
  "20D": number;
  COMPLETE: number;
}

export interface ExpandedPerformance {
  status: ExpandedSurfaceStatus;
  source: string;
  count: number;
  records: ExpandedPerformanceRecord[];
  empty_message: string | null;
  status_summary: ExpandedStatusSummary | null;
}

export interface ExpandedSignalBoardResponse {
  mode: "EXPANDED_SHADOW";
  read_only: true;
  status: ExpandedSurfaceStatus;
  run_summary: ExpandedRunSummary;
  new_candidates: ExpandedNewCandidates;
  status_summary: ExpandedStatusSummary | null;
  performance: ExpandedPerformance;
  warnings: string[];
}