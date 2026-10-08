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
  signal_type: string | null;
  foreign_status: string;
}

export interface ExpandedCompanyProfile {
  company_name: string | null;
  sector: string | null;
  main_business_products: string | null;
  one_line_description: string | null;
  market_cap: number | null;
  market_cap_date: string | null;
  profile_as_of: string | null;
  source: string | null;
}

export interface ExpandedDecisionEvidence {
  signal_reason: string | null;
  prev_score: number | null;
  current_score: number | null;
  delta_score: number | null;
  trend_score: number | null;
  volume_score: number | null;
  momentum_score: number | null;
  foreign_status: string | null;
  foreign_5d_ratio: number | null;
  decision: string | null;
  decision_reason: string | null;
  evidence_status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE";
}

export interface ExpandedSignalPerformance {
  tracking_status: string;
  return_5d: number | null;
  benchmark_5d: number | null;
  excess_5d: number | null;
  return_10d: number | null;
  benchmark_10d: number | null;
  excess_10d: number | null;
  return_20d: number | null;
  benchmark_20d: number | null;
  excess_20d: number | null;
}

export interface ExpandedSignalRecord {
  basDd: string;
  ticker: string;
  stock_name: string;
  market: string;
  signal_date: string;
  signal_price: number | null;
  raw_score: number | null;
  signal_score: number | null;
  signal_type: string | null;
  company_profile: ExpandedCompanyProfile | null;
  decision_evidence: ExpandedDecisionEvidence;
  performance: ExpandedSignalPerformance | null;
  easy_analysis?: ExpandedEasyAnalysis;
}

export interface ExpandedEasyAnalysis {
  summary: string;
  positives: string[];
  risks: string[];
  checks: string[];
  sources: string[];
  method: string;
  disclaimer: string;
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
  benchmark_5d: number | null;
  excess_5d: number | null;
  return_10d: number | null;
  benchmark_10d: number | null;
  excess_10d: number | null;
  return_20d: number | null;
  benchmark_20d: number | null;
  excess_20d: number | null;
}

export interface ExpandedStatusSummary {
  OPEN: number;
  "5D": number;
  "10D": number;
  "20D": number;
  COMPLETE: number;
}

export interface ForwardValidationBucket {
  candidate_count: number;
  matured: Record<"5D" | "10D" | "20D", number>;
  mean_excess: Record<"5D" | "10D" | "20D", number | null>;
}

export interface ForwardValidationSummary {
  status: "READY" | "EMPTY" | "ERROR";
  source?: string;
  sector_membership_source?: string;
  cutoff: string;
  candidate_count?: number;
  matured?: Record<"5D" | "10D" | "20D", number>;
  h1?: {
    groups: Record<"POSITIVE" | "NEUTRAL", ForwardValidationBucket>;
    not_compared_count: number;
  };
  h2?: ForwardValidationBucket;
  h3?: {
    sectors: Record<string, ForwardValidationBucket>;
    unclassified_count: number;
  };
  h4?: {
    method: "Spearman";
    pairs: number;
    rho: number | null;
    status: string;
  };
  error_code?: string;
  warnings: string[];
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
  validation: ForwardValidationSummary;
  warnings: string[];
  signal_records?: ExpandedSignalRecord[];
  signal_records_warnings?: string[];
}