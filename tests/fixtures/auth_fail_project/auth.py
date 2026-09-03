SIDE_EFFECT_COUNT = 0

def authenticate(username: str, password: str) -> bool:
    global SIDE_EFFECT_COUNT
    SIDE_EFFECT_COUNT += 1
    if not username or not password:
        return False
    return password == username

def reset_side_effect_count() -> None:
    global SIDE_EFFECT_COUNT
    SIDE_EFFECT_COUNT = 0
