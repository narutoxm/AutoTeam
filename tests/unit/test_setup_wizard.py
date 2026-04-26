import logging

from autoteam import setup_wizard


def test_write_env_uses_example_template_when_env_file_is_missing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_example = tmp_path / ".env.example"
    env_example.write_text(
        "EMAIL_PROVIDER=\nCLOUDMAIL_BASE_URL=\nCLOUDMAIL_EMAIL=\nAPI_KEY=\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(setup_wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE", env_example)

    setup_wizard._write_env("CLOUDMAIL_EMAIL", "admin@example.com")

    content = env_file.read_text(encoding="utf-8")
    assert "CLOUDMAIL_EMAIL=admin@example.com" in content
    assert "API_KEY=" in content


def test_check_and_setup_non_interactive_returns_true_when_required_values_exist(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "EMAIL_PROVIDER=cloudmail",
                "CLOUDMAIL_BASE_URL=http://mail.example.com",
                "CLOUDMAIL_EMAIL=admin@example.com",
                "CLOUDMAIL_PASSWORD=secret",
                "CLOUDMAIL_DOMAIN=@example.com",
                "CPA_URL=http://127.0.0.1:8317",
                "CPA_KEY=key-1",
                "API_KEY=generated-token",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(setup_wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: False)
    monkeypatch.setattr(setup_wizard, "_verify_email_provider", lambda provider=None: True)
    monkeypatch.setattr(setup_wizard, "_verify_cpa", lambda: True)
    for key in (
        "EMAIL_PROVIDER",
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    assert setup_wizard.check_and_setup(interactive=False) is True


def test_check_and_setup_non_interactive_reports_missing_required_fields(tmp_path, monkeypatch, caplog):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(setup_wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: False)
    for key in (
        "EMAIL_PROVIDER",
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    with caplog.at_level(logging.WARNING):
        ok = setup_wizard.check_and_setup(interactive=False)

    assert ok is False
    assert "[配置] 缺少必填项: EMAIL_PROVIDER" in caplog.text
    assert "[配置] 缺少必填项: CPA_KEY" in caplog.text
    assert "[配置] 缺少必填项: CPA_URL" in caplog.text
    assert "[配置] 缺少必填项: PLAYWRIGHT_PROXY_URL" not in caplog.text
    assert "[配置] 缺少必填项: PLAYWRIGHT_PROXY_BYPASS" not in caplog.text
    assert "[配置] 缺少必填项: API_KEY" in caplog.text
    assert "[配置] 请通过 Web 面板或编辑 .env 文件填入配置" in caplog.text


def test_get_setup_fields_includes_headless_option():
    fields = setup_wizard.get_setup_fields({"EMAIL_PROVIDER": "cloudflare_temp_email"})
    headless = next(field for field in fields if field["key"] == "PLAYWRIGHT_HEADLESS")

    assert headless["type"] == "select"
    assert headless["default"] == "false"
    assert headless["optional"] is True


def test_get_setup_fields_includes_cpa_sync_option():
    fields = setup_wizard.get_setup_fields({"EMAIL_PROVIDER": "cloudflare_temp_email"})
    cpa_sync = next(field for field in fields if field["key"] == "CPA_SYNC_ENABLED")

    assert cpa_sync["type"] == "select"
    assert cpa_sync["default"] == "true"
    assert cpa_sync["optional"] is True
