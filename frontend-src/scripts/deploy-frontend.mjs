/**
 * Atomic CRA → FastAPI static deploy.
 *
 *   build/index.html  → frontend/templates/index.html
 *   build/static/**   → frontend/static/**
 *   + vendor monaco   → frontend/static/monaco/vs
 *
 * Guarantees:
 * - index.html and hashed assets always come from the SAME build
 * - every webpack chunk referenced by main.*.js exists on disk
 * - static swap is staged then renamed (no half-written tree)
 */
import {
  cpSync,
  mkdirSync,
  rmSync,
  existsSync,
  readFileSync,
  renameSync,
  mkdtempSync,
  writeFileSync,
  readdirSync,
} from "node:fs";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const buildDir = join(here, "..", "build");
const frontendDir = join(here, "..", "..", "frontend");
const buildIndex = join(buildDir, "index.html");
const buildStatic = join(buildDir, "static");

if (!existsSync(buildIndex) || !existsSync(buildStatic)) {
  console.error("deploy-frontend: build/ missing — run `npm run build` first.");
  process.exit(1);
}

function collectHtmlRefs(html) {
  return [...html.matchAll(/\/static\/[^"']+/g)].map((m) => m[0]);
}

function collectMainChunkRefs(staticJsDir) {
  const files = existsSync(staticJsDir) ? readdirSync(staticJsDir) : [];
  const mains = files.filter(
    (f) => /^main\.[a-f0-9]+\.js$/.test(f) && !f.endsWith(".LICENSE.txt")
  );
  const refs = [];
  for (const name of mains) {
    const text = readFileSync(join(staticJsDir, name), "utf8");
    const pairs = [...text.matchAll(/(\d+):\"([a-f0-9]{8})\"/g)];
    for (const [, id, hash] of pairs) {
      refs.push(`${id}.${hash}.chunk.js`);
    }
  }
  return { mains, refs };
}

// Stage into a temp directory, validate, then atomically replace
const stageRoot = mkdtempSync(join(tmpdir(), "devos-frontend-"));
const stageStatic = join(stageRoot, "static");
const stageTemplates = join(stageRoot, "templates");
mkdirSync(stageTemplates, { recursive: true });
cpSync(buildIndex, join(stageTemplates, "index.html"));
cpSync(buildStatic, stageStatic, { recursive: true });

// Vendor Monaco into staged static
const monacoSrc = join(here, "..", "node_modules", "monaco-editor", "min", "vs");
const monacoDest = join(stageStatic, "monaco", "vs");
if (existsSync(monacoSrc)) {
  mkdirSync(join(stageStatic, "monaco"), { recursive: true });
  cpSync(monacoSrc, monacoDest, { recursive: true });
  console.log("deploy-frontend: vendored monaco → staged static/monaco/vs");
} else if (existsSync(join(frontendDir, "static", "monaco", "vs", "loader.js"))) {
  // Preserve previously committed monaco if node_modules missing
  mkdirSync(join(stageStatic, "monaco"), { recursive: true });
  cpSync(join(frontendDir, "static", "monaco", "vs"), monacoDest, { recursive: true });
  console.log("deploy-frontend: preserved existing monaco vs tree");
} else {
  console.warn("deploy-frontend: monaco-editor missing — IDE may fail to start");
}

const html = readFileSync(join(stageTemplates, "index.html"), "utf8");
const htmlRefs = collectHtmlRefs(html);
const htmlMissing = htmlRefs.filter((r) => !existsSync(join(stageRoot, r.replace(/^\//, ""))));
if (htmlMissing.length) {
  console.error("deploy-frontend: index.html references missing assets:", htmlMissing);
  process.exit(1);
}

const { mains, refs: chunkRefs } = collectMainChunkRefs(join(stageStatic, "js"));
const chunkMissing = chunkRefs.filter((f) => !existsSync(join(stageStatic, "js", f)));
if (chunkMissing.length) {
  console.error("deploy-frontend: main bundle references missing chunks:", chunkMissing);
  process.exit(1);
}
if (!mains.length) {
  console.error("deploy-frontend: no main.*.js in build/static/js");
  process.exit(1);
}

// Write deploy manifest for operators / tests
writeFileSync(
  join(stageStatic, "deploy-manifest.json"),
  JSON.stringify(
    {
      mains,
      htmlRefs,
      chunkRefs,
      builtAt: new Date().toISOString(),
    },
    null,
    2
  )
);

// Atomic-ish replace: move old static aside, move stage in
mkdirSync(join(frontendDir, "templates"), { recursive: true });
const liveStatic = join(frontendDir, "static");
const liveTemplates = join(frontendDir, "templates");
const backupStatic = join(frontendDir, ".static-prev");
const backupTemplatesIndex = join(frontendDir, ".index.html.prev");

rmSync(backupStatic, { recursive: true, force: true });
if (existsSync(liveStatic)) {
  renameSync(liveStatic, backupStatic);
}
renameSync(stageStatic, liveStatic);

const liveIndex = join(liveTemplates, "index.html");
if (existsSync(liveIndex)) {
  try {
    cpSync(liveIndex, backupTemplatesIndex);
  } catch (_) {
    /* ignore */
  }
}
cpSync(join(stageTemplates, "index.html"), liveIndex);

// Cleanup stage shell + old backup (keep one backup cycle only briefly)
rmSync(stageRoot, { recursive: true, force: true });
rmSync(backupStatic, { recursive: true, force: true });
try {
  rmSync(backupTemplatesIndex, { force: true });
} catch (_) {
  /* ignore */
}

console.log(
  `deploy-frontend: atomic deploy OK — main=${mains.join(",")} htmlRefs=${htmlRefs.length} chunks=${chunkRefs.length}`
);
