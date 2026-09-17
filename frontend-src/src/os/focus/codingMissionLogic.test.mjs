import { mergeCodingSnapshot, codingDisplayStatus, isFailedCoding } from "./codingMissionLogic.js";

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assert failed");
}

const a = mergeCodingSnapshot(null, { files_changed: ["a.py"], status: "agent_progress" });
const b = mergeCodingSnapshot(a, { files_changed: ["b.py"], command: "pytest" });
assert(b.files_changed.includes("a.py") && b.files_changed.includes("b.py"), "files merge");
assert(b.success_implied === false, "no success implied");

assert(codingDisplayStatus({ status: "failed" }, null) === "failed", "failed visible");
assert(isFailedCoding({ status: "failed" }, "failed") === true, "is failed");
assert(codingDisplayStatus({ status: "completed", acceptance: { ok: true } }, "completed") === "accepted", "accepted");
assert(codingDisplayStatus({ status: "completed" }, "completed") === "pending_acceptance", "no premature success");
assert(codingDisplayStatus({ status: "executing", final_success: true }, null) === "accepted", "final_success wins");
assert(codingDisplayStatus({ status: "completed", final_success: false, acceptance: { ok: false } }, "completed") === "failed", "final_success false");

console.log("codingMissionLogic tests ok");
