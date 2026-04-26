from autoteam import manager


class _FakeChatGPT:
    def __init__(self):
        self.browser = True
        self.started = 0
        self.stopped = 0

    def start(self):
        self.browser = True
        self.started += 1

    def stop(self):
        self.browser = False
        self.stopped += 1


class _FakeMailClient:
    def login(self):
        return None


def test_cmd_fill_tries_other_reusable_accounts_before_creating_new(monkeypatch):
    chatgpt = _FakeChatGPT()
    count_values = iter([4, 5])
    events = []

    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [
            {"email": "old-1@example.com", "_quota_recovered": True},
            {"email": "old-2@example.com", "_quota_recovered": True},
        ],
    )

    def fake_reinvite(_chatgpt, _mail, acc):
        events.append(("reinvite", acc["email"]))
        return acc["email"] == "old-2@example.com"

    monkeypatch.setattr(manager, "reinvite_account", fake_reinvite)
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or True,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync", None)))
    monkeypatch.setattr(manager, "cmd_status", lambda: events.append(("status", None)))

    manager.cmd_fill(target=5)

    assert events == [
        ("reinvite", "old-1@example.com"),
        ("reinvite", "old-2@example.com"),
        ("sync", None),
        ("status", None),
    ]
    assert chatgpt.stopped == 1


def test_cmd_fill_skips_google_accounts_during_auto_reuse(monkeypatch):
    chatgpt = _FakeChatGPT()
    count_values = iter([4, 5])
    events = []

    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [
            {"email": "bubblehuntr@gmail.com", "_quota_recovered": True},
            {"email": "old-2@example.com", "_quota_recovered": True},
        ],
    )

    def fake_reinvite(_chatgpt, _mail, acc):
        events.append(("reinvite", acc["email"]))
        return True

    monkeypatch.setattr(manager, "reinvite_account", fake_reinvite)
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or True,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync", None)))
    monkeypatch.setattr(manager, "cmd_status", lambda: events.append(("status", None)))

    manager.cmd_fill(target=5)

    assert events == [
        ("reinvite", "old-2@example.com"),
        ("sync", None),
        ("status", None),
    ]
    assert chatgpt.stopped == 1


def test_cmd_fill_stops_early_when_refreshed_team_count_hits_target(monkeypatch):
    chatgpt = _FakeChatGPT()
    count_values = iter([3, 5])
    events = []

    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(manager, "get_standby_accounts", lambda: [])
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or False,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync", None)))
    monkeypatch.setattr(manager, "cmd_status", lambda: events.append(("status", None)))

    manager.cmd_fill(target=5)

    assert events == [
        ("create", None),
        ("sync", None),
        ("status", None),
    ]
    assert chatgpt.stopped == 1


def test_auto_reuse_skip_reason_detects_google_provider_and_gmail():
    assert manager._auto_reuse_skip_reason({"email": "bubblehuntr@gmail.com"}) == "Google 登录账号暂不支持自动复用"
    assert (
        manager._auto_reuse_skip_reason({"email": "user@example.com", "login_provider": "google"})
        == "Google 登录账号暂不支持自动复用"
    )
    assert manager._auto_reuse_skip_reason({"email": "user@example.com"}) is None


def test_check_skips_auto_relogin_when_cloudflare_mailbox_is_missing(monkeypatch):
    events = []

    monkeypatch.setattr(
        manager,
        "load_accounts",
        lambda: [
            {
                "email": "tmp8g57lftukz3@xumin.fun",
                "password": "",
                "email_provider": "cloudflare_temp_email",
                "mailbox": None,
                "status": manager.STATUS_ACTIVE,
                "auth_file": None,
            }
        ],
    )
    monkeypatch.setattr(manager, "sync_account_states", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "cmd_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "sync_to_cpa", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manager, "CloudMailClient", lambda *args, **kwargs: events.append("mail_client"))
    monkeypatch.setattr(manager, "login_codex_via_browser", lambda *args, **kwargs: events.append("login"))

    manager.cmd_check()

    assert events == []
