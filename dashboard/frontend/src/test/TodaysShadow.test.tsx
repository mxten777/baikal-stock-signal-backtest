import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TodaysShadow } from "../features/todays-shadow/TodaysShadow";
import { defaultMissingOverviewFixture } from "./fixtures";

describe("TodaysShadow Feature", () => {
  it("renders all required operational categories", () => {
    render(<TodaysShadow today={defaultMissingOverviewFixture.today} />);

    expect(screen.getByText("Today's Shadow Monitor")).toBeInTheDocument();
    expect(screen.getByText("New Signals")).toBeInTheDocument();
    expect(screen.getByText("Candidate")).toBeInTheDocument();
    expect(screen.getByText("Excluded")).toBeInTheDocument();
    expect(screen.getByText("KOSDAQ")).toBeInTheDocument();
    expect(screen.getByText("HIGH Classification")).toBeInTheDocument();
  });

  it("does not render fake 0 when ledger is MISSING or UNAVAILABLE", () => {
    render(<TodaysShadow today={defaultMissingOverviewFixture.today} />);

    // Check that MISSING is displayed for missing metrics instead of 0
    const missingElements = screen.getAllByText("MISSING");
    expect(missingElements.length).toBeGreaterThan(0);

    // HIGH metric should show UNAVAILABLE
    const unavailElements = screen.getAllByText("UNAVAILABLE");
    expect(unavailElements.length).toBeGreaterThan(0);
  });
});
