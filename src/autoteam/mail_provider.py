"""邮箱 provider 选择与通用辅助方法。"""

from __future__ import annotations

from typing import Any

from autoteam.config import (
    CLOUDFLARE_TEMP_EMAIL_DOMAINS,
    CLOUDMAIL_DOMAIN,
    EMAIL_PROVIDER,
    normalize_email_provider,
)
from autoteam.mailbox_store import get_saved_mailbox, save_mailbox

EMAIL_PROVIDER_OPTIONS = (
    {"value": "cloudflare_temp_email", "label": "Cloudflare Temp Email"},
    {"value": "cloudmail", "label": "CloudMail"},
)


def get_default_email_provider() -> str:
    return normalize_email_provider(EMAIL_PROVIDER)


def get_provider_label(provider: str | None) -> str:
    normalized = normalize_email_provider(provider)
    for option in EMAIL_PROVIDER_OPTIONS:
        if option["value"] == normalized:
            return option["label"]
    return normalized


def get_provider_domains(provider: str | None = None) -> list[str]:
    normalized = normalize_email_provider(provider or get_default_email_provider())
    if normalized == "cloudmail":
        domain = str(CLOUDMAIL_DOMAIN or "").strip().lower()
        if domain.startswith("@"):
            domain = domain[1:]
        return [domain] if domain else []
    return list(CLOUDFLARE_TEMP_EMAIL_DOMAINS)


def get_all_managed_domains() -> list[str]:
    domains = []
    seen = set()
    for provider in ("cloudflare_temp_email", "cloudmail"):
        for domain in get_provider_domains(provider):
            if domain and domain not in seen:
                seen.add(domain)
                domains.append(domain)
    return domains


def detect_email_provider_for_address(email: str | None) -> str | None:
    normalized_email = str(email or "").strip().lower()
    if not normalized_email or "@" not in normalized_email:
        return None

    for provider in ("cloudflare_temp_email", "cloudmail"):
        domains = get_provider_domains(provider)
        for domain in domains:
            if normalized_email.endswith(f"@{domain}"):
                return provider
    return None


def is_managed_email_address(email: str | None) -> bool:
    return detect_email_provider_for_address(email) is not None


def get_account_email_provider(account: dict | None) -> str:
    if not isinstance(account, dict):
        return get_default_email_provider()

    explicit = account.get("email_provider")
    if explicit:
        return normalize_email_provider(explicit)

    mailbox = account.get("mailbox")
    if isinstance(mailbox, dict):
        if mailbox.get("jwt_token") or mailbox.get("jwt"):
            return "cloudflare_temp_email"
        if mailbox.get("account_id") or mailbox.get("accountId"):
            return "cloudmail"

    if account.get("cloudmail_account_id") not in (None, ""):
        return "cloudmail"

    detected = detect_email_provider_for_address(account.get("email"))
    return detected or get_default_email_provider()


def get_account_mailbox(account: dict | None):
    if not isinstance(account, dict):
        return None
    mailbox = account.get("mailbox")
    if mailbox is not None:
        return mailbox
    cloudmail_account_id = account.get("cloudmail_account_id")
    if cloudmail_account_id not in (None, ""):
        return {"account_id": cloudmail_account_id}
    return get_saved_mailbox(account.get("email"))


def is_managed_account(account: dict | None) -> bool:
    if not isinstance(account, dict):
        return False
    if account.get("mailbox"):
        return True
    if account.get("cloudmail_account_id") not in (None, ""):
        return True
    return is_managed_email_address(account.get("email"))


def build_account_mailbox(provider: str | None, mailbox_ref, email: str | None = None):
    normalized = normalize_email_provider(provider or get_default_email_provider())
    if normalized == "cloudmail":
        if isinstance(mailbox_ref, dict):
            account_id = mailbox_ref.get("account_id") or mailbox_ref.get("accountId")
        else:
            account_id = mailbox_ref
        if account_id in (None, ""):
            return None
        return {"account_id": account_id}

    if isinstance(mailbox_ref, dict):
        jwt_token = str(mailbox_ref.get("jwt_token") or mailbox_ref.get("jwt") or "").strip()
        address = str(mailbox_ref.get("address") or email or "").strip().lower()
        mailbox_password = str(mailbox_ref.get("mailbox_password") or mailbox_ref.get("password") or "").strip()
        if jwt_token or address or mailbox_password:
            return {
                "jwt_token": jwt_token,
                "address": address,
                "mailbox_password": mailbox_password,
            }

    if mailbox_ref not in (None, ""):
        return {
            "jwt_token": str(mailbox_ref).strip(),
            "address": str(email or "").strip().lower(),
            "mailbox_password": "",
        }
    return None


def persist_account_mailbox(email: str | None, provider: str | None, mailbox_ref):
    mailbox = build_account_mailbox(provider, mailbox_ref, email=email)
    if mailbox:
        save_mailbox(email, provider, mailbox)
    return mailbox


def get_message_key(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    for key in ("messageId", "emailId", "id"):
        value = message.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def create_mail_client(provider: str | None = None, account: dict | None = None):
    selected_provider = normalize_email_provider(provider or get_account_email_provider(account))
    if selected_provider == "cloudflare_temp_email":
        from autoteam.cloudflare_temp_email import CloudflareTempEmailClient

        return CloudflareTempEmailClient()

    from autoteam.cloudmail import LegacyCloudMailClient

    return LegacyCloudMailClient()
