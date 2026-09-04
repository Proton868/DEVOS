"""Monaco init contract: singleton AMD path, same-origin vs, no CDN."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = (ROOT / "frontend-src" / "src" / "monacoSetup.js").read_text(encoding="utf-8")


def test_same_origin_vs_path():
    assert '/static/monaco/vs' in SETUP
    assert "cdn.jsdelivr" not in SETUP
    assert "cdnjs" not in SETUP


def test_singleton_load_monaco():
    assert "function loadMonaco" in SETUP or "export function loadMonaco" in SETUP
    assert "monacoPromise" in SETUP
    assert "vs/editor/editor.main" in SETUP


def test_injects_loader_script():
    assert "loader.js" in SETUP
    assert "injectScript" in SETUP or "createElement" in SETUP


def test_assets_on_disk():
    vs = ROOT / "frontend" / "static" / "monaco" / "vs"
    assert (vs / "loader.js").is_file()
    assert (vs / "editor" / "editor.main.js").is_file()
    assert (vs / "base" / "worker" / "workerMain.js").is_file()
