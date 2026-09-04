"""Monaco must be served as real JS from /static/monaco/vs (not SPA/404)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "frontend" / "static" / "monaco" / "vs" / "loader.js"


def test_monaco_loader_exists_on_disk():
    assert LOADER.is_file(), f"missing {LOADER} — run deploy/vendor monaco"
    body = LOADER.read_text(encoding="utf-8", errors="replace")
    assert len(body) > 500
    assert "define" in body or "require" in body or "monaco" in body.lower()


def test_monaco_tree_has_editor_main():
    main = ROOT / "frontend" / "static" / "monaco" / "vs" / "editor" / "editor.main.js"
    assert main.is_file(), f"missing {main}"


def test_monaco_setup_uses_same_origin_path():
    setup = (ROOT / "frontend-src" / "src" / "monacoSetup.js").read_text(encoding="utf-8")
    assert "/static/monaco/vs" in setup
    assert "cdn.jsdelivr" not in setup
