import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { SignalLedger } from "../features/signal-ledger/SignalLedger";
import { SignalLedgerData } from "../types/dashboard";

describe("SignalLedger Feature", () => {
  it("renders empty state when ledger is MISSING", () => {
    const missingLedger: SignalLedgerData = {
      status: "MISSING",
      source: "output/shadow_signal_records.csv",
      data_kind: "operational",
      as_of: null,
      sample_size: 0,
      records: [],
    };

    render(<SignalLedger ledger={missingLedger} />);

    expect(screen.getByText("Shadow Signal Ledger")).toBeInTheDocument();
    expect(screen.getByText("No Shadow Records Available")).toBeInTheDocument();
  });

  it("renders table rows with Candidate / Excluded badges when records exist", () => {
    const populatedLedger: SignalLedgerData = {
      status: "AVAILABLE",
      source: "output/shadow_signal_records.csv",
      data_kind: "operational",
      as_of: "2026-09-04",
      sample_size: 2,
      records: [
        {
          stock_code: "005930",
          stock_name: "Samsung Elec",
          market: "KOSPI",
          signal_date: "2026-09-04",
          signal_score: 85,
          decision: "CANDIDATE",
          foreign_status: "POSITIVE",
          status: "OPEN",
          return_5d: 2.5,
          excess_5d: 1.2,
        },
        {
          stock_code: "035420",
          stock_name: "NAVER",
          market: "KOSPI",
          signal_date: "2026-09-04",
          signal_score: 65,
          decision: "EXCLUDED",
          exclusion_reason: "NEGATIVE_FLOW",
          foreign_status: "NEGATIVE",
          status: "OPEN",
        },
      ],
    };

    render(<SignalLedger ledger={populatedLedger} />);

    expect(screen.getByText("Samsung Elec")).toBeInTheDocument();
    expect(screen.getByText("005930")).toBeInTheDocument();
    expect(screen.getByText("NAVER")).toBeInTheDocument();
    expect(screen.getByText("CANDIDATE")).toBeInTheDocument();
    expect(screen.getByText("EXCLUDED")).toBeInTheDocument();
    expect(screen.getByText("NEGATIVE_FLOW")).toBeInTheDocument();
    expect(screen.getByText("+2.50%")).toBeInTheDocument();
  });
});
