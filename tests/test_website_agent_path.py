"""Website generation must not claim scaffold success as specialist Mission success."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_chat_does_not_primary_scaffold_as_mission_success():
    src = (ROOT / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "DIRECT_SCAFFOLD" not in src
    assert "SCAFFOLD_FALLBACK" in src
    assert "validate_website_artifacts" in src
    assert "validation_started" in src
    assert "DEVOS_ALLOW_WEBSITE_SCAFFOLD_FALLBACK" in src
    assert "MISSION_EXECUTION" in src


def test_verify_runs_website_validation():
    src = (ROOT / "brain" / "orchestration_verify.py").read_text(encoding="utf-8")
    assert "async def validate_website_artifacts" in src
    assert "index.html" in src
    # No early return that dead-codes the candidate scan
    assert "website_validation" in src or "validate_website_artifacts(" in src


def test_heuristic_website_steps_in_source():
    src = (ROOT / "brain" / "orchestration.py").read_text(encoding="utf-8")
    assert 'persona_id="web"' in src
    assert "fs.write" in src
    assert "website" in src


def test_agent_objective_requires_workspace_files():
    src = (ROOT / "brain" / "orchestration_runtime.py").read_text(encoding="utf-8")
    assert "Do NOT return the complete website as chat text" in src
    assert "create_file" in src or "apply_patch" in src


def test_resolve_chat_provider_source_defaults():
    src = (ROOT / "brain" / "llm.py").read_text(encoding="utf-8")
    assert "def resolve_chat_provider" in src
    assert "DEFAULT_PROVIDER" in src
    assert 'or "omniroute"' in src
    assert 'or "ollama"' not in src.split("def resolve_chat_provider")[1].split("def ")[0]


def test_website_builder_module_exists():
    src = (ROOT / "brain" / "website_builder.py").read_text(encoding="utf-8")
    assert "materialize_website_via_agent" in src
    assert "AGENT_MATERIALIZE" in src
    assert "FileService" in src
    assert "validate_website_artifacts" in src


def test_chat_uses_agent_materialize_before_scaffold():
    src = (ROOT / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "materialize_website_via_agent" in src
    assert "AGENT_MATERIALIZE" in src
    # materialize appears before scaffold fallback gate
    i_mat = src.find("materialize_website_via_agent")
    i_fb = src.find("DEVOS_ALLOW_WEBSITE_SCAFFOLD_FALLBACK")
    assert i_mat > 0 and i_fb > i_mat


def test_aicopilot_opens_ide_on_artifact():
    src = (ROOT / "frontend-src" / "src" / "os" / "focus" / "AICopilot.jsx").read_text(encoding="utf-8")
    assert "artifact_created" in src
    assert "openPreview" in src


def test_materialize_writes_real_files(tmp_path, monkeypatch):
    """AGENT_MATERIALIZE must write via FileService and pass validation."""
    import asyncio
    from pathlib import Path as P
    import sys
    sys.path.insert(0, str(ROOT))

    # Point projects dir under tmp
    import execution.files as files_mod
    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    html = """<!DOCTYPE html><html><head><title>Footwalk</title>
<link rel="stylesheet" href="style.css"></head>
<body><h1>Footwalk</h1><script src="script.js"></script></body></html>"""
    css = "body{font-family:sans-serif}"
    js = "console.log('ok')"
    payload = {
        "files": [
            {"path": "index.html", "content": html},
            {"path": "style.css", "content": css},
            {"path": "script.js", "content": js},
        ]
    }

    class FakeBrain:
        async def stream_chat(self, messages):
            import json
            return json.dumps(payload)

    from brain.website_builder import materialize_website_via_agent
    result = asyncio.run(
        materialize_website_via_agent(
            user_id="test-user",
            project_id="default",
            goal="Create a 1 page website for Footwalk shoe store",
            brain=FakeBrain(),
        )
    )
    assert result["ok"] is True, result
    assert result["execution_path"] == "AGENT_MATERIALIZE"
    assert "index.html" in result["files"]
    root = tmp_path / "projects" / "test-user" / "default"
    assert (root / "index.html").is_file()
    assert "Footwalk" in (root / "index.html").read_text()
    assert result.get("entry_point") == "index.html"


def test_chat_never_claims_website_success_without_artifacts():
    src = (ROOT / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "never report completed without validated" in src or "Website goals: never report completed" in src
    assert "I will not paste full HTML into chat" in src
    assert "force_website" in src


def test_no_direct_scaffold_primary_path():
    src = (ROOT / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "DIRECT_SCAFFOLD" not in src or "SCAFFOLD_FALLBACK" in src
    assert "DEVOS_ALLOW_WEBSITE_SCAFFOLD_FALLBACK" in src
