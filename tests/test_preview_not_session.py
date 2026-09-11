"""Preview tokens must not authenticate as session JWTs."""
from api.routes.auth import (
    make_jwt,
    make_preview_token,
    decode_local_token,
    decode_preview_token,
    PREVIEW_TOKEN_TYP,
)


def test_preview_token_rejected_by_decode_local_token():
    preview = make_preview_token("user-a", "ws-1", ttl_seconds=120)
    assert decode_preview_token(preview["token"]) is not None
    assert decode_local_token(preview["token"]) is None


def test_session_jwt_still_decodes():
    session = make_jwt("user-a")
    payload = decode_local_token(session)
    assert payload is not None
    assert payload["sub"] == "user-a"
    assert payload.get("typ") in (None, "session")
    assert payload.get("typ") != PREVIEW_TOKEN_TYP
