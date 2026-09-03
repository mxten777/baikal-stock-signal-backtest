import { Component, ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            backgroundColor: "#1e1e2d",
            border: "1px solid #e11d48",
            borderRadius: "6px",
            padding: "1.5rem",
            color: "#fda4af",
            margin: "1rem 0",
          }}
        >
          <h3 style={{ color: "#f43f5e", marginBottom: "0.5rem" }}>
            {this.props.fallbackTitle || "Component Error"}
          </h3>
          <p style={{ fontSize: "0.85rem", color: "#cbd5e1" }}>
            {this.state.error?.message || "An unexpected error occurred."}
          </p>
        </div>
      );
    }

    return this.props.children;
  }
}
