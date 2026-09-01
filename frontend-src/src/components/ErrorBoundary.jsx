import React from "react";

/**
 * Premium ErrorBoundary — catches render errors in any child tree
 * and displays a polished fallback UI instead of a white screen.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
    console.error("[ErrorBoundary]", error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return typeof this.props.fallback === "function"
          ? this.props.fallback({ error: this.state.error, reset: this.handleReset })
          : this.props.fallback;
      }

      return (
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: 32,
          gap: 16,
          height: "100%",
          minHeight: 200,
          background: "rgba(13, 17, 23, 0.95)",
          backdropFilter: "blur(16px)",
          borderRadius: 12,
          border: "1px solid rgba(248, 81, 73, 0.2)",
          color: "#e6edf3",
          fontFamily: "system-ui, -apple-system, sans-serif",
        }}>
          <div style={{
            width: 48,
            height: 48,
            borderRadius: "50%",
            background: "rgba(248, 81, 73, 0.1)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 24,
          }}>
            ⚠
          </div>
          <div style={{ fontSize: 16, fontWeight: 600, textAlign: "center" }}>
            Something went wrong
          </div>
          <div style={{
            fontSize: 12,
            color: "#8b949e",
            textAlign: "center",
            maxWidth: 400,
            lineHeight: 1.6,
          }}>
            {this.state.error?.message || "An unexpected error occurred in this component."}
          </div>
          <button
            onClick={this.handleReset}
            style={{
              background: "linear-gradient(135deg, #238636 0%, #2ea043 100%)",
              color: "#fff",
              border: "none",
              borderRadius: 8,
              padding: "8px 20px",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
              transition: "all 0.2s",
            }}
          >
            Try Again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}