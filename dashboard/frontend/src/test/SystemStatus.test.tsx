import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { SystemStatus } from "../features/system-status/SystemStatus";
import { defaultMissingOverviewFixture } from "./fixtures";

describe("SystemStatus Feature", () => {
  it("renders pipeline status as UNAVAILABLE in STEP 2 contract", () => {
    render(<SystemStatus system={defaultMissingOverviewFixture.system} />);

    expect(screen.getByText("System Status & Environment")).toBeInTheDocument();
    expect(screen.getByText("Pipeline Status")).toBeInTheDocument();
    expect(screen.getByText("Last Run")).toBeInTheDocument();
    expect(screen.getByText("Data Date")).toBeInTheDocument();
    expect(screen.getByText("Freshness")).toBeInTheDocument();

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
});
