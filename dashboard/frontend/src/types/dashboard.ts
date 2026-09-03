export type ContractStatus =
  | "AVAILABLE"
  | "EMPTY"
  | "MISSING"
  | "STALE"
  | "UNAVAILABLE";

export type DataKind = "operational" | "historical_validation" | "metadata";

export interface Metric<T = number | string | null> {
  value: T;
  sample_size?: number | null;
  status: ContractStatus;
  source?: string | null;
  as_of?: string | null;
  data_kind?: DataKind | string | null;
  warnings?: string[];
}

export interface SystemStatusData {
  mode: string;
  read_only: boolean;
  baseline_commit: string;
  pipeline_status: Metric<string | null>;
  last_run: Metric<string | null>;
  data_date: Metric<string | null>;
  freshness: Metric<string | null>;
  warnings: string[];
}

export interface TodaysShadowData {
  new_signals: Metric<number | null>;
  candidates: Metric<number | null>;
  excluded: Metric<number | null>;
  kosdaq: Metric<number | null>;
  high: Metric<number | null>;
}

export interface HorizonMaturity {
  matured: Metric<number | null>;
  pending: Metric<number | null>;
}

export interface MaturityData {
  "5d": HorizonMaturity;
  "10d": HorizonMaturity;
  "20d": HorizonMaturity;
}

export interface HorizonPerformance {
  candidate_return: Metric<number | null>;
  candidate_excess_return: Metric<number | null>;
  candidate_win_rate: Metric<number | null>;
  candidate_vs_excluded: Metric<number | null>;
}

export interface PerformanceData {
  "5d": HorizonPerformance;
  "10d": HorizonPerformance;
  "20d": HorizonPerformance;
}

export interface DatasetPayload<T = Record<string, unknown>> {
  status: ContractStatus;
  source?: string | null;
  data_kind: string;
  sample_size: number;
  rows?: T[];
  warnings?: string[];
}

export interface WeaknessData {
  HIGH: Metric<number | null>;
  KOSDAQ: Metric<number | null>;
  HIGH_x_KOSDAQ: Metric<number | null>;
}

export interface RiskData {
  operational: {
    status: ContractStatus;
    source?: string | null;
    data_kind: string;
    sample_size: number;
    warnings?: string[];
  };
  historical_validation: DatasetPayload;
}

export interface OpportunityCostData {
  filtered_opportunity_cost: DatasetPayload;
  filter_summary: DatasetPayload;
}

export interface SignalRecord {
  stock_code: string;
  stock_name: string;
  market: string;
  signal_date: string;
  signal_price?: number | string | null;
  signal_score?: number | string | null;
  foreign_status?: string;
  decision: "CANDIDATE" | "EXCLUDED" | string;
  exclusion_reason?: string | null;
  created_at?: string;
  status?: string;
  return_5d?: number | string | null;
  return_10d?: number | string | null;
  return_20d?: number | string | null;
  benchmark_return_5d?: number | string | null;
  benchmark_return_10d?: number | string | null;
  benchmark_return_20d?: number | string | null;
  excess_5d?: number | string | null;
  excess_10d?: number | string | null;
  excess_20d?: number | string | null;
}

export interface SignalLedgerData {
  status: ContractStatus;
  source?: string | null;
  data_kind: string;
  as_of?: string | null;
  sample_size: number;
  records: SignalRecord[];
  warnings?: string[];
}

export interface DashboardOverviewResponse {
  system: SystemStatusData;
  today: TodaysShadowData;
  maturity: MaturityData;
  performance: PerformanceData;
  foreign_flow: DatasetPayload;
  weakness: WeaknessData;
  risk: RiskData;
  opportunity_cost: OpportunityCostData;
  signal_ledger: SignalLedgerData;
}

export interface DashboardHealthResponse {
  mode: string;
  read_only: boolean;
  baseline_commit: string;
  ledger_status: ContractStatus;
  allowed_sources: string[];
  write_endpoints: string[];
}
