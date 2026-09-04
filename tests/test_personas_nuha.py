"""Persona / Nuha orchestration layer — unit tests (no network)."""
from brain.personas import (
    DEFAULT_PERSONA_ID,
    NUHA,
    get_persona,
    list_personas,
    resolve_system_prompt,
    specialist_in_domain,
    suggest_personas_for_goal,
    classify_intent_heuristic,
    should_orchestrate_execution,
)


def test_nuha_is_default():
    assert DEFAULT_PERSONA_ID == "nuha"
    assert get_persona(None).id == "nuha"
    assert get_persona("nuha") is NUHA
    assert NUHA.can_delegate is True
    assert NUHA.role == "orchestrator"


def test_list_personas_nuha_first():
    items = list_personas()
    assert items[0].id == "nuha"
    ids = {p.id for p in items}
    assert "web" in ids and "code" in ids and "automation" in ids


def test_specialist_domain_boundaries():
    assert specialist_in_domain("web", "build a website about shoes")
    assert not specialist_in_domain("web", "draft a legal contract for incorporation")
    assert specialist_in_domain("nuha", "anything")


def test_system_prompt_contains_nuha():
    prompt = resolve_system_prompt("nuha")
    assert "Nuha" in prompt
    assert "UCIP" in prompt


def test_intent_classification_creation():
    classes = classify_intent_heuristic("Create a one page website about shoes")
    assert "CREATION" in classes
    assert should_orchestrate_execution("Create a one page website about shoes")
    assert not should_orchestrate_execution("what is a steelpan?")


def test_suggest_personas_for_website():
    suggested = suggest_personas_for_goal("Create a website with React components")
    assert "web" in suggested or "code" in suggested
