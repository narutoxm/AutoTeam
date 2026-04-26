"""独立持久化 mailbox 凭据，避免账号同步时丢失邮箱访问令牌。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from autoteam.textio import read_text, write_text

PROJECT_ROOT = Path(__file__).parent.parent.parent
MAILBOX_STORE_FILE = PROJECT_ROOT / "mailboxes.json"


def _normalized_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def load_mailboxes() -> dict:
    if MAILBOX_STORE_FILE.exists():
        text = read_text(MAILBOX_STORE_FILE).strip()
        if text:
            return json.loads(text)
    return {}


def save_mailboxes(mailboxes: dict):
    write_text(MAILBOX_STORE_FILE, json.dumps(mailboxes, indent=2, ensure_ascii=False))


def get_saved_mailbox(email: str | None):
    normalized_email = _normalized_email(email)
    if not normalized_email:
        return None

    mailboxes = load_mailboxes()
    entry = mailboxes.get(normalized_email)
    if not isinstance(entry, dict):
        return None
    return entry.get("mailbox")


def save_mailbox(email: str | None, provider: str | None, mailbox):
    normalized_email = _normalized_email(email)
    if not normalized_email or not mailbox:
        return

    mailboxes = load_mailboxes()
    mailboxes[normalized_email] = {
        "provider": str(provider or "").strip(),
        "mailbox": mailbox,
        "updated_at": time.time(),
    }
    save_mailboxes(mailboxes)


def delete_mailbox(email: str | None):
    normalized_email = _normalized_email(email)
    if not normalized_email:
        return

    mailboxes = load_mailboxes()
    if normalized_email not in mailboxes:
        return
    mailboxes.pop(normalized_email, None)
    save_mailboxes(mailboxes)
