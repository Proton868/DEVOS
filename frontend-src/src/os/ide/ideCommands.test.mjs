import { strict as assert } from "node:assert";
import { listIdeCommands, getIdeCommand, IDE_COMMANDS } from "./ideCommands.js";

assert.ok(IDE_COMMANDS.length >= 8);
assert.ok(getIdeCommand("ide.editor.format"));
assert.ok(listIdeCommands("format").some((c) => c.id === "ide.editor.format"));
assert.equal(listIdeCommands("zzz-not-found").length, 0);
console.log("ideCommands.test.mjs: ok");
