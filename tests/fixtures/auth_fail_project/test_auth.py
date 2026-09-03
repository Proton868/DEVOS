"""Failing auth test for Stage 3I acceptance fixture."""
from auth import authenticate

def test_authenticate_accepts_valid_password():
    assert authenticate("alice", "s3cret") is True

def test_authenticate_rejects_wrong_password():
    assert authenticate("alice", "wrong") is False
