"""Nuha executive role: conversation vs delegated execution."""
from brain.nuha_role import NuhaRole, classify_nuha_role
from brain.nuha_bridge import should_auto_orchestrate, is_trivial_chat
from brain.personas import should_orchestrate_execution
from brain.orchestration import detect_mode, NuhaMode


def test_greetings_are_conversation():
    d = classify_nuha_role("hello")
    assert d.role == NuhaRole.CONVERSATION
    assert d.should_execute_tools is False
    assert should_auto_orchestrate("hello") is False


def test_advice_question_not_execution():
    for msg in [
        "what is pytest?",
        "How does React useEffect work?",
        "explain the difference between var and let",
        "should I use FastAPI or Flask?",
    ]:
        d = classify_nuha_role(msg)
        assert d.role == NuhaRole.CONVERSATION, msg
        assert d.should_delegate is False, msg
        assert should_auto_orchestrate(msg) is False, msg
        assert should_orchestrate_execution(msg) is False, msg


def test_planning_without_tools():
    d = classify_nuha_role("plan a rollout strategy for migrating to microservices")
    assert d.role == NuhaRole.PLANNING
    assert d.plan_only is True
    assert d.should_execute_tools is False
    assert d.should_delegate is False
    assert should_auto_orchestrate("plan a rollout strategy for migrating to microservices") is False


def test_coding_execution_delegates():
    for msg in [
        "create a one page website for a shoe store",
        "fix the failing authentication tests",
        "implement input validation on the signup form",
        "build a vite project called dashboard",
    ]:
        d = classify_nuha_role(msg)
        assert d.role == NuhaRole.EXECUTION, msg
        assert d.should_delegate is True, msg
        assert d.should_execute_tools is True, msg
        assert should_auto_orchestrate(msg) is True, msg


def test_verification_role():
    d = classify_nuha_role("verify the last build and re-run tests")
    assert d.role == NuhaRole.VERIFICATION
    assert d.should_delegate is True


def test_reporting_no_tools():
    d = classify_nuha_role("what is the status of the last mission?")
    assert d.role == NuhaRole.REPORTING
    assert d.should_execute_tools is False
    assert should_auto_orchestrate("what is the status of the last mission?") is False


def test_destructive_requires_confirm():
    d = classify_nuha_role("delete all user data from the database")
    assert d.destructive is True
    assert d.requires_explicit_confirm is True
    # still classified as execution if verb present, but confirm required
    assert d.role == NuhaRole.EXECUTION


def test_detect_mode_alignment():
    assert detect_mode("what is docker?") == NuhaMode.CHAT
    assert detect_mode("plan an API versioning strategy") == NuhaMode.PLAN
    assert detect_mode("implement the login endpoint") == NuhaMode.ACTION


def test_conversation_separated_from_execution_state():
    """Conversation decisions must not look like completed missions."""
    d = classify_nuha_role("tell me about unit testing")
    assert d.should_orchestrate is False
    assert d.should_delegate is False
    assert not is_trivial_chat("tell me about unit testing")  # substantive but non-exec
