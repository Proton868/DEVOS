import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const vs = join(root, "frontend", "static", "monaco", "vs", "loader.js");
if (!existsSync(vs)) {
  console.error("FAIL: missing", vs);
  process.exit(1);
}
console.log("PASS: monaco assets present at frontend/static/monaco/vs");
