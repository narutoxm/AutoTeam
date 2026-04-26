"""Cloudflare Temp Email provider client."""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from email import policy
from email.parser import Parser
from typing import Any

import requests

from autoteam.config import (
    CLOUDFLARE_TEMP_EMAIL_ADMIN_PASSWORD,
    CLOUDFLARE_TEMP_EMAIL_API_BASE,
    CLOUDFLARE_TEMP_EMAIL_DOMAINS,
    CLOUDFLARE_TEMP_EMAIL_PROXY,
    EMAIL_POLL_INTERVAL,
    EMAIL_POLL_TIMEOUT,
)
from autoteam.mail_provider import persist_account_mailbox
from autoteam.mailbox_store import get_saved_mailbox

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 25
_DOMAIN_LOCK = threading.Lock()
_DOMAIN_INDEX = 0
_VERIFICATION_CODE_PATTERNS = (
    r"输入此临时验证码以继续[:：]?\s*(\d{6})",
    r"Verification code:?\s*(\d{6})",
    r"code is\s*(\d{6})",
    r"代码为[:：]?\s*(\d{6})",
    r"验证码[:：]?\s*(\d{6})",
    r"(?:temporary\s+(?:openai|chatgpt)\s+login\s+code(?:\s+is)?|verification\s+code(?:\s+is)?|login\s+code(?:\s+is)?|code(?:\s+is)?|验证码(?:为|是)?)\D{0,24}(\d{6})",
    r">\s*(\d{6})\s*<",
    r"(?<![#&])\b(\d{6})\b",
)


def _normalize_proxy(proxy_value: str) -> str:
    value = str(proxy_value or "").strip()
    if not value:
        return ""
    if "://" in value:
        return value
    if ":" in value:
        return f"http://{value}"
    return ""


def _visible_text(value: str) -> str:
    content = str(value or "")
    if not content:
        return ""

    content = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", content)
    content = re.sub(r"(?is)<!--.*?-->", " ", content)
    content = re.sub(r"(?i)<br\\s*/?>", "\n", content)
    content = re.sub(r"(?i)</(?:p|div|tr|table|h[1-6]|li|td|section|article)>", "\n", content)
    content = re.sub(r"(?s)<[^>]+>", " ", content)
    content = html.unescape(content)
    content = re.sub(r"[\t\r\f\v ]+", " ", content)
    content = re.sub(r"\n\s+", "\n", content)
    content = re.sub(r"\n{2,}", "\n", content)
    return content.strip()


def _parse_metadata_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        try:
            parsed = json.loads(payload)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _parse_raw_email(raw_value: str):
    raw_text = str(raw_value or "")
    if not raw_text.strip():
        return None
    try:
        return Parser(policy=policy.default).parsestr(raw_text)
    except Exception:
        return None


def _extract_raw_email_body(raw_value: str) -> str:
    bodies = list(_extract_raw_email_bodies(raw_value))
    for content_type, body in bodies:
        if content_type == "text/plain":
            return body
    for content_type, body in bodies:
        if content_type == "text/html":
            return body
    return ""


def _extract_raw_email_bodies(raw_value: str):
    message = _parse_raw_email(raw_value)
    if not message:
        return

    if message.is_multipart():
        parts = message.walk()
    else:
        parts = [message]

    for part in parts:
        try:
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            if content_type not in ("text/plain", "text/html"):
                continue
            content = part.get_content()
        except Exception:
            continue

        if not isinstance(content, str) or not content.strip():
            continue
        if content_type == "text/plain":
            yield_type = "text/plain"
        else:
            yield_type = "text/html"
        yield yield_type, content


def _extract_raw_email_subject(raw_value: str) -> str:
    message = _parse_raw_email(raw_value)
    if not message:
        return ""
    try:
        return str(message.get("subject") or "")
    except Exception:
        return ""


def _extract_body(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    meta = _parse_metadata_payload(payload.get("metadata"))
    for key in ("text", "html", "body", "content"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value
    for key in ("text", "html", "body", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    for raw_value in (meta.get("raw"), payload.get("raw")):
        if isinstance(raw_value, str) and raw_value.strip():
            body = _extract_raw_email_body(raw_value)
            if body:
                return body
            return raw_value
    return ""


def _body_candidates(payload: dict[str, Any], label: str) -> list[tuple[str, str]]:
    if not isinstance(payload, dict):
        return []

    candidates: list[tuple[str, str]] = []
    meta = _parse_metadata_payload(payload.get("metadata"))
    for source_label, source in ((f"{label}.metadata", meta), (label, payload)):
        for key in ("text", "html", "body", "content"):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                candidates.append((f"{source_label}.{key}", value))
        raw_value = source.get("raw") if isinstance(source, dict) else None
        if isinstance(raw_value, str) and raw_value.strip():
            raw_body = _extract_raw_email_body(raw_value)
            if raw_body:
                candidates.append((f"{source_label}.raw.mime_body", raw_body))
            else:
                candidates.append((f"{source_label}.raw", raw_value))
    return candidates


def _snippet_around_code(text: str, code: str = "", limit: int = 140) -> str:
    visible = _visible_text(text)
    if not visible:
        return ""
    if code and code in visible:
        index = visible.find(code)
        start = max(0, index - 45)
        end = min(len(visible), index + len(code) + 45)
        return visible[start:end].replace("\n", " ")
    return visible[:limit].replace("\n", " ")


def _extract_field(payloads: list[dict[str, Any]], keys: tuple[str, ...]) -> str:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        meta = _parse_metadata_payload(payload.get("metadata"))
        for source in (payload, meta):
            for key in keys:
                value = source.get(key)
                if value not in (None, ""):
                    return str(value)
            if "subject" in keys or "title" in keys:
                raw_value = source.get("raw")
                if isinstance(raw_value, str) and raw_value.strip():
                    subject = _extract_raw_email_subject(raw_value)
                    if subject:
                        return subject
    return ""


def _message_id(message: dict[str, Any]) -> str:
    for key in ("messageId", "emailId", "id"):
        value = message.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


class CloudflareTempEmailClient:
    provider_name = "cloudflare_temp_email"

    def __init__(
        self,
        api_base: str | None = None,
        proxy: str | None = None,
        admin_password: str | None = None,
    ) -> None:
        self.api_base = str(api_base or CLOUDFLARE_TEMP_EMAIL_API_BASE or "").rstrip("/")
        self.admin_password = str(admin_password or CLOUDFLARE_TEMP_EMAIL_ADMIN_PASSWORD or "").strip()
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self._runtime_mailboxes: dict[str, dict[str, Any]] = {}
        self._consumed_message_keys: set[str] = set()

        normalized_proxy = _normalize_proxy(str(proxy or CLOUDFLARE_TEMP_EMAIL_PROXY or ""))
        if normalized_proxy:
            self.session.proxies = {"http": normalized_proxy, "https": normalized_proxy}

    def login(self):
        return None

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.admin_password:
            headers["x-custom-auth"] = self.admin_password
            headers["x-admin-auth"] = self.admin_password
        return headers

    def _next_domain(self) -> str:
        if not CLOUDFLARE_TEMP_EMAIL_DOMAINS:
            return ""
        if len(CLOUDFLARE_TEMP_EMAIL_DOMAINS) == 1:
            return CLOUDFLARE_TEMP_EMAIL_DOMAINS[0]

        global _DOMAIN_INDEX
        with _DOMAIN_LOCK:
            domain = CLOUDFLARE_TEMP_EMAIL_DOMAINS[_DOMAIN_INDEX % len(CLOUDFLARE_TEMP_EMAIL_DOMAINS)]
            _DOMAIN_INDEX = (_DOMAIN_INDEX + 1) % len(CLOUDFLARE_TEMP_EMAIL_DOMAINS)
        return domain

    @staticmethod
    def _normalize_mailbox_ref(value, email: str = "") -> dict[str, Any] | None:
        if isinstance(value, dict):
            jwt_token = str(value.get("jwt_token") or value.get("jwt") or "").strip()
            address = str(value.get("address") or email or "").strip().lower()
            password = str(value.get("mailbox_password") or value.get("password") or "").strip()
            if jwt_token or address or password:
                return {
                    "jwt_token": jwt_token,
                    "address": address,
                    "mailbox_password": password,
                }
            return None

        jwt_token = str(value or "").strip()
        if not jwt_token:
            return None
        return {"jwt_token": jwt_token, "address": str(email or "").strip().lower(), "mailbox_password": ""}

    def _resolve_mailbox(self, to_email: str, account_id=None) -> dict[str, Any] | None:
        normalized_email = str(to_email or "").strip().lower()

        mailbox = self._normalize_mailbox_ref(account_id, email=normalized_email)
        if mailbox:
            if mailbox.get("address"):
                self._runtime_mailboxes[mailbox["address"]] = mailbox
            return mailbox

        if normalized_email in self._runtime_mailboxes:
            return self._runtime_mailboxes[normalized_email]

        try:
            from autoteam.accounts import load_accounts

            for acc in load_accounts():
                if str(acc.get("email") or "").strip().lower() != normalized_email:
                    continue
                mailbox = self._normalize_mailbox_ref(acc.get("mailbox"), email=normalized_email)
                if mailbox:
                    self._runtime_mailboxes[normalized_email] = mailbox
                    return mailbox
        except Exception:
            pass

        mailbox = self._normalize_mailbox_ref(get_saved_mailbox(normalized_email), email=normalized_email)
        if mailbox:
            self._runtime_mailboxes[normalized_email] = mailbox
            return mailbox

        return None

    def create_temp_email(self, prefix=None):
        if not self.api_base:
            raise RuntimeError("cloudflare_temp_email: api_base 未配置")

        domain = self._next_domain()
        if not domain:
            raise RuntimeError("cloudflare_temp_email: domain 未配置")

        name = str(prefix or "").strip().lower()
        if name:
            name = re.sub(r"[^a-z0-9._-]+", "-", name).strip("-._")

        response = self.session.post(
            f"{self.api_base}/api/new_address",
            headers=self._auth_headers(),
            json={"name": name, "domain": domain},
            timeout=DEFAULT_TIMEOUT,
            verify=False,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"cloudflare_temp_email: 创建邮箱失败 HTTP {response.status_code}: {response.text[:300]}")

        data = response.json()
        address = str(data.get("address") or "").strip().lower()
        jwt_token = str(data.get("jwt") or "").strip()
        mailbox_password = str(data.get("password") or "").strip()
        if not address or not jwt_token:
            raise RuntimeError("cloudflare_temp_email: 创建邮箱响应缺少 address/jwt")

        mailbox = {
            "address": address,
            "jwt_token": jwt_token,
            "mailbox_password": mailbox_password,
        }
        self._runtime_mailboxes[address] = mailbox
        persist_account_mailbox(address, self.provider_name, mailbox)
        logger.info("[CF Temp Email] 临时邮箱已创建: %s", address)
        return mailbox, address

    def list_messages(self, jwt_token: str) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.api_base}/api/mails",
            headers={"Authorization": f"Bearer {jwt_token}", **self._auth_headers()},
            params={"limit": 50, "offset": 0},
            timeout=DEFAULT_TIMEOUT,
            verify=False,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        return results if isinstance(results, list) else []

    def read_message(self, jwt_token: str, mail_id: str) -> dict[str, Any]:
        normalized_mail_id = str(mail_id or "").strip().split("/")[-1]
        response = self.session.get(
            f"{self.api_base}/api/mail/{normalized_mail_id}",
            headers={"Authorization": f"Bearer {jwt_token}", **self._auth_headers()},
            timeout=DEFAULT_TIMEOUT,
            verify=False,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def search_emails_by_recipient(self, to_email, size=10, account_id=None):
        mailbox = self._resolve_mailbox(to_email, account_id=account_id)
        if not mailbox or not mailbox.get("jwt_token"):
            return []

        try:
            rows = self.list_messages(mailbox["jwt_token"])
        except Exception as exc:
            logger.warning("[CF Temp Email] 拉取邮件列表失败: %s", exc)
            return []

        def _sort_key(item):
            try:
                return int((item or {}).get("id") or 0)
            except Exception:
                return 0

        rows = sorted(rows, key=_sort_key, reverse=True)
        messages = []
        for row in rows[: max(size, 1)]:
            message_id = str((row or {}).get("id") or "").strip()
            detail = {}
            if message_id:
                try:
                    detail = self.read_message(mailbox["jwt_token"], message_id)
                except Exception:
                    detail = {}

            payloads = [detail, row]
            body = _extract_body(detail) or _extract_body(row)
            message = {
                "id": message_id,
                "messageId": message_id,
                "emailId": message_id,
                "accountEmail": mailbox.get("address") or str(to_email or "").strip().lower(),
                "receiveEmail": mailbox.get("address") or str(to_email or "").strip().lower(),
                "subject": _extract_field(payloads, ("subject", "title")),
                "sendEmail": _extract_field(
                    payloads,
                    ("from", "fromAddress", "from_addr", "sender", "sendEmail", "from_email"),
                ),
                "text": _visible_text(body),
                "content": body,
                "raw": row,
                "detail": detail,
            }
            messages.append(message)

        return messages

    def extract_verification_code(self, email_data):
        code, _, _ = self._extract_verification_code_with_source(email_data)
        return code

    def _extract_verification_code_with_source(self, email_data):
        sources = [
            ("message.text", str(email_data.get("text") or "").strip()),
            ("message.content.visible", _visible_text(email_data.get("content") or "")),
            ("message.content", str(email_data.get("content") or "").strip()),
        ]
        detail = email_data.get("detail")
        if isinstance(detail, dict):
            sources = _body_candidates(detail, "detail") + sources

        for source_label, source in sources:
            if not source:
                continue
            for pattern in _VERIFICATION_CODE_PATTERNS:
                matches = re.findall(pattern, source, re.IGNORECASE)
                for code in matches:
                    if code == "177010":
                        continue
                    return code, source_label, _snippet_around_code(source, code)
        return None, "", ""

    def wait_for_code(
        self,
        to_email,
        timeout=120,
        poll_interval=3,
        account_id=None,
        ignore_message_keys=None,
        skip_invites=True,
    ):
        ignore_keys = {str(item) for item in (ignore_message_keys or set()) if item}
        ignore_keys.update(self._consumed_message_keys)
        deadline = time.time() + max(timeout, 1)

        mailbox = self._resolve_mailbox(to_email, account_id=account_id)
        if not mailbox or not mailbox.get("jwt_token"):
            return None, ""

        while time.time() < deadline:
            try:
                rows = self.list_messages(mailbox["jwt_token"])
            except Exception as exc:
                logger.warning("[CF Temp Email] 拉取邮件列表失败: %s", exc)
                time.sleep(poll_interval)
                continue

            def _sort_key(item):
                try:
                    return int((item or {}).get("id") or 0)
                except Exception:
                    return 0

            rows = sorted(rows, key=_sort_key, reverse=True)
            for row in rows[:10]:
                message_key = str((row or {}).get("id") or "").strip()
                if message_key and message_key in ignore_keys:
                    continue

                detail = {}
                if message_key:
                    try:
                        detail = self.read_message(mailbox["jwt_token"], message_key)
                    except Exception as exc:
                        logger.debug("[CF Temp Email] 读取邮件详情失败 mail_id=%s: %s", message_key, exc)
                        continue

                payloads = [detail, row]
                subject = str(_extract_field(payloads, ("subject", "title")) or "")
                subject_l = subject.lower()
                if skip_invites and (
                    "invited" in subject_l
                    or "invitation" in subject_l
                    or "邀请" in subject_l
                    or "use codex" in subject_l
                ):
                    continue

                email_data = {
                    "id": message_key,
                    "messageId": message_key,
                    "emailId": message_key,
                    "subject": subject,
                    "text": _visible_text(_extract_body(detail)),
                    "content": _extract_body(detail),
                    "detail": detail,
                }
                code, source_label, snippet = self._extract_verification_code_with_source(email_data)
                if code:
                    if message_key:
                        self._consumed_message_keys.add(message_key)
                    logger.info(
                        "[CF Temp Email] 验证码来自邮件详情: mail_id=%s subject=%s source=%s snippet=%s",
                        message_key or "-",
                        subject or "-",
                        source_label or "-",
                        snippet or "-",
                    )
                    return code, message_key

                if "验证码" in subject or "verification" in subject_l or "code" in subject_l:
                    preview = ""
                    candidates = _body_candidates(detail, "detail")
                    if candidates:
                        preview = _snippet_around_code(candidates[0][1])
                    logger.warning(
                        "[CF Temp Email] 找到验证码邮件但未从详情正文提取到验证码: mail_id=%s subject=%s detail_keys=%s preview=%s",
                        message_key or "-",
                        subject or "-",
                        ",".join(sorted(str(key) for key in detail.keys())) if isinstance(detail, dict) else "-",
                        preview or "-",
                    )
            time.sleep(poll_interval)

        return None, ""

    def wait_for_email(self, to_email, timeout=None, sender_keyword=None):
        timeout = timeout or EMAIL_POLL_TIMEOUT
        logger.info("[CF Temp Email] 等待邮件到达 %s... (超时 %ds)", to_email, timeout)
        start = time.time()

        while time.time() - start < timeout:
            emails = self.search_emails_by_recipient(to_email, size=10)
            for email in emails:
                sender = str(email.get("sendEmail") or "")
                if sender_keyword and sender_keyword.lower() not in sender.lower():
                    continue
                subject = str(email.get("subject") or "")
                logger.info("[CF Temp Email] 收到邮件: %s (from: %s)", subject, sender)
                return email

            elapsed = int(time.time() - start)
            print(f"\r[CF Temp Email] 等待中... ({elapsed}s)", end="", flush=True)
            time.sleep(EMAIL_POLL_INTERVAL)

        print()
        raise TimeoutError("等待邮件超时")

    def extract_invite_link(self, email_data):
        candidates = [
            ("content", str(email_data.get("content") or "")),
            ("text", str(email_data.get("text") or "")),
        ]
        detail = email_data.get("detail")
        if isinstance(detail, dict):
            candidates.extend(_body_candidates(detail, "detail"))
        raw_payloads = []
        if isinstance(detail, dict):
            meta = _parse_metadata_payload(detail.get("metadata"))
            raw_payloads.extend([meta.get("raw"), detail.get("raw")])
        raw_payloads.append(email_data.get("raw"))

        for raw_value in raw_payloads:
            if not isinstance(raw_value, str) or not raw_value.strip():
                continue
            for content_type, body in _extract_raw_email_bodies(raw_value):
                candidates.append((f"raw.{content_type}", body))

        href_pattern = re.compile(
            r"""href=["'](https://chatgpt\.com/auth/login\?[^"']+)["']""",
            re.IGNORECASE,
        )

        for source_label, body in candidates:
            if not body:
                continue
            links = href_pattern.findall(body)
            if links:
                link = html.unescape(links[0])
                logger.info("[CF Temp Email] 提取到邀请链接: %s... (source=%s)", link[:80], source_label)
                return link

        direct_pattern = re.compile(r"(https://chatgpt\.com/auth/login\?[^\s<>\"']+)", re.IGNORECASE)
        for source_label, body in candidates:
            if not body:
                continue
            links = direct_pattern.findall(body)
            if links:
                link = html.unescape(links[0])
                logger.info("[CF Temp Email] 提取到邀请链接: %s... (source=%s)", link[:80], source_label)
                return link

        generic_pattern = re.compile(r"https?://[^\s<>\"']+(?:invite|accept|join|workspace|accept_wId)[^\s<>\"']*", re.I)
        for source_label, body in candidates:
            if not body:
                continue
            match = generic_pattern.search(body)
            if match:
                link = html.unescape(match.group(0))
                logger.info("[CF Temp Email] 提取到链接: %s... (source=%s)", link[:80], source_label)
                return link

        return None

    def delete_emails_for(self, to_email):
        return 0

    def delete_account(self, account_id):
        mailbox = self._normalize_mailbox_ref(account_id)
        address = mailbox.get("address") if mailbox else ""
        if address and address in self._runtime_mailboxes:
            self._runtime_mailboxes.pop(address, None)
        logger.info("[CF Temp Email] 当前 provider 不支持删除邮箱，跳过: %s", address or _message_id(mailbox or {}))
        return {"code": 204, "message": "cloudflare_temp_email does not support mailbox deletion"}
