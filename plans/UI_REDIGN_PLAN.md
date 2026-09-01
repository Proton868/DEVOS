# UI Redesign Plan: Midnight Obsidian + Automation Switcher

## 1. Goal in context

Replace the current DevOS layout/theme with a **single玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃玻璃", "Midnight Obsidian" glassmorphism UI and add a **Flow/Automation hub** with two views:
- Graph View (n8n-style dark canvas with mock nodes/edges)
- PyRunner Matrix View (datatable of real backend scripts)

The right side keeps the Cursor-style IDE + Chat pinned. Row clicks update a global active script context so the IDE opens the file.

## 2. Current state

- `frontend-src` uses **plain CSS files** (`devos.css`, `devos-extra.css`, `devos-premium.css`, `devos-light.css`) + inline styles. No Tailwind is installed yet.
- Backend routes already exist:
  - `GET /api/scripts` → list scripts (id, name, language, schedule_type, is_active, tags, retry_policy, notify_on_success, notify_on_failure)
  - `GET /api/scripts/{id}` → full script incl. `code`
  - `GET /api/scripts/{id}/runs` → last 20 runs (id, status, exit_code, duration_ms, trigger, started_at, stdout, stderr)
  - `POST /api/scripts/{id}/run`, `PATCH`, etc.
  - No dedicated `venv` field exists on the `Script` model. The closest concept is language-specific package installs tracked via `api/marketplace/install` but not stored on the script record.

## 3. Tailwind strategy

Option A: Add Tailwind CSS to CRA (postcss + autoprefixer + tailwindcss, custom `tailwind.config.js`, update build scripts). This is the cleanest long-term path but is a **bigger build-tooling change** and can conflict with existing CSS files if not namespaced.

Option B: Implement the design system using existing **CSS custom properties and utility classes**, without adding Tailwind. This is faster, avoids build-tool risk, and preserves the existing production CSS pipeline.

**Recommendation: Option B for this pass.** We can still deliver the exact colors, `backdrop-blur`, and glass-panel aesthetic purely with CSS, and avoid the weeks of Tailwind integration risk. We can revisit Tailwind in a future dedicated build-tooling session.

## 4. Component/file plan

| File | Purpose |
|------|---------|
| `frontend-src/src/App.jsx` | Swap to 3-column layout: `GlobalSidebar` (left), `Workspace` (center), `RightDock` (IDE + Chat). Keep auth/HITL toasts. |
| `frontend-src/src/components/layout/GlobalSidebar.jsx` | Slim left rail: logo, primary nav icons (Builder, Flow, Workers, Git, Settings). |
| `frontend-src/src/components/layout/Workspace.jsx` | Center stage. Houses the Automation Switcher and whichever tool is active. |
| `frontend-src/src/components/layout/RightDock.jsx` | Cursor-style right panel: code editor + chat tabs, resizable. |
| `frontend-src/src/components/automation/AutomationHub.jsx` | Top toggle between Graph/Matrix + state management. |
| `frontend-src/src/components/automation/GraphCanvas.jsx` | Dark grid SVG canvas with mock nodes (`GitHub Hook`, `Code Reviewer AI`, `PyRunner Exec`) and glowing Neon Mint edges. |
| `frontend-src/src/components/automation/PyRunnerMatrix.jsx` | Datatable with cols: Script Name, Status (pulse), Venv/Packages (derived from `language`/tags), Schedule, Last Run. Fetches from `/api/scripts` + `/api/scripts/{id}/runs`. |
| `frontend-src/src/components/automation/StatusBadge.jsx` | Reusable running/idle/failed badge with Neon Mint pulse. |
| `frontend-src/src/devos.css` | Add Midnight Obsidian tokens and `.glass`, `.glass-panel`, `.neon-*` utility classes. |
| `frontend-src/src/store/useStore.js` | Add `activeScriptId`, `activeScriptCode`, `openScriptInEditor(script)`. |
| `frontend-src/src/services/api.js` | Add `getScript(id)` if not already present (verify). |

## 5. Data shapes for the matrix

Per script row:
```js
{
  id,
  name,
  language,       // "python" | "node" | "bash"
  schedule_type,  // "manual" | "interval" | "cron"
  schedule_value, // cron expr or seconds
  is_active,
  lastRun: { status, started_at, duration_ms }, // from GET /api/scripts/{id}/runs[0]
  venv: "python" ? "system" : "node" ? "npm" : "sh", // inferred; real venv not stored today
}
```

Click handler:
1. `api.getScript(row.id)` → get `code`.
2. `useStore.getState().openScriptInEditor({ path: row.name, content: code, language: row.language })`.
3. Right dock switches to editor tab.

## 6. Open questions

1. **Tailwind vs. CSS-only:** Do you want me to add real Tailwind now, or implement the look with existing CSS to avoid build-tool churn?
2. **Scope of layout replacement:** Should the new 3-column layout replace the entire current `App.jsx` layout (including resizable file-tree/terminal), or only the **center workspace** while preserving the existing resizable panels?
3. **Real venv column:** The backend doesn't store a venv name yet. Should I infer it from `language`, or add a small backend change to expose it?
4. **Graph node interactivity:** Should mock nodes be clickable (e.g., selecting a node filters the matrix) purely visual?

## 7. Suggested first implementation slice

1. Add CSS tokens + utility classes (`glass`, `neon-mint`, etc.).
2. Build `AutomationHub` with `GraphCanvas` and `PyRunnerMatrix` behind a real toggle.
3. Wire matrix to backend; add store action to open a script in the IDE.
4. Swap `App.jsx` to the 3-column layout using existing `react-resizable-panels` (no new deps).
5. Test matrix with mocked fetch + build passes.
6. Update `record.md`.

Approve this plan or answer the questions above and I'll start coding.