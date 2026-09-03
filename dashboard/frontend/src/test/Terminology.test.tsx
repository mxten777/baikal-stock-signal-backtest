import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { App } from "../App";
import { defaultMissingOverviewFixture } from "./fixtures";
import { dashboardApi } from "../api/dashboardApi";

describe("Strict Terminology & Clean Control Tower Verification", () => {
  beforeEach(() => {
    vi.spyOn(dashboardApi, "getOverview").mockResolvedValue(
      defaultMissingOverviewFixture
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("ensures forbidden trading/recommendation terms do NOT appear anywhere in the rendered UI", async () => {
    const { container } = render(<App />);

    // Wait for async load
    await screen.findByText("System Status & Environment");

    const fullText = container.textContent || "";

    // Forbidden terms:
    // BUY, SELL (case-insensitive words), 매수, 매도, 추천
    expect(fullText).not.toMatch(/\bBUY\b/i);
    expect(fullText).not.toMatch(/\bSELL\b/i);
    expect(fullText).not.toContain("매수");
    expect(fullText).not.toContain("매도");
    expect(fullText).not.toContain("추천");
  });

  it("verifies permitted operational signal terminology is present", async () => {
    const { container } = render(<App />);

    await screen.findByText("System Status & Environment");

    const fullText = container.textContent || "";

    expect(fullText).toContain("Candidate");
    expect(fullText).toContain("Excluded");
    expect(fullText).toContain("Signal");
    expect(fullText).toContain("Maturity");
  });
});
