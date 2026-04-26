from autoteam import accounts, cloudmail


def test_resolve_account_id_falls_back_to_account_list(monkeypatch):
    client = cloudmail.LegacyCloudMailClient()
    target_email = "tmp-user@example.com"

    monkeypatch.setattr(accounts, "load_accounts", lambda: [])
    monkeypatch.setattr(
        client,
        "list_accounts",
        lambda size=200: [
            {"accountId": 43, "email": target_email},
        ],
    )

    assert client._resolve_account_id_for_email(target_email.upper()) == 43


def test_search_emails_by_recipient_falls_back_to_email_latest(monkeypatch):
    client = cloudmail.LegacyCloudMailClient()
    target_email = "tmp-user@example.com"
    calls = []

    monkeypatch.setattr(accounts, "load_accounts", lambda: [])

    def fake_get(path, params=None):
        calls.append((path, params))
        if path == "/account/list":
            return {
                "code": 200,
                "data": [
                    {"accountId": 43, "email": target_email},
                ],
            }
        if path == "/email/list":
            return {
                "code": 200,
                "data": {
                    "list": [],
                    "total": 0,
                    "latestEmail": {"emailId": 15, "accountId": 43, "userId": 1},
                },
            }
        if path == "/email/latest":
            return {
                "code": 200,
                "data": [
                    {
                        "emailId": 15,
                        "accountId": 43,
                        "sendEmail": "noreply@tm.openai.com",
                        "subject": "Your ChatGPT code is 189799",
                        "content": "Your ChatGPT code is 189799",
                    }
                ],
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(client, "_get", fake_get)

    emails = client.search_emails_by_recipient(target_email, size=5)

    assert len(emails) == 1
    assert emails[0]["emailId"] == 15
    assert emails[0]["subject"] == "Your ChatGPT code is 189799"
    assert ("/email/latest", {"emailId": 0, "accountId": 43, "allReceive": 0}) in calls


def test_search_emails_by_recipient_ignores_unrelated_global_results(monkeypatch):
    client = cloudmail.LegacyCloudMailClient()
    target_email = "tmp-user@example.com"

    monkeypatch.setattr(accounts, "load_accounts", lambda: [])

    def fake_get(path, params=None):
        if path == "/account/list":
            return {
                "code": 200,
                "data": [
                    {"accountId": 43, "email": target_email},
                ],
            }
        if path == "/email/list":
            return {
                "code": 200,
                "data": {
                    "list": [],
                    "total": 0,
                },
            }
        if path == "/email/latest":
            return {
                "code": 200,
                "data": [],
            }
        if path == "/allEmail/list":
            return {
                "code": 200,
                "data": {
                    "list": [
                        {
                            "emailId": 410,
                            "accountId": 999,
                            "accountEmail": "someone-else@example.com",
                            "sendEmail": "noreply@tm.openai.com",
                            "subject": "Your ChatGPT code is 202123",
                            "content": "Your ChatGPT code is 202123",
                        }
                    ]
                },
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(client, "_get", fake_get)

    emails = client.search_emails_by_recipient(target_email, size=5)

    assert emails == []


def test_search_emails_by_recipient_accepts_global_results_for_matching_account(monkeypatch):
    client = cloudmail.LegacyCloudMailClient()
    target_email = "tmp-user@example.com"

    monkeypatch.setattr(accounts, "load_accounts", lambda: [])

    def fake_get(path, params=None):
        if path == "/account/list":
            return {
                "code": 200,
                "data": [
                    {"accountId": 43, "email": target_email},
                ],
            }
        if path == "/email/list":
            return {
                "code": 200,
                "data": {
                    "list": [],
                    "total": 0,
                },
            }
        if path == "/email/latest":
            return {
                "code": 200,
                "data": [],
            }
        if path == "/allEmail/list":
            return {
                "code": 200,
                "data": {
                    "list": [
                        {
                            "emailId": 411,
                            "accountId": 43,
                            "sendEmail": "noreply@tm.openai.com",
                            "subject": "Your ChatGPT code is 654321",
                            "content": "Your ChatGPT code is 654321",
                        }
                    ]
                },
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(client, "_get", fake_get)

    emails = client.search_emails_by_recipient(target_email, size=5)

    assert [email["emailId"] for email in emails] == [411]


def test_extract_verification_code_prefers_visible_text_over_html_color_values():
    client = cloudmail.LegacyCloudMailClient()

    email_data = {
        "text": None,
        "content": """
        <html>
          <head>
            <title>Your ChatGPT code is 676952</title>
            <style>
              .top { color: #202123; }
              .body { color: #353740; }
            </style>
          </head>
          <body>
            <p>Your ChatGPT code is 676952</p>
          </body>
        </html>
        """,
    }

    assert client.extract_verification_code(email_data) == "676952"


def test_extract_verification_code_uses_plain_text_when_available():
    client = cloudmail.LegacyCloudMailClient()

    email_data = {
        "text": "Your temporary OpenAI login code is 123456",
        "content": "<html><style>.top{color:#202123}</style><body>ignored</body></html>",
    }

    assert client.extract_verification_code(email_data) == "123456"


def test_extract_verification_code_ignores_html_color_before_chinese_code():
    client = cloudmail.LegacyCloudMailClient()

    email_data = {
        "text": "",
        "content": """
        <html>
          <head><style>.brand { color: #327496; }</style></head>
          <body>
            <p>输入此临时验证码以继续：</p>
            <p>536291</p>
          </body>
        </html>
        """,
    }

    assert client.extract_verification_code(email_data) == "536291"


def test_wait_for_code_skips_invites_and_returns_login_code(monkeypatch):
    client = cloudmail.LegacyCloudMailClient()

    messages = [
        {
            "emailId": 11,
            "subject": "You're invited to a ChatGPT Team workspace",
            "text": "invite mail",
        },
        {
            "emailId": 12,
            "subject": "Your verification code",
            "text": "Verification code: 654321",
        },
    ]

    monkeypatch.setattr(client, "search_emails_by_recipient", lambda *args, **kwargs: messages)

    code, message_key = client.wait_for_code("user@example.com", timeout=1, poll_interval=0, skip_invites=True)

    assert code == "654321"
    assert message_key == "12"
