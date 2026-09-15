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
