/**
 * DevOS InfiniteCanvas
 * Pan/zoom infinite canvas for the workflow editor.
 * Uses transform-based panning and zooming for smooth 60fps.
 */
import React, { useRef, useState, useCallback, useEffect } from "react";
import { useStore } from "../../store/useStore";

const MIN_ZOOM = 0.2;
const MAX_ZOOM = 3;
const ZOOM_SENSITIVITY = 0.001;
const PAN_SENSITIVITY = 1;

export default function InfiniteCanvas({ children, width = "100%", height = "100%" }) {
  const [transform, setTransform] = useState({ x: 0, y: 0, zoom: 1 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const canvasRef = useRef(null);

  const handleWheel = useCallback((e) => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      // Zoom
      const delta = -e.deltaY * ZOOM_SENSITIVITY;
      const newZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, transform.zoom + delta));
      // Zoom toward cursor
      const rect = canvasRef.current.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;
      const zoomRatio = newZoom / transform.zoom;
      setTransform((t) => ({
        x: mouseX - (mouseX - t.x) * zoomRatio,
        y: mouseY - (mouseY - t.y) * zoomRatio,
        zoom: newZoom,
      }));
    } else {
      // Pan
      setTransform((t) => ({
        ...t,
        x: t.x - e.deltaX * PAN_SENSITIVITY,
        y: t.y - e.deltaY * PAN_SENSITIVITY,
      }));
    }
  }, [transform]);

  const handleMouseDown = useCallback((e) => {
    if (e.button === 0 || e.button === 1) {
      // Left or middle click to pan
      setIsPanning(true);
      setPanStart({ x: e.clientX - transform.x, y: e.clientY - transform.y });
    }
  }, [transform]);

  const handleMouseMove = useCallback((e) => {
    if (!isPanning) return;
    setTransform((t) => ({
      ...t,
      x: e.clientX - panStart.x,
      y: e.clientY - panStart.y,
    }));
  }, [isPanning, panStart]);

  const handleMouseUp = useCallback(() => {
    setIsPanning(false);
  }, []);

  const zoomIn = useCallback(() => {
    setTransform((t) => ({ ...t, zoom: Math.min(MAX_ZOOM, t.zoom + 0.2) }));
  }, []);

  const zoomOut = useCallback(() => {
    setTransform((t) => ({ ...t, zoom: Math.max(MIN_ZOOM, t.zoom - 0.2) }));
  }, []);

  const resetView = useCallback(() => {
    setTransform({ x: 0, y: 0, zoom: 1 });
  }, []);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  return (
    <div
      ref={canvasRef}
      className="devos-canvas-container"
      style={{ width, height, cursor: isPanning ? "grabbing" : "grab" }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      role="application"
      aria-label="Workflow canvas — drag to pan, Ctrl+scroll to zoom"
    >
      {/* Grid background */}
      <div
        className="devos-canvas-grid"
        style={{
          backgroundPosition: `${transform.x}px ${transform.y}px`,
          backgroundSize: `${20 * transform.zoom}px ${20 * transform.zoom}px`,
        }}
      />
      {/* Transform layer */}
      <div
        className="devos-canvas-transform"
        style={{
          transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.zoom})`,
          transformOrigin: "0 0",
        }}
      >
        {children}
      </div>
      {/* Zoom controls */}
      <div className="devos-canvas-controls">
        <button onClick={zoomOut} title="Zoom out" aria-label="Zoom out">−</button>
        <button onClick={resetView} title="Reset view" aria-label="Reset view" className="devos-canvas-zoom-label">
          {Math.round(transform.zoom * 100)}%
        </button>
        <button onClick={zoomIn} title="Zoom in" aria-label="Zoom in">+</button>
      </div>
    </div>
  );
}
