/**
 * IDE command registry — consumed by CommandBar / keyboard handlers.
 * Commands invoke DevOS-native services; they do not embed a second IDE.
 */

/** @typedef {{ id: string, title: string, category?: string, run: (ctx: object) => any }} IdeCommand */

/** @type {IdeCommand[]} */
export const IDE_COMMANDS = [
  {
    id: "ide.editor.format",
    title: "Format Document",
    category: "Editor",
    run: (ctx) => ctx.formatDocument?.(),
  },
  {
    id: "ide.editor.save",
    title: "Save File",
    category: "Editor",
    run: (ctx) => ctx.saveFile?.(),
  },
  {
    id: "ide.editor.split",
    title: "Split Editor",
    category: "Editor",
    run: (ctx) => ctx.splitEditor?.(),
  },
  {
    id: "ide.editor.closeTab",
    title: "Close Editor",
    category: "Editor",
    run: (ctx) => ctx.closeActiveTab?.(),
  },
  {
    id: "ide.search.workspace",
    title: "Search in Workspace",
    category: "Search",
    run: (ctx) => ctx.openSearch?.(),
  },
  {
    id: "ide.goto.symbol",
    title: "Go to Symbol in Editor",
    category: "Navigation",
    run: (ctx) => ctx.gotoSymbol?.(),
  },
  {
    id: "ide.problems.focus",
    title: "Focus Problems",
    category: "View",
    run: (ctx) => ctx.focusProblems?.(),
  },
  {
    id: "ide.terminal.toggle",
    title: "Toggle Terminal",
    category: "View",
    run: (ctx) => ctx.toggleTerminal?.(),
  },
  {
    id: "ide.task.test",
    title: "Run Project Tests",
    category: "Tasks",
    run: (ctx) => ctx.runTests?.(),
  },
  {
    id: "ide.task.build",
    title: "Run Project Build",
    category: "Tasks",
    run: (ctx) => ctx.runBuild?.(),
  },
];

export function listIdeCommands(filter = "") {
  const q = String(filter || "").toLowerCase().trim();
  if (!q) return IDE_COMMANDS;
  return IDE_COMMANDS.filter(
    (c) => c.title.toLowerCase().includes(q) || c.id.includes(q) || (c.category || "").toLowerCase().includes(q)
  );
}

export function getIdeCommand(id) {
  return IDE_COMMANDS.find((c) => c.id === id) || null;
}
