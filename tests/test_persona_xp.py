from brain.persona_xp import calculate_level, calculate_xp_to_next_level, XP_RULES


def test_level_zero():
    assert calculate_level(0) == 1


def test_level_thresholds():
    assert calculate_level(99) == 1
    assert calculate_level(100) == 2
    assert calculate_level(250) == 3


def test_xp_to_next():
    p = calculate_xp_to_next_level(0)
    assert p["level"] == 1
    assert p["xp_to_next"] == 100
    assert 0 <= p["progress"] <= 1


def test_xp_rules_no_arbitrary():
    assert "task_completed" in XP_RULES
    assert all(isinstance(v, int) and v > 0 for v in XP_RULES.values())


def test_rename_does_not_change_id():
    from brain.personas import get_persona
    p = get_persona("web")
    assert p.id == "web"
