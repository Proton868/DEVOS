"""Website builds must use A2A Web Agent path — not website_builder."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_website_builder_removed():
    assert not (ROOT / "brain" / "website_builder.py").exists()


def test_chat_uses_a2a_delegation_not_materialize():
    src = (ROOT / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "run_delegated_mission" in src
    assert "A2A_DELEGATION" in src
    assert "materialize_website_via_agent" not in src
    assert "AGENT_MATERIALIZE" not in src


def test_scaffold_is_env_gated_fallback_only():
    src = (ROOT / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "DEVOS_ALLOW_WEBSITE_SCAFFOLD_FALLBACK" in src
