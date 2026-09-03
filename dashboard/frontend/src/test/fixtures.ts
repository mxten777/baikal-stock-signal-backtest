import { DashboardOverviewResponse } from "../types/dashboard";

/**
 * Fixture representing the real STEP 2 adapter state:
 * - Ledger is MISSING
 * - System pipeline status / last_run are UNAVAILABLE
 * - Historical validation is available from allowlisted output files
 */
export const defaultMissingOverviewFixture: DashboardOverviewResponse = {
  system: {
    mode: "SHADOW",
    read_only: true,
    baseline_commit: "e4d38f7",
    pipeline_status: {
      value: null,
      status: "UNAVAILABLE",
      source: null,
      data_kind: "operational",
      warnings: ["pipeline status is not persisted in STEP 2"],
    },
    last_run: {
      value: null,
      status: "UNAVAILABLE",
      source: null,
      data_kind: "operational",
      warnings: ["pipeline last_run is not persisted in STEP 2"],
    },
    data_date: {
      value: null,
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      data_kind: "operational",
    },
    market_data_date: {
      value: null,
      status: "MISSING",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    investor_data_date: {
      value: null,
      status: "MISSING",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    input_data_freshness: {
      value: "MISSING",
      status: "MISSING",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    ledger_status: {
      value: "MISSING",
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      data_kind: "operational",
    },
    freshness: {
      value: "MISSING",
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      data_kind: "operational",
    },
    warnings: ["shadow ledger file does not exist"],
  },
  today: {
    new_signals: {
      value: null,
      sample_size: 0,
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      as_of: null,
      data_kind: "operational",
      warnings: ["shadow ledger file does not exist"],
    },
    candidates: {
      value: null,
      sample_size: 0,
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      as_of: null,
      data_kind: "operational",
      warnings: ["shadow ledger file does not exist"],
    },
    excluded: {
      value: null,
      sample_size: 0,
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      as_of: null,
      data_kind: "operational",
      warnings: ["shadow ledger file does not exist"],
    },
    kosdaq: {
      value: null,
      sample_size: 0,
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      as_of: null,
      data_kind: "operational",
      warnings: ["shadow ledger file does not exist"],
    },
    high: {
      value: null,
      sample_size: null,
      status: "UNAVAILABLE",
      source: "output/shadow_signal_records.csv",
      as_of: null,
      data_kind: "operational",
      warnings: ["HIGH classification is not present in the operational ledger contract"],
    },
  },
  maturity: {
    "5d": {
      matured: {
        value: null,
        sample_size: 0,
        status: "MISSING",
        source: "output/shadow_signal_records.csv",
        data_kind: "operational",
      },
      pending: {
        value: null,
        sample_size: 0,
        status: "MISSING",
        source: "output/shadow_signal_records.csv",
        data_kind: "operational",
      },
    },
    "10d": {
      matured: {
        value: null,
        sample_size: 0,
        status: "MISSING",
        source: "output/shadow_signal_records.csv",
        data_kind: "operational",
      },
      pending: {
        value: null,
        sample_size: 0,
        status: "MISSING",
        source: "output/shadow_signal_records.csv",
        data_kind: "operational",
      },
    },
    "20d": {
      matured: {
        value: null,
        sample_size: 0,
        status: "MISSING",
        source: "output/shadow_signal_records.csv",
        data_kind: "operational",
      },
      pending: {
        value: null,
        sample_size: 0,
        status: "MISSING",
        source: "output/shadow_signal_records.csv",
        data_kind: "operational",
      },
    },
  },
  performance: {
    "5d": {
      candidate_return: {
        value: 1.15,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_excess_return: {
        value: 0.85,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_win_rate: {
        value: 58.2,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_vs_excluded: {
        value: 1.45,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
    },
    "10d": {
      candidate_return: {
        value: 2.10,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_excess_return: {
        value: 1.40,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_win_rate: {
        value: 61.5,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_vs_excluded: {
        value: 2.20,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
    },
    "20d": {
      candidate_return: {
        value: 3.50,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_excess_return: {
        value: 2.80,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_win_rate: {
        value: 64.0,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
      candidate_vs_excluded: {
        value: 3.60,
        sample_size: 450,
        status: "AVAILABLE",
        source: "output/v02_step9_final_comparison.csv",
        data_kind: "historical_validation",
      },
    },
  },
  foreign_flow: {
    status: "AVAILABLE",
    source: "output/v02_step3_foreign_score_performance.csv",
    data_kind: "historical_validation",
    sample_size: 3,
    rows: [
      { Factor: "FOREIGN", Group: "POSITIVE", N: 120, "Avg Excess 20D": "3.85" },
      { Factor: "FOREIGN", Group: "NEUTRAL", N: 85, "Avg Excess 20D": "1.10" },
      { Factor: "FOREIGN", Group: "NEGATIVE", N: 45, "Avg Excess 20D": "-2.40" },
    ],
    warnings: [],
  },
  weakness: {
    HIGH: {
      value: null,
      status: "UNAVAILABLE",
      source: "output/v02_step3_foreign_score_performance.csv",
      data_kind: "historical_validation",
      warnings: ["HIGH weakness requires an explicit validation mapping in a later step"],
    },
    KOSDAQ: {
      value: null,
      status: "UNAVAILABLE",
      source: "output/v02_step7_filter_by_market.csv",
      data_kind: "historical_validation",
      warnings: ["KOSDAQ weakness is allowed but not normalized in STEP 2"],
    },
    HIGH_x_KOSDAQ: {
      value: null,
      status: "UNAVAILABLE",
      source: null,
      data_kind: "historical_validation",
      warnings: ["HIGH x KOSDAQ cross metric is unavailable in STEP 2"],
    },
  },
  risk: {
    operational: {
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      data_kind: "operational",
      sample_size: 0,
      warnings: ["shadow ledger file does not exist"],
    },
    historical_validation: {
      status: "AVAILABLE",
      source: "output/v02_step9_risk_review.csv",
      data_kind: "historical_validation",
      sample_size: 2,
      rows: [
        { Strategy: "CANDIDATE", Metric: "Max Drawdown 20D", Value: "-8.5%" },
        { Strategy: "CANDIDATE", Metric: "Tail Risk <= -10%", Value: "2.1%" },
      ],
      warnings: [],
    },
  },
  opportunity_cost: {
    filtered_opportunity_cost: {
      status: "AVAILABLE",
      source: "output/v02_step8_filtered_opportunity_cost.csv",
      data_kind: "historical_validation",
      sample_size: 1,
      rows: [
        { "Filtered N": "37", "Avg Excess 20D": "-4.24%", "Missed Winners": "0" },
      ],
      warnings: [],
    },
    filter_summary: {
      status: "AVAILABLE",
      source: "output/v02_step6_filter_opportunity_cost.csv",
      data_kind: "historical_validation",
      sample_size: 1,
      rows: [
        { "Filtered Class": "NEGATIVE", N: "37", "Win Rate": "24.3%" },
      ],
      warnings: [],
    },
  },
  signal_ledger: {
    status: "MISSING",
    source: "output/shadow_signal_records.csv",
    data_kind: "operational",
    as_of: null,
    sample_size: 0,
    records: [],
    warnings: ["shadow ledger file does not exist"],
  },
};

export const staleInputOverviewFixture: DashboardOverviewResponse = {
  ...defaultMissingOverviewFixture,
  system: {
    ...defaultMissingOverviewFixture.system,
    pipeline_status: {
      value: "SUCCESS",
      status: "AVAILABLE",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    last_run: {
      value: "2026-09-03T20:54:51+00:00",
      status: "AVAILABLE",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    data_date: {
      value: "2026-08-14",
      status: "AVAILABLE",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    market_data_date: {
      value: "2026-08-14",
      status: "STALE",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    investor_data_date: {
      value: "2026-07-31",
      status: "STALE",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    input_data_freshness: {
      value: "STALE",
      status: "STALE",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    ledger_status: {
      value: "MISSING",
      status: "MISSING",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
    freshness: {
      value: "STALE",
      status: "STALE",
      source: "output/shadow_dashboard_run_metadata.json",
      data_kind: "operational",
    },
  },
};
