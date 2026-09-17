from brain.provider_routing import classify_provider_failure, FailureClass, _retry_after_seconds


class _Resp:
    def __init__(self, code, headers=None):
        self.status_code = code
        self.headers = headers or {}
        self.text = "rate limit"


class _Exc(Exception):
    def __init__(self, code, headers=None):
        self.response = _Resp(code, headers)
        super().__init__(f"HTTP {code}")


def test_429_retryable_with_retry_after():
    fc = classify_provider_failure(_Exc(429, {"Retry-After": "3"}))
    assert fc.category == "rate_limited"
    assert fc.retryable is True
    assert fc.retry_after_s == 3.0


def test_auth_not_retryable():
    fc = classify_provider_failure(_Exc(401))
    assert fc.retryable is False
