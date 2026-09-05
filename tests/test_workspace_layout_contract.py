"""Workspace layout contracts: collapse/restore affordances exist in source."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OS = ROOT / "frontend-src" / "src" / "os"


def test_layout_state_in_store():
    t = (OS / "store" / "osStore.js").read_text(encoding="utf-8")
    assert "fleetCollapsed" in t
    assert "focusCollapsed" in t
    assert "filesDrawerOpen" in t
    assert "hydrateLayout" in t
    assert "setFocusCollapsed" in t


def test_spatial_workspace_edge_dock():
    t = (OS / "workspace" / "SpatialWorkspace.jsx").read_text(encoding="utf-8")
    assert "sp-edge-dock" in t
    assert "setFocusCollapsed" in t
    assert "sp-focus-resizer" in t


def test_ide_has_files_and_collapse():
    t = (OS / "focus" / "DevOSIde.jsx").read_text(encoding="utf-8")
    assert "sp-ide-files" in t
    assert "onCollapse" in t
    assert "openPreview" in t


def test_fleet_collapsible():
    t = (OS / "dashboard" / "AgencyDashboard.jsx").read_text(encoding="utf-8")
    assert "sp-fleet-chip" in t
    assert "setFleetCollapsed" in t
