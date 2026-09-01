/**
 * DevOS DockZone — Drop target for docking panels.
 * Shows visual feedback when a panel is dragged over it.
 */
import React from "react";
import { useDrop } from "react-dnd";
import { PANEL_DRAG_TYPE } from "./Panel";
import { usePanelStore } from "../../store/panelStore";

export const DockZone = ({ position, children, className = "" }) => {
  const { dockPanel, draggingPanelId, dragOverDock } = usePanelStore();

  const [{ isOver, canDrop }, dropRef] = useDrop(() => ({
    accept: PANEL_DRAG_TYPE,
    drop: (item) => {
      dockPanel(item.id, position);
    },
    collect: (monitor) => ({
      isOver: monitor.isOver(),
      canDrop: monitor.canDrop(),
    }),
  }), [position, dockPanel]);

  const showIndicator = draggingPanelId && (isOver || dragOverDock === position);

  return (
    <div
      ref={dropRef}
      className={`devos-dock-zone devos-dock-${position} ${className} ${showIndicator ? "drop-active" : ""}`}
      data-dock-position={position}
    >
      {showIndicator && (
        <div className="devos-dock-indicator" aria-hidden="true">
          <div className="devos-dock-indicator-inner" />
        </div>
      )}
      {children}
    </div>
  );
};

/**
 * FloatingDockZone — captures drops anywhere on the layout (for floating).
 */
export const FloatingDockZone = ({ children }) => {
  const { floatPanel, draggingPanelId } = usePanelStore();

  const [{ isOver }, dropRef] = useDrop(() => ({
    accept: PANEL_DRAG_TYPE,
    drop: (item, monitor) => {
      const offset = monitor.getClientOffset();
      if (offset) {
        floatPanel(item.id, { x: offset.x - 100, y: offset.y - 20 });
      } else {
        floatPanel(item.id, { x: 200, y: 150 });
      }
    },
    collect: (monitor) => ({
      isOver: monitor.isOver({ shallow: true }),
    }),
  }), [floatPanel]);

  return (
    <div
      ref={dropRef}
      className={`devos-floating-dock-zone ${draggingPanelId ? "active" : ""} ${isOver ? "over" : ""}`}
    >
      {children}
    </div>
  );
};

export default DockZone;
