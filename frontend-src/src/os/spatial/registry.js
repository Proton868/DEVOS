/**
 * First-class spatial workspace (dimension) registry.
 * Content minimums drive adaptation — not device marketing names.
 */

/** @typedef {'primary'|'secondary'|'utility'} WorkspaceKind */

/**
 * @typedef {Object} WorkspaceDefinition
 * @property {string} id
 * @property {string} label
 * @property {WorkspaceKind} kind
 * @property {number} minWidth
 * @property {number} minHeight
 * @property {number} preferredWidth
 * @property {number} preferredHeight
 * @property {number} flexibility  // 0 rigid … 1 highly flexible
 * @property {number} priority     // higher survives longer under pressure
 * @property {boolean} supportsFullscreen
 * @property {boolean} supportsOverlay
 * @property {boolean} supportsDock
 * @property {boolean} supportsMobileNav
 * @property {boolean} supportsGestures
 * @property {boolean} defaultOpen
 */

/** @type {Record<string, WorkspaceDefinition>} */
export const WORKSPACE_REGISTRY = {
  flow: {
    id: "flow",
    label: "Flow",
    kind: "primary",
    minWidth: 320,
    minHeight: 280,
    preferredWidth: 720,
    preferredHeight: 600,
    flexibility: 0.85,
    priority: 70,
    supportsFullscreen: true,
    supportsOverlay: false,
    supportsDock: true,
    supportsMobileNav: true,
    supportsGestures: true,
    defaultOpen: true,
  },
  chat: {
    id: "chat",
    label: "Nuha",
    kind: "primary",
    minWidth: 300,
    minHeight: 360,
    preferredWidth: 420,
    preferredHeight: 640,
    flexibility: 0.55,
    priority: 90,
    supportsFullscreen: true,
    supportsOverlay: true,
    supportsDock: true,
    supportsMobileNav: true,
    supportsGestures: true,
    defaultOpen: true,
  },
  ide: {
    id: "ide",
    label: "IDE",
    kind: "primary",
    minWidth: 360,
    minHeight: 320,
    preferredWidth: 640,
    preferredHeight: 720,
    flexibility: 0.65,
    priority: 85,
    supportsFullscreen: true,
    supportsOverlay: false,
    supportsDock: true,
    supportsMobileNav: true,
    supportsGestures: true,
    defaultOpen: false,
  },
  preview: {
    id: "preview",
    label: "Preview",
    kind: "primary",
    minWidth: 280,
    minHeight: 240,
    preferredWidth: 480,
    preferredHeight: 560,
    flexibility: 0.7,
    priority: 75,
    supportsFullscreen: true,
    supportsOverlay: true,
    supportsDock: true,
    supportsMobileNav: true,
    supportsGestures: true,
    defaultOpen: false,
  },
  terminal: {
    id: "terminal",
    label: "Terminal",
    kind: "secondary",
    minWidth: 280,
    minHeight: 140,
    preferredWidth: 560,
    preferredHeight: 220,
    flexibility: 0.6,
    priority: 50,
    supportsFullscreen: true,
    supportsOverlay: true,
    supportsDock: true,
    supportsMobileNav: true,
    supportsGestures: false,
    defaultOpen: false,
  },
  inspector: {
    id: "inspector",
    label: "Inspector",
    kind: "secondary",
    minWidth: 260,
    minHeight: 200,
    preferredWidth: 320,
    preferredHeight: 400,
    flexibility: 0.5,
    priority: 45,
    supportsFullscreen: false,
    supportsOverlay: true,
    supportsDock: true,
    supportsMobileNav: false,
    supportsGestures: false,
    defaultOpen: false,
  },
  files: {
    id: "files",
    label: "Files",
    kind: "utility",
    minWidth: 220,
    minHeight: 200,
    preferredWidth: 280,
    preferredHeight: 480,
    flexibility: 0.4,
    priority: 40,
    supportsFullscreen: false,
    supportsOverlay: true,
    supportsDock: true,
    supportsMobileNav: false,
    supportsGestures: false,
    defaultOpen: false,
  },
};

export const PRIMARY_NAV_ORDER = ["chat", "flow", "ide", "preview"];

export function getWorkspace(id) {
  return WORKSPACE_REGISTRY[id] || null;
}

export function listWorkspaces() {
  return Object.values(WORKSPACE_REGISTRY);
}
