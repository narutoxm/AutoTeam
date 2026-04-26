from autoteam import manager


class _FakeChatGPT:
    def __init__(self):
        self.browser = True
        self.stopped = 0

    def stop(self):
        self.browser = False
        self.stopped += 1


def test_create_new_account_prefers_invite_flow(monkeypatch):
    chatgpt = _FakeChatGPT()

    monkeypatch.setattr(manager, "_check_pending_invites", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(manager, "create_account_via_invite", lambda *_args, **_kwargs: "new@example.com")
    monkeypatch.setattr(
        manager,
        "create_account_direct",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not fall back to direct registration")),
    )

    result = manager.create_new_account(chatgpt, object())

    assert result == "new@example.com"


def test_create_new_account_falls_back_to_direct_when_invite_flow_returns_none(monkeypatch):
    chatgpt = _FakeChatGPT()

    monkeypatch.setattr(manager, "_check_pending_invites", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(manager, "create_account_via_invite", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        manager,
        "create_account_direct",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not fall back when invite mode is available")),
    )

    result = manager.create_new_account(chatgpt, object())

    assert result is None


def test_create_new_account_uses_direct_mode_only_without_team_api(monkeypatch):
    monkeypatch.setattr(manager, "create_account_direct", lambda *_args, **_kwargs: "fallback@example.com")

    result = manager.create_new_account(None, object())

    assert result == "fallback@example.com"


def test_invite_to_team_falls_back_from_usage_based_to_default():
    calls = []

    class _ChatGPT:
        def invite_member(self, email, seat_type="usage_based"):
            calls.append((email, seat_type))
            if seat_type == "usage_based":
                return 200, {"errored_emails": [{"error": "Unable to invite user due to an error."}]}
            return 200, {"account_invites": [{"email_address": email}]}

    assert manager.invite_to_team(_ChatGPT(), "user@example.com", seat_type="usage_based") is True
    assert calls == [
        ("user@example.com", "usage_based"),
        ("user@example.com", "default"),
    ]


def test_complete_registration_requires_team_membership_before_oauth(monkeypatch):
    monkeypatch.setattr("autoteam.invite.register_with_invite", lambda *_args, **_kwargs: (True, "secret"))
    monkeypatch.setattr(manager, "_is_email_in_team", lambda email: False)
    monkeypatch.setattr(manager.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        manager,
        "login_codex_via_browser",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not start oauth before team membership is confirmed")),
    )

    class _Browser:
        def new_context(self, **kwargs):
            class _Context:
                def new_page(self):
                    return object()

            return _Context()

        def close(self):
            return None

    class _Playwright:
        chromium = type("Chromium", (), {"launch": staticmethod(lambda **kwargs: _Browser())})()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _Playwright())

    result = manager._complete_registration("user@example.com", "secret", "https://invite", object())

    assert result is None
