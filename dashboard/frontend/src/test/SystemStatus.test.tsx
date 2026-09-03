import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { SystemStatus } from "../features/system-status/SystemStatus";
import { defaultMissingOverviewFixture, staleInputOverviewFixture } from "./fixtures";

describe("SystemStatus Feature", () => {
  it("renders pipeline status as UNAVAILABLE in STEP 2 contract", () => {
    render(<SystemStatus system={defaultMissingOverviewFixture.system} />);

    expect(screen.getByText("System Status & Environment")).toBeInTheDocument();
    expect(screen.getByText("Pipeline Status")).toBeInTheDocument();
    expect(screen.getByText("Last Run")).toBeInTheDocument();
    expect(screen.getByText("Scan Base Date")).toBeInTheDocument();
    expect(screen.getByText("Input Freshness")).toBeInTheDocument();
    expect(screen.getByText("Ledger Status")).toBeInTheDocument();

    // Verify UNAVAILABLE and MISSING statuses appear
    const unavailBadges = screen.getAllByText("UNAVAILABLE");
    expect(unavailBadges.length).toBeGreaterThan(0);
    expect(screen.getByText("Baseline:")).toBeInTheDocument();
    expect(screen.getByText("e4d38f7")).toBeInTheDocument();
  });

  it("renders operator-friendly sanitized warnings if present in system status", () => {
    render(<SystemStatus system={defaultMissingOverviewFixture.system} />);
    expect(
      screen.getByText("Shadow ledger file not found")
    ).toBeInTheDocument();
  });

  it("renders stale input freshness and missing ledger separately", () => {
    render(<SystemStatus system={staleInputOverviewFixture.system} />);

    expect(screen.getByText("Market Data Date")).toBeInTheDocument();
    expect(screen.getByText("Investor Data Date")).toBeInTheDocument();
    expect(screen.getAllByText("2026-08-14").length).toBeGreaterThan(0);
    expect(screen.getByText("2026-07-31")).toBeInTheDocument();
    expect(screen.getAllByText("STALE").length).toBeGreaterThan(0);
    expect(screen.getByText("Ledger Status")).toBeInTheDocument();
    expect(screen.getAllByText("MISSING").length).toBeGreaterThan(0);
  });
});
