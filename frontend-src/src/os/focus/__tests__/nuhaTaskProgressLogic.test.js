/**
 * @jest-environment node
 */
const {
  refineStates,
  formatElapsed,
  isTerminalStatus,
  KNOWN_STATUS_KEYS,
} = require("../nuhaTaskProgressLogic");

describe("nuhaTaskProgressLogic", () => {
  test("known keys include real SSE statuses only", () => {
    expect(KNOWN_STATUS_KEYS).toContain("planning");
    expect(KNOWN_STATUS_KEYS).toContain("plan_created");
    expect(KNOWN_STATUS_KEYS).toContain("delegating");
    expect(KNOWN_STATUS_KEYS).toContain("agent_progress");
    expect(KNOWN_STATUS_KEYS).not.toContain("fake_stage");
  });

  test("pending until real status seen", () => {
    const steps = refineStates([], null, true);
    expect(steps.every((s) => s.state === "pending")).toBe(true);
  });

  test("planning becomes active then completed when plan_created arrives", () => {
    let steps = refineStates(["planning"], "planning", true);
    const plan = steps.find((s) => s.id === "planning");
    expect(plan.state).toBe("active");
    steps = refineStates(["planning", "plan_created"], "plan_created", true);
    expect(steps.find((s) => s.id === "planning").state).toBe("completed");
    expect(steps.find((s) => s.id === "plan_created").state).toBe("active");
  });

  test("failed terminal marks failure without inventing success", () => {
    const steps = refineStates(["planning", "plan_created"], "failed", false);
    expect(isTerminalStatus("failed")).toBe(true);
    // steps that were seen remain completed; no automatic validation success
    expect(steps.find((s) => s.id === "validation").state).toBe("pending");
  });

  test("formatElapsed returns null without real start", () => {
    expect(formatElapsed(null)).toBe(null);
    expect(formatElapsed(-1)).toBe(null);
    expect(formatElapsed(1500)).toBe("1s");
  });

  test("completed terminal is recognized", () => {
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("cancelled")).toBe(true);
    expect(isTerminalStatus("agent_progress")).toBe(false);
  });
});
