from brain.personas import surface_intent_for_message


def test_conversation_stays_chat():
    si = surface_intent_for_message("What is dependency injection?")
    assert si["surface"] == "chat"
    assert si["action"] == "none"


def test_build_opens_ide():
    si = surface_intent_for_message("Build a React login page for this project.")
    assert si["surface"] == "ide"
    assert si["required"] is True


def test_automation_opens_flow():
    si = surface_intent_for_message("Create an automation that emails when a GitHub issue opens")
    assert si["surface"] == "flow"


def test_does_not_authorize():
    si = surface_intent_for_message("deploy to production")
    # intent is presentation only — no capability grant fields
    assert "capabilities" not in si
    assert "authorization" not in si
