import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Header } from "../components/Header/Header";

describe("Header Component", () => {
  it("renders the title and subtitle correctly", () => {
    render(
      <Header
        mode="SHADOW"
        isReadOnly={true}
        baselineCommit="e4d38f7a6c"
        dataStatus="MISSING"
      />
    );

    expect(screen.getByText("BAIKAL Stock Signal")).toBeInTheDocument();
    expect(
      screen.getByText("Shadow Validation Control Tower")
    ).toBeInTheDocument();
  });

  it("renders SHADOW and READ ONLY badges", () => {
    render(
      <Header
        mode="SHADOW"
        isReadOnly={true}
        baselineCommit="e4d38f7a6c"
        dataStatus="AVAILABLE"
      />
    );

    expect(screen.getByText("SHADOW")).toBeInTheDocument();
    expect(screen.getByText("READ ONLY")).toBeInTheDocument();
  });

  it("renders baseline commit and data status badge", () => {
    render(
      <Header
        mode="SHADOW"
        isReadOnly={true}
        baselineCommit="e4d38f7"
        dataStatus="MISSING"
      />
    );

    expect(screen.getByText(/BASELINE E4D38F7/i)).toBeInTheDocument();
    expect(screen.getByText(/DATA MISSING/i)).toBeInTheDocument();
  });

  it("renders operational input freshness label when provided", () => {
    render(
      <Header
        mode="SHADOW"
        isReadOnly={true}
        baselineCommit="e4d38f7"
        dataStatus="STALE"
        dataStatusLabel="MARKET DATA STALE"
      />
    );

    expect(screen.getByText(/MARKET DATA STALE/i)).toBeInTheDocument();
  });
});
