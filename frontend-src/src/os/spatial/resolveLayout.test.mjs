/**
 * Spatial engine unit tests (node:test / assert — no browser).
 */
import { strict as assert } from "node:assert";
import {
  resolveLayout,
  maxSimultaneousPrimaries,
  openIdsFromLegacyFlags,
  normalizeActiveId,
  contentArea,
} from "./resolveLayout.js";
import { WORKSPACE_REGISTRY } from "./registry.js";

function baseState(over = {}) {
  return {
    activeId: "chat",
    openIds: ["flow", "chat", "ide"],
    collapsedIds: [],
    fullscreenId: null,
    overlayId: null,
    widthPct: { focus: 62 },
    ...over,
  };
}

// content area subtracts chrome
{
  const a = contentArea({ width: 1000, height: 800 }, { railWidth: 52, missionBarHeight: 44 });
  assert.equal(a.width, 948);
  assert.equal(a.height, 756);
}

// large desktop: multiple primaries visible
{
  const r = resolveLayout(
    { width: 1600, height: 900, orientation: "landscape" },
    baseState({ activeId: "ide", openIds: ["flow", "chat", "ide"] })
  );
  assert.equal(r.presentation, "split");
  assert.ok(r.visibleIds.includes("ide") || r.surfaces.ide?.mode === "visible");
  assert.ok(["visible", "fullscreen"].includes(r.surfaces.ide.mode));
}

// narrow width: stack mode
{
  const r = resolveLayout(
    { width: 390, height: 844, orientation: "portrait" },
    baseState({ activeId: "chat", openIds: ["flow", "chat", "ide"] })
  );
  assert.equal(r.presentation, "stack");
  assert.equal(r.surfaces.chat.mode, "fullscreen");
  assert.equal(r.showDimensionNav, true);
  // inactive open dimensions collapse for nav
  assert.ok(
    r.surfaces.ide.mode === "collapsed" || r.surfaces.ide.mode === "hidden"
  );
}

// tablet-ish mid width
{
  const r = resolveLayout(
    { width: 900, height: 700, orientation: "landscape" },
    baseState({ activeId: "ide", openIds: ["flow", "ide"] })
  );
  assert.ok(r.presentation === "split" || r.presentation === "stack");
  assert.ok(r.surfaces.ide);
}

// fullscreen exclusive
{
  const r = resolveLayout(
    { width: 1400, height: 900 },
    baseState({ fullscreenId: "preview", openIds: ["flow", "preview", "chat"], activeId: "preview" })
  );
  assert.equal(r.surfaces.preview.mode, "fullscreen");
  assert.notEqual(r.surfaces.chat.mode, "fullscreen");
}

// max simultaneous respects mins
{
  const n = maxSimultaneousPrimaries(700, ["chat", "ide", "flow"]);
  assert.ok(n >= 1 && n <= 3);
}

// legacy flags
{
  const ids = openIdsFromLegacyFlags({
    copilotOpen: true,
    editorOpen: true,
    previewOpen: false,
    terminalOpen: true,
  });
  assert.ok(ids.includes("flow"));
  assert.ok(ids.includes("chat"));
  assert.ok(ids.includes("ide"));
  assert.ok(ids.includes("terminal"));
}

assert.equal(normalizeActiveId("canvas"), "flow");
assert.equal(normalizeActiveId("ide"), "ide");

// registry completeness
for (const id of ["flow", "chat", "ide", "preview", "terminal", "inspector", "files"]) {
  assert.ok(WORKSPACE_REGISTRY[id], `missing ${id}`);
  assert.ok(WORKSPACE_REGISTRY[id].minWidth > 0);
  assert.ok(WORKSPACE_REGISTRY[id].priority > 0);
}

// insufficient width demotes rather than crush below min
{
  const r = resolveLayout(
    { width: 500, height: 800 },
    baseState({
      activeId: "ide",
      openIds: ["flow", "chat", "ide", "preview"],
      collapsedIds: [],
    })
  );
  for (const id of r.visibleIds) {
    const mode = r.surfaces[id].mode;
    if (mode === "visible" && r.surfaces[id].widthPx) {
      assert.ok(
        r.surfaces[id].widthPx >= WORKSPACE_REGISTRY[id].minWidth - 1,
        `${id} crushed`
      );
    }
  }
}

console.log("resolveLayout.test.mjs: ok");

// portrait stack: prefer single active dimension, dimension nav
{
  const plan = resolveLayout(
    { width: 390, height: 844, orientation: "portrait" },
    {
      activeId: "ide",
      openIds: ["flow", "chat", "ide"],
      fullscreenId: null,
      collapsedIds: [],
    }
  );
  assert.equal(plan.presentation, "stack");
  assert.equal(plan.showDimensionNav, true);
  assert.ok(plan.surfaces.ide?.mode === "visible" || plan.surfaces.ide?.mode === "fullscreen");
}

// tablet landscape mid-ground: limited simultaneous
{
  const plan = resolveLayout(
    { width: 900, height: 600, orientation: "landscape" },
    {
      activeId: "flow",
      openIds: ["flow", "chat", "ide"],
      fullscreenId: null,
      collapsedIds: [],
    }
  );
  assert.ok(["split", "stack"].includes(plan.presentation));
}

// fullscreen chat restores active
{
  const plan = resolveLayout(
    { width: 1280, height: 800 },
    {
      activeId: "flow",
      openIds: ["flow", "chat"],
      fullscreenId: "chat",
      collapsedIds: [],
    }
  );
  assert.equal(plan.surfaces.chat?.mode, "fullscreen");
}

console.log("resolveLayout.test.mjs: extended ok");
