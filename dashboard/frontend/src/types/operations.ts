export interface OperationsStatus {
  target_trade_date: string | null;
  current_status: string;
  attempt: number | null;
  next_retry_at: string | null;
  last_attempt_at: string | null;
  completed_at: string | null;
  latest_market_date: string | null;
  latest_investor_date: string | null;
  integrity_status: string | null;
  pipeline_status: string | null;
  health_status: string | null;
  failed_phase: string | null;
  error_code: string | null;
  error_message: string | null;
  operator_action_required: boolean;
  operator_action_code: string | null;
  last_run_id: string | null;
  last_daily_status: string | null;
  last_successful_run_at: string | null;
  last_successful_trade_date: string | null;
  timezone: string | null;
  manual_run: ManualRunCapability;
}

export interface ManualRunCapability {
  allowed: boolean;
  reason_code: string;
  reason: string;
  requires_confirmation: boolean;
}

export interface ManualRunResult {
  accepted: boolean;
  executed: boolean;
  run_id: string;
  daily_status: string;
  overall_status: string;
  started_at: string;
  completed_at: string;
  error_code: string | null;
  error_message: string | null;
  warning: string | null;
  scheduler_reconciliation_required: boolean;
}

export interface OperationsSummary {
  trade_date: string;
  final_status: string | null;
  attempts: number;
  first_attempt_at: string | null;
  last_attempt_at: string | null;
  last_run_id: string | null;
  error_code: string | null;
  operator_action_required: boolean;
}

export interface OperationsAttempt {
  slot: number | null;
  attempt: number | null;
  orchestration_status: string | null;
  daily_status: string | null;
  started_at: string | null;
  finished_at: string | null;
  next_retry_at: string | null;
  error_code: string | null;
  error_message: string | null;
  failed_phase: string | null;
  operator_action_required: boolean;
  operator_action_code: string | null;
  last_run_id: string | null;
}

export interface OperationsException {
  trade_date: string;
  status: string | null;
  severity: string;
  failed_phase: string | null;
  error_code: string | null;
  summary: string;
  details: string | null;
  retryable: boolean;
  operator_action_required: boolean;
  operator_action_code: string | null;
  operator_guidance: string | null;
  manual_rerun_allowed: boolean | null;
  affected_components: string[];
  data_context: Record<string, string | null>;
  run_context: Record<string, string | number | null>;
}