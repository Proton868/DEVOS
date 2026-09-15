/**
 * NuhaEdgeControls — Flow + IDE workspace controls above the Nuha composer.
 * Default: horizontal [ Flow ] [ IDE ] just above the message bar.
 * Unlocked: independently draggable within the Nuha surface; positions persist.
 */
import React, { useCallback, useRef, useState } from "react";
import { Code2, Workflow, Lock, Unlock } from "lucide-react";
import useOsStore from "../store/osStore";

const STORAGE_KEY = "devos_nuha_edge_controls";

const DEFAULTS = {
  locked: true,
  flow: { x: null, y: null },
  ide: { x: null, y: null },
};

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw);
    return {
      locked: parsed.locked !== false,
      flow: parsed.flow && typeof parsed.flow === "object" ? parsed.flow : DEFAULTS.flow,
      ide: parsed.ide && typeof parsed.ide === "object" ? parsed.ide : DEFAULTS.ide,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

function saveState(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (_) {}
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

/**
 * @param {{ hostRef: React.RefObject<HTMLElement|null> }} props
 */
export default function NuhaEdgeControls({ hostRef }) {
  const setFocusCollapsed = useOsStore((s) => s.setFocusCollapsed);
  const openEditor = useOsStore((s) => s.openEditor);
  const setActiveWorkspace = useOsStore((s) => s.setActiveWorkspace);
  const closeEditor = useOsStore((s) => s.closeEditor);

  const [state, setState] = useState(loadState);
  const dragRef = useRef(null);
  const suppressClickRef = useRef(false);
  const stripRef = useRef(null);

  const persist = useCallback((next) => {
    setState(next);
    saveState(next);
  }, []);

  const toggleLock = useCallback(
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      persist({ ...state, locked: !state.locked });
    },
    [persist, state]
  );

  const openFlow = useCallback(
    (e) => {
      if (suppressClickRef.current) {
        suppressClickRef.current = false;
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      // Reveal Flow/canvas workspace
      try {
        closeEditor?.();
      } catch (_) {}
      setFocusCollapsed?.(true);
      setActiveWorkspace?.("canvas");
    },
    [setFocusCollapsed, setActiveWorkspace, closeEditor]
  );

  const openIde = useCallback(
    (e) => {
      if (suppressClickRef.current) {
        suppressClickRef.current = false;
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      setFocusCollapsed?.(false);
      openEditor?.({ file: "index.html" });
      setActiveWorkspace?.("ide");
    },
    [openEditor, setActiveWorkspace, setFocusCollapsed]
  );

  const onPointerDown = useCallback(
    (which, e) => {
      if (state.locked) return;
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const host = hostRef?.current;
      if (!host) return;
      const hostRect = host.getBoundingClientRect();
      const btn = e.currentTarget;
      const btnRect = btn.getBoundingClientRect();
      const origin = state[which] || {};
      const baseLeft =
        origin.x != null ? origin.x : btnRect.left - hostRect.left;
      const baseTop =
        origin.y != null ? origin.y : btnRect.top - hostRect.top;

      dragRef.current = {
        which,
        startX: e.clientX,
        startY: e.clientY,
        baseLeft,
        baseTop,
        hostRect,
        btnW: btnRect.width,
        btnH: btnRect.height,
        moved: false,
        pointerId: e.pointerId,
      };
      try {
        btn.setPointerCapture(e.pointerId);
      } catch (_) {}
    },
    [hostRef, state]
  );

  const onPointerMove = useCallback((e) => {
    const d = dragRef.current;
    if (!d || d.which == null) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
    d.moved = true;
    const maxX = Math.max(0, d.hostRect.width - d.btnW - 4);
    const maxY = Math.max(0, d.hostRect.height - d.btnH - 4);
    const x = clamp(d.baseLeft + dx, 0, maxX);
    const y = clamp(d.baseTop + dy, 0, maxY);
    setState((prev) => ({
      ...prev,
      [d.which]: { x, y },
    }));
  }, []);

  const onPointerUp = useCallback((e) => {
    const d = dragRef.current;
    if (!d) return;
    dragRef.current = null;
    try {
      e.currentTarget.releasePointerCapture?.(d.pointerId);
    } catch (_) {}
    if (d.moved) {
      suppressClickRef.current = true;
      setState((prev) => {
        saveState(prev);
        return prev;
      });
    }
  }, []);

  const flowCustom = state.flow?.x != null && state.flow?.y != null;
  const ideCustom = state.ide?.x != null && state.ide?.y != null;

  const flowStyle = flowCustom
    ? {
        position: "absolute",
        left: state.flow.x,
        top: state.flow.y,
        right: "auto",
        bottom: "auto",
        zIndex: 8,
      }
    : undefined;
  const ideStyle = ideCustom
    ? {
        position: "absolute",
        left: state.ide.x,
        top: state.ide.y,
        right: "auto",
        bottom: "auto",
        zIndex: 8,
      }
    : undefined;

  // Portal-style absolute buttons when customized; strip holds defaults + lock
  return (
    <>
      <div
        ref={stripRef}
        className={`sp-nuha-ws-controls ${state.locked ? "is-locked" : "is-unlocked"}`}
        aria-label="Nuha workspace controls"
      >
        {!flowCustom && (
          <button
            type="button"
            className="sp-nuha-ws-btn sp-nuha-ws-flow"
            title={state.locked ? "Open Flow" : "Flow — drag to move"}
            aria-label="Open Flow"
            onClick={openFlow}
            onPointerDown={(e) => onPointerDown("flow", e)}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          >
            <Workflow size={14} />
            <span>Flow</span>
          </button>
        )}
        {!ideCustom && (
          <button
            type="button"
            className="sp-nuha-ws-btn sp-nuha-ws-ide"
            title={state.locked ? "Open IDE" : "IDE — drag to move"}
            aria-label="Open IDE"
            onClick={openIde}
            onPointerDown={(e) => onPointerDown("ide", e)}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          >
            <Code2 size={14} />
            <span>IDE</span>
          </button>
        )}
        <button
          type="button"
          className="sp-nuha-ws-lock"
          title={state.locked ? "Unlock position" : "Lock position"}
          aria-label={state.locked ? "Unlock Flow and IDE positions" : "Lock Flow and IDE positions"}
          aria-pressed={state.locked}
          onClick={toggleLock}
        >
          {state.locked ? <Lock size={12} /> : <Unlock size={12} />}
        </button>
      </div>
      {flowCustom && (
        <button
          type="button"
          className="sp-nuha-ws-btn sp-nuha-ws-flow is-floating"
          style={flowStyle}
          title={state.locked ? "Open Flow" : "Flow — drag to move"}
          aria-label="Open Flow"
          onClick={openFlow}
          onPointerDown={(e) => onPointerDown("flow", e)}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <Workflow size={14} />
          <span>Flow</span>
        </button>
      )}
      {ideCustom && (
        <button
          type="button"
          className="sp-nuha-ws-btn sp-nuha-ws-ide is-floating"
          style={ideStyle}
          title={state.locked ? "Open IDE" : "IDE — drag to move"}
          aria-label="Open IDE"
          onClick={openIde}
          onPointerDown={(e) => onPointerDown("ide", e)}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <Code2 size={14} />
          <span>IDE</span>
        </button>
      )}
    </>
  );
}
