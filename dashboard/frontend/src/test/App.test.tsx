import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, afterEach, vi } from "vitest";
import { App } from "../App";
import { defaultMissingOverviewFixture } from "./fixtures";
import { dashboardApi, DashboardApiError } from "../api/dashboardApi";

describe("App Root Integration Test", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders full overview structure correctly when API succeeds", async () => {
    vi.spyOn(dashboardApi, "getOverview").mockResolvedValue(
      defaultMissingOverviewFixture
    );

    render(<App />);

    // Header
    expect(screen.getByText("BAIKAL Stock Signal")).toBeInTheDocument();
    expect(screen.getByText("READ ONLY")).toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByText("System Status & Environment")
      ).toBeInTheDocument();
    });

    // Panels
    expect(screen.getByText("Today's Shadow Monitor")).toBeInTheDocument();
    expect(screen.getByText("Maturity Monitor")).toBeInTheDocument();
    expect(screen.getByText("Strategy Performance")).toBeInTheDocument();
    expect(screen.getByText("Foreign Flow Validation")).toBeInTheDocument();
    expect(screen.getByText("Weakness Segment Monitor")).toBeInTheDocument();
    expect(screen.getByText("Risk & Drawdown Monitor")).toBeInTheDocument();
    expect(
      screen.getByText("Opportunity Cost & Exclusion Analysis")
    ).toBeInTheDocument();
    expect(screen.getByText("Shadow Signal Ledger")).toBeInTheDocument();

    // Verify "No operational Shadow data yet" notice is displayed
    expect(
      screen.getByText(/No operational Shadow data yet/i)
    ).toBeInTheDocument();
  });

  it("renders error banner when API call fails", async () => {
    vi.spyOn(dashboardApi, "getOverview").mockRejectedValue(
      new DashboardApiError(500, "Internal Server Error", "Failed to connect")
    );

    render(<App />);

    await waitFor(() => {
      expect(
        screen.getByText("Adapter Connection Error")
      ).toBeInTheDocument();
    });

    expect(screen.getByText("Retry")).toBeInTheDocument();
  });
});
