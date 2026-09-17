import assert from "assert";
import {
  refineStates,
  formatElapsed,
  isTerminalStatus,
  KNOWN_STATUS_KEYS,
} from "../src/os/focus/nuhaTaskProgressLogic.js";

assert.ok(KNOWN_STATUS_KEYS.includes("planning"));
assert.ok(KNOWN_STATUS_KEYS.includes("artifact_created"));
assert.ok(!KNOWN_STATUS_KEYS.includes("fake_stage"));

const pending = refineStates([], null, true);
assert.ok(pending.every((s) => s.state === "pending"));

const activePlan = refineStates(["planning"], "planning", true);
assert.strictEqual(activePlan.find((s) => s.id === "planning").state, "active");

const advanced = refineStates(["planning", "plan_created"], "plan_created", true);
assert.strictEqual(advanced.find((s) => s.id === "planning").state, "completed");
assert.strictEqual(advanced.find((s) => s.id === "plan_created").state, "active");

const failed = refineStates(["planning", "plan_created"], "failed", false);
assert.strictEqual(failed.find((s) => s.id === "validation").state, "pending");
assert.ok(isTerminalStatus("failed"));
assert.ok(isTerminalStatus("completed"));
assert.ok(isTerminalStatus("cancelled"));
assert.ok(!isTerminalStatus("agent_progress"));

assert.strictEqual(formatElapsed(null), null);
assert.strictEqual(formatElapsed(-1), null);
assert.strictEqual(formatElapsed(1500), "1s");

console.log("nuhaTaskProgressLogic: 6 assertions OK");
