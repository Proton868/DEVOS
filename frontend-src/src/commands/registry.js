/**
 * Central IDE command registry.
 * Components register commands once; CommandPalette and shortcuts consume this list.
 * Do not hardcode command catalogs in scattered UI components.
 */

const _commands = new Map();

/**
 * @param {{
 *   id: string,
 *   label: string,
 *   category?: string,
 *   shortcut?: string,
 *   keywords?: string[],
 *   when?: () => boolean,
 *   run: () => void | Promise<void>,
 * }} cmd
 */
export function registerCommand(cmd) {
  if (!cmd?.id || typeof cmd.run !== "function") {
    throw new Error("registerCommand requires id and run()");
  }
  _commands.set(cmd.id, {
    category: "General",
    keywords: [],
    when: () => true,
    ...cmd,
  });
  return () => _commands.delete(cmd.id);
}

export function getCommand(id) {
  return _commands.get(id) || null;
}

export function listCommands({ query = "", includeDisabled = false } = {}) {
  const q = (query || "").trim().toLowerCase();
  const items = [];
  for (const cmd of _commands.values()) {
    const enabled = !cmd.when || cmd.when();
    if (!includeDisabled && !enabled) continue;
    if (q) {
      const hay = [cmd.label, cmd.id, cmd.category, ...(cmd.keywords || [])]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) continue;
    }
    items.push({ ...cmd, enabled });
  }
  items.sort((a, b) => a.label.localeCompare(b.label));
  return items;
}

export async function executeCommand(id) {
  const cmd = _commands.get(id);
  if (!cmd) throw new Error(`Unknown command: ${id}`);
  if (cmd.when && !cmd.when()) throw new Error(`Command disabled: ${id}`);
  return cmd.run();
}

export function clearCommands() {
  _commands.clear();
}
