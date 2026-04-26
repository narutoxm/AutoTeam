from autoteam import accounts, mail_provider, mailbox_store
from autoteam.cloudflare_temp_email import CloudflareTempEmailClient


def test_detect_email_provider_for_address_uses_configured_domains(monkeypatch):
    monkeypatch.setattr(mail_provider, "CLOUDFLARE_TEMP_EMAIL_DOMAINS", ["cfe.example.com"])
    monkeypatch.setattr(mail_provider, "CLOUDMAIL_DOMAIN", "@cloudmail.example.com")

    assert mail_provider.detect_email_provider_for_address("user@cfe.example.com") == "cloudflare_temp_email"
    assert mail_provider.detect_email_provider_for_address("user@cloudmail.example.com") == "cloudmail"
    assert mail_provider.detect_email_provider_for_address("user@other.example.com") is None


def test_cloudflare_temp_email_search_uses_saved_mailbox(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    mailbox_store_file = tmp_path / "mailboxes.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)
    monkeypatch.setattr(mailbox_store, "MAILBOX_STORE_FILE", mailbox_store_file)
    accounts.save_accounts(
        [
            {
                "email": "user@cfe.example.com",
                "password": "",
                "cloudmail_account_id": None,
                "email_provider": "cloudflare_temp_email",
                "mailbox": {
                    "address": "user@cfe.example.com",
                    "jwt_token": "jwt-token",
                    "mailbox_password": "",
                },
                "status": accounts.STATUS_PENDING,
                "auth_file": None,
                "quota_exhausted_at": None,
                "quota_resets_at": None,
                "created_at": 0,
                "last_active_at": None,
            }
        ]
    )

    client = CloudflareTempEmailClient(api_base="https://worker.example.com")
    monkeypatch.setattr(
        client,
        "list_messages",
        lambda jwt_token: [{"id": "12", "subject": "Your ChatGPT code", "from": "noreply@tm.openai.com"}],
    )
    monkeypatch.setattr(
        client,
        "read_message",
        lambda jwt_token, mail_id: {
            "id": "12",
            "subject": "Your ChatGPT code",
            "from": "noreply@tm.openai.com",
            "text": "Your ChatGPT code is 123456",
        },
    )

    emails = client.search_emails_by_recipient("user@cfe.example.com", size=5)

    assert len(emails) == 1
    assert emails[0]["messageId"] == "12"
    assert emails[0]["sendEmail"] == "noreply@tm.openai.com"
    assert client.extract_verification_code(emails[0]) == "123456"


def test_get_account_mailbox_falls_back_to_mailbox_store(tmp_path, monkeypatch):
    mailbox_store_file = tmp_path / "mailboxes.json"
    monkeypatch.setattr(mailbox_store, "MAILBOX_STORE_FILE", mailbox_store_file)

    mailbox_store.save_mailbox(
        "user@cfe.example.com",
        "cloudflare_temp_email",
        {
            "address": "user@cfe.example.com",
            "jwt_token": "jwt-token",
            "mailbox_password": "",
        },
    )

    mailbox = mail_provider.get_account_mailbox(
        {
            "email": "user@cfe.example.com",
            "email_provider": "cloudflare_temp_email",
            "mailbox": None,
            "cloudmail_account_id": None,
        }
    )

    assert mailbox["jwt_token"] == "jwt-token"


def test_cloudflare_temp_email_wait_for_code_skips_invite(monkeypatch):
    client = CloudflareTempEmailClient(api_base="https://worker.example.com")
    client._runtime_mailboxes["user@cfe.example.com"] = {
        "address": "user@cfe.example.com",
        "jwt_token": "jwt-token",
        "mailbox_password": "",
    }
    monkeypatch.setattr(
        client,
        "list_messages",
        lambda jwt_token: [
            {"id": "1", "subject": "Workspace invitation"},
            {"id": "2", "subject": "Your verification code"},
        ],
    )
    monkeypatch.setattr(
        client,
        "read_message",
        lambda jwt_token, mail_id: {
            "1": {"id": "1", "subject": "Workspace invitation", "text": "invite"},
            "2": {"id": "2", "subject": "Your verification code", "text": "验证码: 112233"},
        }[str(mail_id)],
    )

    code, message_key = client.wait_for_code("user@cfe.example.com", timeout=1, poll_interval=0)

    assert code == "112233"
    assert message_key == "2"


def test_cloudflare_temp_email_extracts_chinese_temp_code_before_html_colors():
    client = CloudflareTempEmailClient(api_base="https://worker.example.com")
    email = {
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

    assert client.extract_verification_code(email) == "536291"


def test_cloudflare_temp_email_wait_for_code_ignores_existing_messages(monkeypatch):
    client = CloudflareTempEmailClient(api_base="https://worker.example.com")
    client._runtime_mailboxes["user@cfe.example.com"] = {
        "address": "user@cfe.example.com",
        "jwt_token": "jwt-token",
        "mailbox_password": "",
    }
    calls = 0

    def fake_list_messages(jwt_token):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [
                {"id": "10846", "subject": "Jennifer Johnson has invited you"},
                {"id": "10845", "subject": "您的临时ChatGPT验证码"},
            ]
        return [
            {"id": "10847", "subject": "您的临时ChatGPT验证码"},
            {"id": "10846", "subject": "Jennifer Johnson has invited you"},
            {"id": "10845", "subject": "您的临时ChatGPT验证码"},
        ]

    def fake_read_message(jwt_token, mail_id):
        return {
            "10847": {"id": "10847", "subject": "您的临时ChatGPT验证码", "text": "输入此临时验证码以继续：\n\n536291"},
            "10846": {"id": "10846", "subject": "Jennifer Johnson has invited you", "text": "invite"},
            "10845": {"id": "10845", "subject": "您的临时ChatGPT验证码", "text": "327496"},
        }[str(mail_id)]

    monkeypatch.setattr(client, "list_messages", fake_list_messages)
    monkeypatch.setattr(client, "read_message", fake_read_message)

    code, message_key = client.wait_for_code(
        "user@cfe.example.com",
        timeout=1,
        poll_interval=0,
        ignore_message_keys={"10845", "10846"},
    )

    assert code == "536291"
    assert message_key == "10847"


def test_cloudflare_temp_email_wait_for_code_reads_detail_not_list_snippet(monkeypatch):
    client = CloudflareTempEmailClient(api_base="https://worker.example.com")
    client._runtime_mailboxes["user@cfe.example.com"] = {
        "address": "user@cfe.example.com",
        "jwt_token": "jwt-token",
        "mailbox_password": "",
    }
    monkeypatch.setattr(
        client,
        "list_messages",
        lambda jwt_token: [
            {
                "id": "10849",
                "subject": "您的临时ChatGPT验证码",
                "raw": "stale list preview 329272",
            }
        ],
    )
    monkeypatch.setattr(
        client,
        "read_message",
        lambda jwt_token, mail_id: {
            "id": "10849",
            "subject": "您的临时ChatGPT验证码",
            "metadata": '{"text":"OpenAI\\n\\n输入此临时验证码以继续：\\n\\n536291"}',
        },
    )

    code, message_key = client.wait_for_code("user@cfe.example.com", timeout=1, poll_interval=0)

    assert code == "536291"
    assert message_key == "10849"


def test_cloudflare_temp_email_wait_for_code_parses_raw_mime_body(monkeypatch):
    client = CloudflareTempEmailClient(api_base="https://worker.example.com")
    client._runtime_mailboxes["user@cfe.example.com"] = {
        "address": "user@cfe.example.com",
        "jwt_token": "jwt-token",
        "mailbox_password": "",
    }
    raw_message = (
        "DKIM-Signature: header-random=329272\r\n"
        "Subject: 您的临时ChatGPT验证码\r\n"
        "Content-Type: text/html; charset=UTF-8\r\n"
        "\r\n"
        "<html><body><p>输入此临时验证码以继续：</p><p>536291</p></body></html>"
    )
    monkeypatch.setattr(
        client,
        "list_messages",
        lambda jwt_token: [{"id": "10849", "raw": "list preview 111111"}],
    )
    monkeypatch.setattr(
        client,
        "read_message",
        lambda jwt_token, mail_id: {"id": "10849", "raw": raw_message},
    )

    code, message_key = client.wait_for_code("user@cfe.example.com", timeout=1, poll_interval=0)

    assert code == "536291"
    assert message_key == "10849"


def test_cloudflare_temp_email_extract_invite_link_from_raw_mime_html():
    client = CloudflareTempEmailClient(api_base="https://worker.example.com")
    invite_link = (
        "https://chatgpt.com/auth/login?inv_ws_name=AI+Team+Workspace"
        "&inv_email=user%40cfe.example.com&next=%2Fcodex%2Fcloud"
        "&wId=workspace-id&accept_wId=workspace-id"
    )
    raw_message = (
        "Subject: Jennifer Johnson has invited you to use Codex\r\n"
        "Content-Type: multipart/alternative; boundary=abc\r\n"
        "\r\n"
        "--abc\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        "Start using Codex\r\n"
        "--abc\r\n"
        "Content-Type: text/html; charset=UTF-8\r\n"
        "\r\n"
        f'<html><body><a href="{invite_link.replace("&", "&amp;")}">Start using Codex</a></body></html>\r\n'
        "--abc--\r\n"
    )

    extracted = client.extract_invite_link({"detail": {"raw": raw_message}})

    assert extracted == invite_link
