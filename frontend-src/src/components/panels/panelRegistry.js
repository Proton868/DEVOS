/**
 * DevOS Panel Registry
 * Tracks all available panel types and their metadata.
 * Panels register themselves here; the layout system reads from here.
 */

class PanelRegistry {
  constructor() {
    this.panels = new Map();
    this.listeners = new Set();
  }

  /**
   * Register a panel type.
   * @param {string} id - Unique panel type ID (e.g., 'workflow', 'ide', 'chat')
   * @param {object} definition - Panel definition
   * @param {string} definition.name - Display name
   * @param {string} definition.description
   * @param {React.Component} definition.component - The panel content component
   * @param {string} definition.icon - Icon name (lucide) or emoji
   * @param {string} definition.category - 'core' | 'ide' | 'workflow' | 'agent' | 'tools'
   * @param {object} definition.defaultConfig - Default panel configuration
   * @param {object} definition.defaultSize - Default {width, height}
   * @param {boolean} definition.singleton - Only one instance allowed
   * @param {boolean} definition.sidebarItem - Show in sidebar by default
   */
  register(id, definition) {
    this.panels.set(id, {
      id,
      name: definition.name || id,
      description: definition.description || "",
      component: definition.component,
      icon: definition.icon || "Square",
      category: definition.category || "core",
      defaultConfig: definition.defaultConfig || {},
      defaultSize: definition.defaultSize || { width: 420, height: 300 },
      singleton: definition.singleton !== false, // default true
      sidebarItem: definition.sidebarItem !== false, // default true
      ...definition,
    });
    this.notify();
  }

  /**
   * Unregister a panel type.
   */
  unregister(id) {
    this.panels.delete(id);
    this.notify();
  }

  /**
   * Get a panel definition by ID.
   */
  get(id) {
    return this.panels.get(id);
  }

  /**
   * Get all registered panel definitions.
   */
  getAll() {
    return Array.from(this.panels.values());
  }

  /**
   * Get panels that should appear in the sidebar.
   */
  getSidebarPanels() {
    return this.getAll().filter((p) => p.sidebarItem);
  }

  /**
   * Get panels by category.
   */
  getByCategory(category) {
    return this.getAll().filter((p) => p.category === category);
  }

  /**
   * Subscribe to registry changes.
   */
  on(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    this.listeners.forEach((l) => l());
  }
}

export const panelRegistry = new PanelRegistry();
export default panelRegistry;
