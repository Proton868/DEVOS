/**
 * DevOS Panel Wrapper Utility
 * Converts existing components into DevOS panels by registering them in the panel registry.
 * This is the compatibility layer for migrating existing components to the panel system.
 *
 * Usage:
 *   import AutomationHub from "../automation/AutomationHub";
 *   wrapAsPanel("workflow", {
 *     name: "Workflow Canvas",
 *     component: AutomationHub,
 *     icon: Workflow,
 *     category: "workflow",
 *     defaultSize: { width: 600, height: 400 },
 *   });
 */
import React, { lazy } from "react";
import { panelRegistry } from "./panelRegistry";

/**
 * Wrap a component as a DevOS panel.
 * @param {string} id - Unique panel type ID
 * @param {object} options - Panel definition
 * @param {string} options.name - Display name
 * @param {string} options.description
 * @param {React.Component|Function} options.component - The panel content component
 * @param {string} options.icon - Icon name (lucide) or component
 * @param {string} options.category - 'core' | 'ide' | 'workflow' | 'agent' | 'tools'
 * @param {object} options.defaultConfig - Default panel configuration
 * @param {object} options.defaultSize - Default {width, height}
 * @param {boolean} options.singleton - Only one instance allowed (default true)
 * @param {boolean} options.sidebarItem - Show in sidebar by default (default true)
 */
export function wrapAsPanel(id, options) {
  const {
    name,
    description = "",
    component,
    icon = "Square",
    category = "core",
    defaultConfig = {},
    defaultSize = { width: 420, height: 300 },
    singleton = true,
    sidebarItem = true,
    lazy: useLazy = false,
  } = options;

  // Support lazy loading
  let Component = component;
  if (useLazy && typeof component === "function") {
    Component = lazy(component);
  }

  // Wrap the component to pass panel context
  const WrappedComponent = React.forwardRef((props, ref) => {
    return <Component ref={ref} {...props} />;
  });
  WrappedComponent.displayName = `Panel(${name || id})`;

  panelRegistry.register(id, {
    name,
    description,
    component: WrappedComponent,
    icon,
    category,
    defaultConfig,
    defaultSize,
    singleton,
    sidebarItem,
  });

  return WrappedComponent;
}

/**
 * Wrap multiple components as panels.
 * @param {Array} panels - Array of {id, ...options}
 */
export function wrapPanelsAsRegistered(panels) {
  return panels.map((p) => wrapAsPanel(p.id, p));
}

export default wrapAsPanel;
