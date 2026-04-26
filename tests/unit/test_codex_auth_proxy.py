import requests

from autoteam import codex_auth


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {
            "access_token": "access",
            "refresh_token": "refresh",
            "id_token": "a.eyJlbWFpbCI6InVzZXJAZXhhbXBsZS5jb20iLCJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjIiwgImNoYXRncHRfcGxhbl90eXBlIjoicGx1cyJ9fQ.c",
            "expires_in": 3600,
        }
        self.text = ""

    def json(self):
        return self._payload


def test_exchange_auth_code_uses_requests_proxy(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeResponse()

    monkeypatch.setattr(codex_auth, "get_requests_proxy_dict", lambda: {"http": "http://proxy:8080", "https": "http://proxy:8080"})
    monkeypatch.setattr(requests, "post", fake_post)

    bundle = codex_auth._exchange_auth_code("auth-code", "code-verifier", fallback_email="user@example.com")

    assert bundle is not None
    assert captured["kwargs"]["proxies"] == {"http": "http://proxy:8080", "https": "http://proxy:8080"}


def test_refresh_access_token_uses_requests_proxy(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeResponse(payload={"access_token": "new-access", "refresh_token": "new-refresh", "id_token": "", "expires_in": 3600})

    monkeypatch.setattr(codex_auth, "get_requests_proxy_dict", lambda: {"http": "http://proxy:8080", "https": "http://proxy:8080"})
    monkeypatch.setattr(requests, "post", fake_post)

    token = codex_auth.refresh_access_token("refresh-token")

    assert token is not None
    assert captured["kwargs"]["proxies"] == {"http": "http://proxy:8080", "https": "http://proxy:8080"}


def test_check_codex_quota_uses_requests_proxy(monkeypatch):
    captured = {}

    class _QuotaResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "rate_limit": {
                    "primary_window": {"used_percent": 10, "reset_at": 111},
                    "secondary_window": {"used_percent": 20, "reset_at": 222},
                    "limit_reached": False,
                }
            }

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _QuotaResponse()

    monkeypatch.setattr(codex_auth, "get_requests_proxy_dict", lambda: {"http": "http://proxy:8080", "https": "http://proxy:8080"})
    monkeypatch.setattr(requests, "get", fake_get)

    status, info = codex_auth.check_codex_quota("access-token", account_id="acc-1")

    assert status == "ok"
    assert info["primary_pct"] == 10
    assert captured["kwargs"]["proxies"] == {"http": "http://proxy:8080", "https": "http://proxy:8080"}
    assert captured["kwargs"]["headers"]["Chatgpt-Account-Id"] == "acc-1"
