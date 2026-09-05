"""Frontend deploy integrity: index + main chunk map must match on-disk assets."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
INDEX = FRONTEND / "templates" / "index.html"
STATIC = FRONTEND / "static"


def test_index_html_exists():
    assert INDEX.is_file()


def test_index_refs_exist_on_disk():
    html = INDEX.read_text(encoding="utf-8")
    refs = re.findall(r"/static/[^\"']+", html)
    assert refs, "index.html has no /static refs"
    missing = []
    for r in refs:
        rel = r[1:] if r.startswith("/") else r
        if not (ROOT / "frontend" / rel.replace("static/", "static/", 1)).exists():
            # path is frontend/static/...
            p = FRONTEND / r.lstrip("/").removeprefix("static/")
            # r is /static/js/foo — file at frontend/static/js/foo
            p = FRONTEND / r.lstrip("/")
            if not p.exists():
                missing.append(r)
    assert not missing, f"index references missing files: {missing}"


def test_main_bundle_chunks_exist():
    js_dir = STATIC / "js"
    mains = [
        p
        for p in js_dir.glob("main.*.js")
        if not p.name.endswith(".LICENSE.txt") and ".map" not in p.name
    ]
    assert mains, "no main.*.js"
    missing = []
    for main in mains:
        text = main.read_text(encoding="utf-8", errors="replace")
        for num, h in re.findall(r'(\d+):"([a-f0-9]{8})"', text):
            chunk = js_dir / f"{num}.{h}.chunk.js"
            if not chunk.is_file():
                missing.append(chunk.name)
    assert not missing, f"main references missing chunks: {missing}"


def test_stale_chunk_133_present_if_referenced():
    """The production failure was 133.0d0d5ace missing while referenced by main."""
    js_dir = STATIC / "js"
    main = next(
        p
        for p in js_dir.glob("main.*.js")
        if not p.name.endswith(".LICENSE.txt") and ".map" not in p.name
    )
    text = main.read_text(encoding="utf-8", errors="replace")
    if "133" in text and "0d0d5ace" in text:
        assert (js_dir / "133.0d0d5ace.chunk.js").is_file()


def test_deploy_script_validates_chunks():
    script = (ROOT / "frontend-src" / "scripts" / "deploy-frontend.mjs").read_text(
        encoding="utf-8"
    )
    assert "collectMainChunkRefs" in script or "chunkRefs" in script
    assert "atomic" in script.lower() or "renameSync" in script
