import {
  nodeGlowState, planGlowState, nodeStatusLabel, planNeedsHitl, planPivotInfo, isActiveMission,
} from "./orchestrationUi.js";
import { normalizeSurfaceIntent } from "./surfaceIntent.js";
import assert from "assert";

assert.equal(nodeGlowState("running"), "EXECUTING");
assert.equal(nodeGlowState("waiting_for_user"), "WAITING");
assert.equal(nodeGlowState("verifying"), "VERIFYING");
assert.equal(nodeGlowState("cancelled"), "CANCELLED");
assert.equal(nodeGlowState("completed"), "SUCCESS");
assert.equal(nodeGlowState("failed"), "FAILED");
assert.equal(nodeStatusLabel("waiting_for_user"), "Waiting for you");
assert.equal(planNeedsHitl({ status: "waiting_for_user" }), true);
assert.equal(planNeedsHitl({ status: "running" }), false);
assert.deepEqual(planPivotInfo({ pivot_reached: true, pivot_action: "github_push" }).action, "github_push");
assert.equal(isActiveMission("running"), true);
assert.equal(isActiveMission("completed"), false);

const n = normalizeSurfaceIntent({ surface: "deployment", action: "open", required: false });
assert.equal(n.surface, "deployment");
assert.equal(n.invalid, undefined);

const bad = normalizeSurfaceIntent({ surface: "not_a_surface", action: "open" });
assert.equal(bad.invalid, true);

console.log("orchestrationUi + surfaceIntent tests OK");
