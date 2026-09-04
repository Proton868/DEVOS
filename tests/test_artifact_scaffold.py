"""Website creation should produce workspace files, not chat-only dumps."""
from brain.artifact_scaffold import _brand_from_goal, _is_website_goal, render_static_site_html
from brain.personas import classify_intent_heuristic, should_orchestrate_execution, surface_intent_for_message


def test_website_goal_detection():
    assert _is_website_goal("create a 1 page website for a shoe business named Footwalk")
    assert not _is_website_goal("what is the weather today")


def test_brand_extraction():
    assert "Footwalk" in _brand_from_goal("shoe business named Footwalk")


def test_html_contains_brand():
    html = render_static_site_html("Footwalk", "shoe store")
    assert "Footwalk" in html
    assert "<!DOCTYPE html>" in html


def test_creation_triggers_orchestrate():
    msg = "I want to create a 1 page website for a shoe business named Footwalk"
    assert "CREATION" in classify_intent_heuristic(msg)
    assert should_orchestrate_execution(msg) is True
    si = surface_intent_for_message(msg)
    assert si["surface"] == "ide"
    assert si["context"].get("filePath") == "index.html"


def test_ide_launch_not_external_editors():
    msg = "Launch an IDE environment for coding"
    classes = classify_intent_heuristic(msg)
    assert "IDE" in classes or "EXECUTION" in classes
    si = surface_intent_for_message(msg)
    assert si["surface"] == "ide"
