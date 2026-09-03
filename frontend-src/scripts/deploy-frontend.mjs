/**
 * Copies the CRA production build into the FastAPI static serving layout:
 *   build/index.html    -> ../frontend/templates/index.html
 *   build/static/       -> ../frontend/static/
 * This guarantees app.py always serves the ACTUAL current build assets
 * (no stale hashed JS/CSS references in index.html).
 */
import { cpSync, mkdirSync, rmSync, existsSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const buildDir = join(here, "..", "build");
const frontendDir = join(here, "..", "..", "frontend");

if (!existsSync(join(buildDir, "index.html"))) {
  console.error("deploy-frontend: build/index.html missing — run `npm run build` first.");
  process.exit(1);
}

mkdirSync(join(frontendDir, "templates"), { recursive: true });
rmSync(join(frontendDir, "static"), { recursive: true, force: true });
cpSync(join(buildDir, "index.html"), join(frontendDir, "templates", "index.html"));
cpSync(join(buildDir, "static"), join(frontendDir, "static"), { recursive: true });

// Verify the served index.html references assets that actually exist
const html = readFileSync(join(frontendDir, "templates", "index.html"), "utf8");
const refs = [...html.matchAll(/\/static\/[^"']+/g)].map((m) => m[0]);
const missing = refs.filter((r) => !existsSync(join(frontendDir, r.replace("/static/", "static/"))));
if (missing.length) {
  console.error("deploy-frontend: MISSING asset references:", missing);
  process.exit(1);
}
console.log(`deploy-frontend: deployed index.html + ${refs.length} asset refs to frontend/{templates,static} ✓`);
