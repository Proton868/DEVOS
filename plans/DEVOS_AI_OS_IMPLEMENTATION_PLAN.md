# DevOS AI Operating System — Implementation Plan

*Generated: 2026-07-23*  
*Status: Approved for Implementation*

---

## Executive Summary

Transform DevOS from a web dashboard into a premium AI Operating System with dockable panels, workspace presets, 3-state sidebar, flexible chat modes, modular IDE, smart terminal, living workflow nodes, and 16+ themes.

---

## Architectural Decisions

### ✅ Panel System Architecture
**Decision: Custom Build with `react-dnd` + `react-resizable-panels`**

**Rationale:**
- Full control over docking behavior and visual feedback
- No licensing constraints
- Can match exact vision (JetBrains/VS Code-style docking)
- `react-resizable-panels` already in dependencies for panel resizing
- `react-dnd` provides battle-tested drag-and-drop primitives

**Implementation Approach:**
- Create dock zones (top, bottom, left, right, center, floating)
- Implement panel drag handles with visual docking indicators
- Use `react-resizable-panels` for size management
- Custom state management for panel positions/sizes

---

### ✅ State Management Architecture
**Decision: Separate Specialized Stores**

**Stores to Create:**
1. **`panelStore.js`** — Panel state (positions, sizes, visibility, dock state)
2. **`layoutStore.js`** — Layout presets, active layout, workspace-specific layouts
3. **`themeStore.js`** — Active theme, theme customization, motion preferences
4. **`workspaceStore.js`** — Workspace CRUD, active workspace, workspace settings

**Rationale:**
- Better separation of concerns
- Optimized re-renders (only subscribe to relevant state)
- Easier to test each store independently
- Clear ownership boundaries

**Existing Store:**
- Keep `useStore.js` for auth, file tree, chat messages, terminal, settings (existing patterns)
- Gradually migrate UI-specific state to specialized stores

---

### ✅ Migration Strategy
**Decision: Big Bang Rewrite**

**Approach:**
- Build complete panel system foundation (Stage 1)
- Replace `App.jsx` with new `DockableLayout.jsx`
- Migrate all components to panels in one coordinated effort
- Remove old layout system entirely

**Risk Mitigation:**
- Comprehensive testing of panel system before migration
- Backup current working version in git
- Implement all core panels simultaneously to maintain feature parity
- Keep old components as reference during migration

---

## Implementation Roadmap

### Stage 1: Foundation (53 tasks)
**Goal:** Establish design system, panel manager, and application shell

**Critical Path:**
1. Design tokens & theme engine (7 tasks)
2. Panel manager core (9 tasks)
3. Layout preset system (7 tasks)
4. Responsive layout system (6 tasks)
5. Accessibility foundation (7 tasks)
6. Command palette enhancement (7 tasks)
7. Migration strategy (10 tasks)

**Deliverables:**
- ✅ 16 built-in themes with customization
- ✅ Dockable/floating/pinnable panel system
- ✅ 6 workspace presets (Builder, Developer, Debug, Operations, AI Collaboration, Presentation)
- ✅ Responsive breakpoints (mobile/tablet/desktop)
- ✅ Full keyboard accessibility
- ✅ Enhanced command palette with panel management
- ✅ All existing components wrapped as panels

**Key Components:**
```
src/theme/
  ├── tokens.js              # Design tokens
  ├── ThemeContext.jsx        # React Context
  ├── themeRegistry.js        # Theme registration
  └── motion.js               # Motion tokens

src/store/
  ├── panelStore.js           # Panel state
  ├── layoutStore.js          # Layout presets
  ├── themeStore.js           # Theme state
  └── workspaceStore.js       # Workspace management

src/components/panels/
  ├── Panel.jsx               # Base panel component
  ├── PanelContainer.jsx      # Panel manager
  └── DockZone.jsx            # Docking zones

src/components/layout/
  ├── DockableLayout.jsx      # Main layout (replaces App.jsx)
  └── ThreeStateSidebar.jsx   # Expanded/Compact/Hidden

src/hooks/
  ├── useResponsive.js        # Breakpoint detection
  └── useKeyboardShortcuts.js # Shortcut registry
```

---

### Stage 2: Shell & Navigation (18 tasks)
**Goal:** Build the operating system frame

**Deliverables:**
- Three-state sidebar (Expanded, Compact, Hidden)
- Top mission bar with status indicators
- Workspace switcher
- Global keyboard shortcut system

**Key Components:**
```
src/components/sidebar/
  └── ThreeStateSidebar.jsx   # Raycast-style sidebar

src/components/layout/
  └── MissionBar.jsx          # Top status bar

src/hooks/
  └── useGlobalShortcuts.js   # Centralized shortcuts
```

---

### Stage 3: Workflow Canvas (20 tasks)
**Goal:** Create the spatial orchestration experience (heart of system)

**Deliverables:**
- Infinite zoomable canvas with pan/zoom
- Node graph engine with 6 node types
- Living nodes with 6 animation states (Idle, Thinking, Executing, Waiting, Success, Failed)
- Inline metrics (CPU, RAM, Queue, Memory, Latency)
- Execution controls (play/pause/stop, step-through)

**Key Components:**
```
src/components/workflow/
  ├── InfiniteCanvas.jsx      # Pan/zoom canvas
  ├── Node.jsx                # Base node
  ├── LivingNode.jsx          # Animated node
  ├── Edge.jsx                # Connections
  └── NodeLogs.jsx            # Inline logs
```

---

### Stage 4: IDE Integration (13 tasks)
**Goal:** Seamlessly integrate coding

**Deliverables:**
- Modular IDE with dockable sub-panels (Explorer, Editor, Problems, Outline, Git, Debugger, Terminal, AI)
- Enhanced Monaco editor with multi-file tabs
- Inline AI actions (explain, refactor, test generation)
- Floating editor windows

**Key Components:**
```
src/components/ide/
  ├── IDEPanel.jsx            # IDE container
  ├── FloatingEditor.jsx      # Detachable editor
  └── AIActions.jsx           # Inline AI menu
```

---

### Stage 5: Execution & Debugging (14 tasks)
**Goal:** Make runtime feel alive

**Deliverables:**
- Smart Terminal with dual modes (Smart/Manual)
- Node log streaming
- Debug inspector with breakpoints, stack traces, variable inspector
- Resource monitoring

**Key Components:**
```
src/components/terminal/
  └── SmartTerminal.jsx       # Dual-mode terminal

src/components/debug/
  └── DebugInspector.jsx      # Debug panel
```

---

### Stage 6: AI Collaboration (11 tasks)
**Goal:** Make AI feel like teammates

**Deliverables:**
- Flexible chat with 6 modes (Docked, Floating, Bottom, Inline, Detached, Bubble)
- Agent memory viewer
- Multi-agent collaboration panel

**Key Components:**
```
src/components/chat/
  └── ChatPanel.jsx           # Multi-mode chat

src/components/agent/
  ├── MemoryViewer.jsx        # Memory inspection
  └── AgentCollaboration.jsx  # Multi-agent view
```

---

### Stage 7: Polish & Delight (30 tasks)
**Goal:** Premium feel

**Deliverables:**
- Purposeful motion system (no decorative animations)
- Theme customization UI
- Onboarding & guided tours
- Performance optimization
- Plugin API

**Key Components:**
```
src/components/settings/
  └── ThemeCustomizer.jsx     # Theme controls

src/components/onboarding/
  └── OnboardingWizard.jsx    # Interactive tutorials
```

---

## Technical Architecture

### Panel State Schema
```javascript
{
  id: string,                    // Unique panel ID
  type: string,                  // Panel type (workflow, ide, chat, etc.)
  state: 'docked' | 'floating' | 'pinned' | 'hidden' | 'fullscreen',
  dock: 'top' | 'bottom' | 'left' | 'right' | 'center' | null,
  position: { x: number, y: number },  // For floating panels
  size: { width: number, height: number },
  config: {},                    // Panel-specific configuration
  zIndex: number                 // For floating panels
}
```

### Layout Preset Schema
```javascript
{
  name: string,
  description: string,
  panels: [
    {
      id: string,
      type: string,
      dock: string,
      size: { width: number, height: number },
      config: {}
    }
  ]
}
```

### Built-in Layout Presets

**Builder**
- Workflow Canvas: 75%
- Chat: 25%
- IDE: hidden

**Developer**
- IDE: 70%
- Workflow: 20%
- Terminal: 10%

**Debug**
- Terminal: 50%
- IDE: 30%
- Workflow: 20%

**Operations**
- Metrics, Workers, Deployments, Logs, Alerts

**AI Collaboration**
- Chat, Workflow, Memory, Planning

**Presentation**
- Clean, Minimal, Readonly, No sidebars

---

## Theme System

### 16 Built-in Themes
1. Midnight (dark, blue accents)
2. Aurora (dark, gradient accents)
3. Carbon (dark, neutral)
4. Graphite (dark, gray tones)
5. Nebula (dark, purple/blue)
6. Ocean (dark, blue/teal)
7. Nord (dark, nord palette)
8. Tokyo (dark, cyberpunk)
9. Solar (dark, warm tones)
10. Light (light, neutral)
11. Warm Light (light, warm tones)
12. Cream (light, cream/beige)
13. Sepia (light, sepia tones)
14. OLED (pure black for OLED screens)
15. Developer Green (dark, green accents)
16. Purple (dark, purple accents)
17. Glass (transparent with blur)

### Theme Customization API
```javascript
{
  accent: string,              // Primary accent color
  borderRadius: number,        // Border radius (px)
  glowIntensity: number,       // Glow effect strength (0-1)
  blur: number,                // Background blur (px)
  density: 'compact' | 'comfortable' | 'spacious',
  typographyScale: number,     // Font size multiplier
  animationSpeed: number       // Animation duration multiplier
}
```

---

## Motion System

### Animation States for Living Nodes
- **Idle:** Soft breathing animation (subtle opacity/scale pulse)
- **Thinking:** Gentle pulse (faster breathing)
- **Executing:** Data flowing animation (particle flow along edges)
- **Waiting:** Dim state (reduced opacity)
- **Success:** Green ripple (expanding ring)
- **Failed:** Red glow (pulsing red border)

### Purposeful Animations
- Open IDE: Camera zoom effect
- Close IDE: Camera zoom out
- Click Node: Other panels soften (opacity reduction)
- Logs: Flow from node (slide animation)
- Workflow: Pan with inertia (momentum scrolling)
- Terminal: Slide naturally (smooth slide)

**Principle:** Motion should explain, never decorate.

---

## Accessibility Requirements

### Keyboard Navigation
- All panel operations accessible via keyboard
- Focus management for dockable panels
- Screen reader announcements for state changes
- High-contrast theme support

### Keyboard Shortcuts
```
Ctrl/Cmd + K          Open command palette
Ctrl/Cmd + `          Toggle terminal
Ctrl/Cmd + Shift + L  Toggle chat
Ctrl/Cmd + ,          Open settings
Ctrl/Cmd + B          Toggle sidebar
Ctrl/Cmd + \          Toggle panel fullscreen
Ctrl/Cmd + Shift + P  Dock panel right
Ctrl/Cmd + Shift + O  Dock panel bottom
Ctrl/Cmd + Shift + I  Float panel
?                     Show keyboard cheatsheet
```

---

## Performance Targets

### Metrics
- Initial load: < 2s on 4G connection
- Panel drag: 60 FPS
- Theme switch: < 100ms
- Layout preset switch: < 500ms
- Memory usage: < 150MB idle

### Optimization Strategies
- Virtual scrolling for large lists
- Lazy loading for panels (load on first open)
- React.memo for panel components
- Unmount hidden panels (not just hide with CSS)
- Debounce panel resize events

---

## Testing Strategy

### Unit Tests
- Panel store operations
- Layout preset save/load
- Theme application
- Keyboard shortcuts

### Integration Tests
- Dock/float operations
- Panel resizing
- Layout preset switching
- Theme customization

### E2E Tests
- Complete workflow creation
- Multi-panel layout management
- Workspace switching

### Accessibility Tests
- axe-core integration
- Keyboard navigation audit
- Screen reader compatibility

---

## Dependencies

### New Dependencies to Add
```json
{
  "react-dnd": "^16.0.1",
  "react-dnd-html5-backend": "^16.0.1",
  "@xyflow/react": "^12.0.0"  // Optional: for workflow canvas
}
```

### Existing Dependencies to Leverage
- `react-resizable-panels` (already installed) — panel resizing
- `@monaco-editor/react` (already installed) — code editor
- `xterm` (already installed) — terminal
- `lucide-react` (already installed) — icons
- `zustand` (already installed) — state management

---

## File Structure

```
frontend-src/src/
├── theme/
│   ├── tokens.js
│   ├── ThemeContext.jsx
│   ├── themeRegistry.js
│   └── motion.js
├── store/
│   ├── useStore.js (existing, keep for auth/settings)
│   ├── panelStore.js (new)
│   ├── layoutStore.js (new)
│   ├── themeStore.js (new)
│   └── workspaceStore.js (new)
├── components/
│   ├── panels/
│   │   ├── Panel.jsx
│   │   ├── PanelContainer.jsx
│   │   └── DockZone.jsx
│   ├── layout/
│   │   ├── DockableLayout.jsx (replaces App.jsx)
│   │   ├── ThreeStateSidebar.jsx
│   │   └── MissionBar.jsx
│   ├── workflow/
│   │   ├── InfiniteCanvas.jsx
│   │   ├── Node.jsx
│   │   ├── LivingNode.jsx
│   │   ├── Edge.jsx
│   │   └── NodeLogs.jsx
│   ├── ide/
│   │   ├── IDEPanel.jsx
│   │   └── FloatingEditor.jsx
│   ├── terminal/
│   │   └── SmartTerminal.jsx
│   ├── chat/
│   │   └── ChatPanel.jsx
│   ├── agent/
│   │   ├── MemoryViewer.jsx
│   │   └── AgentCollaboration.jsx
│   └── settings/
│       └── ThemeCustomizer.jsx
├── hooks/
│   ├── useResponsive.js
│   ├── useKeyboardShortcuts.js
│   └── useGlobalShortcuts.js
└── App.jsx (refactored to use DockableLayout)
```

---

## Implementation Checklist

### Stage 1: Foundation
- [ ] Design tokens & theme engine (7 tasks)
- [ ] Panel manager core (9 tasks)
- [ ] Layout preset system (7 tasks)
- [ ] Responsive layout system (6 tasks)
- [ ] Accessibility foundation (7 tasks)
- [ ] Command palette enhancement (7 tasks)
- [ ] Migration strategy (10 tasks)

**Total: 53 tasks**

### Stage 2: Shell & Navigation
- [ ] Three-state sidebar (6 tasks)
- [ ] Mission bar (4 tasks)
- [ ] Workspace switcher (5 tasks)
- [ ] Keyboard shortcuts (4 tasks)

**Total: 18 tasks**

### Stage 3: Workflow Canvas
- [ ] Infinite canvas (5 tasks)
- [ ] Node graph engine (5 tasks)
- [ ] Living nodes (5 tasks)
- [ ] Execution controls (4 tasks)

**Total: 20 tasks**

### Stage 4: IDE Integration
- [ ] Modular IDE (4 tasks)
- [ ] Monaco enhancements (5 tasks)
- [ ] Floating editors (4 tasks)

**Total: 13 tasks**

### Stage 5: Execution & Debugging
- [ ] Smart terminal (4 tasks)
- [ ] Node logs (4 tasks)
- [ ] Debug inspector (5 tasks)

**Total: 14 tasks**

### Stage 6: AI Collaboration
- [ ] Flexible chat (4 tasks)
- [ ] Memory viewer (4 tasks)
- [ ] Agent collaboration (4 tasks)

**Total: 11 tasks**

### Stage 7: Polish & Delight
- [ ] Motion system (3 tasks)
- [ ] Theme customization (4 tasks)
- [ ] Onboarding (3 tasks)
- [ ] Performance (4 tasks)
- [ ] Plugin API (4 tasks)
- [ ] Documentation (4 tasks)
- [ ] Testing (4 tasks)

**Total: 30 tasks**

**Grand Total: 159 tasks**

---

## Next Steps

1. **Install Dependencies**
   ```bash
   cd frontend-src
   npm install react-dnd react-dnd-html5-backend
   ```

2. **Begin Stage 1 Implementation**
   - Start with design tokens (`src/theme/tokens.js`)
   - Create panel store (`src/store/panelStore.js`)
   - Build base Panel component (`src/components/panels/Panel.jsx`)

3. **Testing Strategy**
   - Set up testing framework (Jest + React Testing Library)
   - Write tests for panel store before implementation
   - Test panel operations incrementally

4. **Documentation**
   - Document panel architecture as it's built
   - Create migration guide for component developers
   - Update README with new architecture

---

## Success Criteria

### Stage 1 Complete When:
- ✅ All 16 themes work with customization
- ✅ Panels can dock, float, pin, hide, resize, fullscreen
- ✅ 6 layout presets save/load/switch
- ✅ Responsive on mobile/tablet/desktop
- ✅ Full keyboard accessibility
- ✅ Command palette manages panels
- ✅ All existing components work as panels

### Project Complete When:
- ✅ All 7 stages delivered
- ✅ Performance targets met
- ✅ Accessibility audit passed
- ✅ Documentation complete
- ✅ User testing validated

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Panel system too complex | High | Start with simple dock/float, add advanced features incrementally |
| Performance issues with many panels | Medium | Implement virtual scrolling, lazy loading, panel unmounting |
| Migration breaks existing features | High | Comprehensive testing, keep old components as reference |
| Theme system conflicts with existing CSS | Medium | Gradual CSS migration, use CSS custom properties |
| Drag-and-drop feels clunky | Medium | Polish drag handles, add visual feedback, test on multiple devices |

---

## Conclusion

This plan transforms DevOS into a premium AI Operating System with the flexibility of VS Code, the spatial navigation of Figma, and the polish of Arc Browser. The big bang rewrite approach is aggressive but achievable with the clear architectural decisions made:

- **Custom panel system** for maximum control
- **Separate stores** for clean state management
- **7-stage roadmap** for organized delivery

**Ready to begin Stage 1 implementation.**

---

*This plan is a living document. Update as implementation progresses and requirements evolve.*
