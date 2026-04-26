#!/usr/bin/env python3
import autoteam.display  # noqa: F401 — 自动设置虚拟显示器

"""
ChatGPT Team 自动邀请 + 注册工具

完整流程:
1. CloudMail 创建临时邮箱
2. ChatGPT API 发送 Team 邀请
3. CloudMail 收取邀请邮件，提取邀请链接
4. Playwright 打开邀请链接，注册 ChatGPT 账号
5. CloudMail 收取验证码邮件，自动填入
6. 完成注册并加入 workspace

用法:
    python invite.py
"""

import logging
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

from autoteam.chatgpt_api import ChatGPTTeamAPI
from autoteam.cloudmail import CloudMailClient
from autoteam.config import get_playwright_launch_options
from autoteam.mail_provider import get_message_key
from autoteam.paths import data_path

logger = logging.getLogger(__name__)

MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "180"))
SCREENSHOT_DIR = data_path("screenshots")


def screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    page.screenshot(path=str(path), full_page=True)
    logger.debug("[截图] %s", str(path))


def find_and_click(page, selectors, label="元素", timeout=3000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout):
                logger.debug("[注册] 找到%s: %s", label, sel)
                loc.click()
                return True
        except Exception:
            continue
    return False


def find_visible(page, selectors, label="元素", timeout=3000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout):
                logger.debug("[注册] 找到%s: %s", label, sel)
                return loc
        except Exception:
            continue
    return None


def click_primary_auth_button(page, field, labels):
    label_re = re.compile(rf"^(?:{'|'.join(re.escape(label) for label in labels)})$", re.I)

    try:
        form = field.locator("xpath=ancestor::form[1]").first
        btn = form.get_by_role("button", name=label_re).first
        if btn.is_visible(timeout=2000):
            btn.click()
            return True
    except Exception:
        pass

    try:
        form = field.locator("xpath=ancestor::form[1]").first
        btn = form.locator('button[type="submit"], input[type="submit"]').first
        if btn.is_visible(timeout=2000):
            btn.click()
            return True
    except Exception:
        pass

    try:
        field.press("Enter")
        return True
    except Exception:
        return False


def _snapshot_existing_message_keys(mail_client, email, size=20):
    if not mail_client:
        return set()

    try:
        return {
            key
            for key in (get_message_key(message) for message in mail_client.search_emails_by_recipient(email, size=size))
            if key
        }
    except Exception as exc:
        logger.debug("[注册] 记录已有邮件 ID 失败: %s", exc)
        return set()


def _code_error_detail(page):
    try:
        body = page.locator("body").inner_text(timeout=1000)
    except Exception:
        return ""

    lowered = body.lower()
    invalid_hints = (
        "invalid code",
        "incorrect code",
        "expired code",
        "wrong code",
        "验证码无效",
        "验证码错误",
        "验证码已过期",
        "代码无效",
        "代码错误",
    )
    for hint in invalid_hints:
        if hint in lowered:
            return hint
    return ""


def _is_code_input_visible(page, timeout=500):
    if _is_profile_page(page):
        return False
    try:
        single_inputs = page.locator('input[maxlength="1"]').all()
        visible_single_inputs = []
        for input_item in single_inputs:
            try:
                if input_item.is_visible(timeout=timeout):
                    visible_single_inputs.append(input_item)
            except Exception:
                continue
        if len(visible_single_inputs) >= 4:
            return True
    except Exception:
        pass

    try:
        return page.locator(
            'input[name="code"], input[autocomplete="one-time-code"], '
            'input[placeholder*="验证码"], input[placeholder*="验证" i], input[placeholder*="code" i]'
        ).first.is_visible(timeout=timeout)
    except Exception:
        return False


def _is_profile_page(page):
    current_url = (page.url or "").lower()
    if "about-you" in current_url:
        return True

    try:
        body = page.locator("body").inner_text(timeout=1000).lower()
    except Exception:
        body = ""

    return "birthday" in body or "生日" in body


def _wait_for_code_submit_result(page, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        error_detail = _code_error_detail(page)
        if error_detail:
            return "invalid", error_detail
        if _is_profile_page(page):
            return "accepted", ""
        if not _is_code_input_visible(page, timeout=300):
            return "accepted", ""
        time.sleep(0.5)

    error_detail = _code_error_detail(page)
    if error_detail:
        return "invalid", error_detail
    return "pending", ""


def wait_for_cloudflare(page, max_wait=60):
    for i in range(max_wait // 5):
        html = page.content()[:2000].lower()
        if "verify you are human" not in html and "challenge" not in page.url:
            return True
        logger.info("[注册] 等待 Cloudflare... (%ds)", i * 5)
        time.sleep(5)
    return False


def _detect_invite_register_step(page):
    current_url = (page.url or "").lower()
    if "accounts.google.com" in current_url:
        return "google"

    if "chatgpt.com/auth/login" in current_url:
        try:
            login_btn = page.locator('[data-testid="login-button"], button:has-text("登录"), button:has-text("Log in")').first
            if login_btn.is_visible(timeout=800):
                return "login_button"
        except Exception:
            pass

    if _is_profile_page(page):
        return "profile"

    if _is_code_input_visible(page, timeout=800):
        return "code"

    if find_visible(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
            'input[id="password"]',
        ],
        "密码输入框",
        timeout=800,
    ):
        return "password"

    if find_visible(
        page,
        [
            'input[name="email"]',
            'input[type="email"]',
            'input[placeholder*="email" i]',
            'input[id="email"]',
            '#email-input',
            'input[autocomplete="email"]',
        ],
        "邮箱输入框",
        timeout=800,
    ):
        return "email"

    try:
        body = page.locator("body").inner_text(timeout=1000).lower()
    except Exception:
        body = ""
    logged_in_hints = (
        "历史聊天记录",
        "新聊天",
        "search chats",
        "chat history",
        "deep research",
        "codex",
    )
    if ("chatgpt.com" in current_url and "/auth/" not in current_url) or any(hint in body for hint in logged_in_hints):
        return "completed"
    return "unknown"


def _submit_invite_verification_code(page, verification_code):
    try:
        single_inputs = page.locator('input[maxlength="1"]').all()
    except Exception:
        single_inputs = []

    visible_single_inputs = []
    for input_item in single_inputs:
        try:
            if input_item.is_visible(timeout=300):
                visible_single_inputs.append(input_item)
        except Exception:
            continue

    if len(visible_single_inputs) >= 4:
        logger.debug("[注册] 检测到 %d 个单字符输入框", len(single_inputs))
        try:
            for input_item in visible_single_inputs:
                input_item.fill("")
            visible_single_inputs[0].click()
            page.keyboard.type(verification_code, delay=80)
            time.sleep(0.5)
            values = [item.input_value(timeout=300) for item in visible_single_inputs[: len(verification_code)]]
            if "".join(values) == verification_code:
                return True
        except Exception:
            pass

        for i, char in enumerate(verification_code):
            if i < len(visible_single_inputs):
                visible_single_inputs[i].fill(char)
                try:
                    visible_single_inputs[i].evaluate(
                        "(el) => { el.dispatchEvent(new Event('input', { bubbles: true }));"
                        " el.dispatchEvent(new Event('change', { bubbles: true })); }"
                    )
                except Exception:
                    pass
                time.sleep(0.2)
        return True

    code_input = find_visible(
        page,
        [
            'input[name="code"]',
            'input[placeholder*="code" i]',
            'input[placeholder*="验证" i]',
            'input[type="text"]',
            'input[autocomplete="one-time-code"]',
        ],
        "验证码输入框",
    )
    if not code_input:
        return False

    try:
        code_input.click()
        code_input.press("ControlOrMeta+A")
        code_input.press("Backspace")
        code_input.type(verification_code, delay=80)
    except Exception:
        code_input.fill(verification_code)

    try:
        code_input.evaluate(
            "(el) => { el.dispatchEvent(new Event('input', { bubbles: true }));"
            " el.dispatchEvent(new Event('change', { bubbles: true })); }"
        )
    except Exception:
        pass
    return True


def _fill_invite_profile(page):
    name_input = find_visible(
        page,
        [
            'input[name="name"]',
            'input[placeholder*="name" i]',
            'input[id="name"]',
            'input[placeholder*="全名" i]',
        ],
        "名字输入框",
        timeout=5000,
    )

    if name_input:
        name_input.fill("User")
        time.sleep(0.5)

    filled_age = False
    try:
        spinbuttons = page.locator('[role="spinbutton"]').all()
    except Exception:
        spinbuttons = []
    if len(spinbuttons) >= 3:
        try:
            page.locator("text=生日日期").click()
            time.sleep(0.5)
        except Exception:
            pass
        for sb, val in zip(spinbuttons[:3], ["1995", "06", "15"]):
            sb.click(force=True)
            time.sleep(0.2)
            page.keyboard.type(val, delay=80)
            time.sleep(0.3)
        logger.info("[注册] 填入生日: 1995/06/15 (spinbutton)")
        filled_age = True
    else:
        age_input = find_visible(
            page,
            [
                'input[name="age"]',
                'input[id="age"]',
                'input[placeholder*="age" i]',
                'input[placeholder*="年龄" i]',
                'input[type="number"]',
            ],
            "年龄输入框",
            timeout=3000,
        )
        if age_input:
            age_input.fill("25")
            logger.info("[注册] 填入年龄: 25")
            filled_age = True

    if name_input or filled_age:
        find_and_click(
            page,
            [
                'button:has-text("完成帐户创建")',
                'button:has-text("Complete")',
                'button:has-text("Continue")',
                'button:has-text("Agree")',
                'button[type="submit"]',
            ],
            "完成按钮",
        )
        time.sleep(8)
        screenshot(page, "reg_07_after_profile.png")


def register_with_invite(page, invite_link, email, mail_client, password=None):
    """用邀请链接注册 ChatGPT 账号并加入 workspace，返回 (success, password)"""

    known_message_keys = _snapshot_existing_message_keys(mail_client, email)
    used_code_message_keys = set()
    if known_message_keys:
        logger.info("[注册] 已记录发码前已有邮件 %d 封，后续只读取新验证码", len(known_message_keys))

    logger.info("[注册] 打开邀请链接...")
    page.goto(invite_link, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    wait_for_cloudflare(page)
    screenshot(page, "reg_01_invite_page.png")
    logger.info("[注册] 当前 URL: %s", page.url)

    # 可能需要点击 Sign up
    find_and_click(
        page,
        [
            'button:has-text("Sign up")',
            'a:has-text("Sign up")',
            'button:has-text("Create account")',
            'a:has-text("Create account")',
            'button:has-text("注册")',
        ],
        "注册按钮",
        timeout=5000,
    )
    time.sleep(3)
    screenshot(page, "reg_02_signup.png")
    verification_code = None
    verification_message_key = ""
    code_submit_attempts = {}

    for attempt in range(24):
        step = _detect_invite_register_step(page)
        logger.info("[注册] 邀请注册链接步骤: %s | URL: %s", step, page.url)

        if step == "completed":
            screenshot(page, "reg_08_final.png")
            logger.info("[注册] 注册成功并已加入 workspace!")
            return True, password

        if step == "google":
            logger.warning("[注册] 误跳转到 Google 登录，返回上一步重试")
            try:
                page.go_back(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                page.goto(invite_link, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            continue

        if step == "login_button":
            logger.info("[注册] 点击邀请页登录按钮")
            clicked = find_and_click(
                page,
                [
                    '[data-testid="login-button"]',
                    'button:has-text("登录")',
                    'button:has-text("Log in")',
                ],
                "登录按钮",
                timeout=2000,
            )
            logger.info("[注册] 邀请页登录按钮点击结果: %s", clicked)
            time.sleep(5)
            screenshot(page, f"reg_02b_after_login_click_{attempt}.png")
            continue

        if step == "email":
            logger.info("[注册] 输入邮箱: %s", email)
            email_input = find_visible(
                page,
                [
                    'input[name="email"]',
                    'input[type="email"]',
                    'input[placeholder*="email" i]',
                    'input[id="email"]',
                    '#email-input',
                    'input[autocomplete="email"]',
                ],
                "邮箱输入框",
            )
            if email_input:
                email_input.fill(email)
                time.sleep(1)
                click_primary_auth_button(page, email_input, ["Continue", "继续", "Log in"])
            time.sleep(5)
            screenshot(page, f"reg_03_after_email_{attempt}.png")
            continue

        if step == "password":
            pwd_input = find_visible(
                page,
                [
                    'input[name="password"]',
                    'input[type="password"]',
                    'input[id="password"]',
                ],
                "密码输入框",
                timeout=5000,
            )
            if pwd_input:
                if not password:
                    import uuid

                    password = f"Tmp_{uuid.uuid4().hex[:12]}!"
                logger.info("[注册] 设置密码: %s", password)
                pwd_input.fill(password)
                time.sleep(1)
                click_primary_auth_button(page, pwd_input, ["Continue", "继续", "Log in"])
            time.sleep(5)
            screenshot(page, f"reg_04_after_password_{attempt}.png")
            continue

        if step == "code":
            if not verification_code:
                logger.info("[注册] 等待 ChatGPT 发送验证码到 %s...", email)
                try:
                    ignored_message_keys = known_message_keys | used_code_message_keys
                    verification_code, verification_message_key = mail_client.wait_for_code(
                        email,
                        timeout=MAIL_TIMEOUT,
                        poll_interval=3,
                        ignore_message_keys=ignored_message_keys,
                        skip_invites=True,
                    )
                    if verification_code:
                        logger.info(
                            "[注册] 收到验证码: %s (mail_id=%s)",
                            verification_code,
                            verification_message_key or "-",
                        )
                except Exception as e:
                    logger.error("[注册] 等待验证码异常: %s", e)

            if not verification_code:
                logger.warning("[注册] 未自动获取到验证码")
                screenshot(page, "reg_05_no_code.png")
                return False, password

            logger.info("[注册] 输入验证码: %s", verification_code)
            screenshot(page, "reg_05_before_code.png")
            if not _submit_invite_verification_code(page, verification_code):
                logger.warning("[注册] 未找到验证码输入框")
                screenshot(page, "reg_05_no_code_input.png")
                return False, password

            time.sleep(1)
            try:
                code_field = page.locator('input[maxlength="1"], input[name="code"], input[autocomplete="one-time-code"]').first
                click_primary_auth_button(page, code_field, ["Continue", "Verify", "Submit", "继续"])
            except Exception:
                pass
            submit_status, error_detail = _wait_for_code_submit_result(page, timeout=12)
            if submit_status == "accepted":
                screenshot(page, f"reg_06_after_code_{attempt}.png")
                continue

            if submit_status == "invalid":
                if verification_message_key:
                    used_code_message_keys.add(verification_message_key)
                logger.warning(
                    "[注册] 验证码邮件 %s（code=%s）被页面判定无效，页面提示: %s",
                    verification_message_key or "-",
                    verification_code,
                    error_detail or "-",
                )
                known_message_keys.update(_snapshot_existing_message_keys(mail_client, email))
                verification_code = None
                verification_message_key = ""
                time.sleep(3)
                screenshot(page, f"reg_06_after_invalid_code_{attempt}.png")
                continue

            if submit_status == "pending":
                code_key = verification_message_key or verification_code
                submit_attempts = code_submit_attempts.get(code_key, 0)
                if submit_attempts < 3:
                    code_submit_attempts[code_key] = submit_attempts + 1
                    logger.warning(
                        "[注册] 验证码 %s 提交后未确认成功，继续重试同一验证码（第 %d 次）",
                        verification_code,
                        submit_attempts + 1,
                    )
                    screenshot(page, f"reg_06_after_code_{attempt}.png")
                    continue

                logger.warning(
                    "[注册] 验证码 %s 多次提交后页面仍无响应，丢弃该邮件并继续等待新验证码",
                    verification_code,
                )
                if verification_message_key:
                    used_code_message_keys.add(verification_message_key)
                known_message_keys.update(_snapshot_existing_message_keys(mail_client, email))
                verification_code = None
                verification_message_key = ""
                time.sleep(3)
            screenshot(page, f"reg_06_after_code_{attempt}.png")
            continue

        if step == "profile":
            _fill_invite_profile(page)
            continue

        find_and_click(
            page,
            [
                'button:has-text("Sign up")',
                'a:has-text("Sign up")',
                'button:has-text("Create account")',
                'a:has-text("Create account")',
                'button:has-text("Continue")',
                'button:has-text("继续")',
                'button:has-text("Accept")',
                'button:has-text("Agree")',
                'button:has-text("Join")',
                'button:has-text("Join workspace")',
                'button:has-text("加入")',
                'button:has-text("Accept invite")',
                'button[type="submit"]',
            ],
            "推进按钮",
            timeout=1500,
        )
        time.sleep(3)

    screenshot(page, "reg_08_final.png")
    logger.warning("[注册] 注册流程可能未完成，请查看截图")
    return False, password


def run():
    mail_client = None
    account_id = None
    chatgpt = None

    try:
        # Step 1: 创建临时邮箱
        mail_client = CloudMailClient()
        mail_client.login()
        account_id, email = mail_client.create_temp_email()
        logger.info("[邀请] 临时邮箱: %s", email)

        # Step 2: 发送 Team 邀请
        chatgpt = ChatGPTTeamAPI()
        chatgpt.start()
        status, data = chatgpt.invite_member(email)

        if status != 200:
            logger.error("[邀请] 邀请失败 (HTTP %d)", status)
            return False
        logger.info("[邀请] 邀请已发送")

        # Step 3: 等待邀请邮件
        logger.info("[邀请] 等待邀请邮件...")
        invite_link = None
        try:
            email_data = mail_client.wait_for_email(
                to_email=email,
                timeout=MAIL_TIMEOUT,
            )
            invite_link = mail_client.extract_invite_link(email_data)
            if not invite_link:
                emails = mail_client.search_emails_by_recipient(email, size=10)
                for item in emails:
                    invite_link = mail_client.extract_invite_link(item)
                    if invite_link:
                        break
        except TimeoutError:
            logger.error("[邀请] 等待邀请邮件超时")
        except Exception as e:
            logger.error("[邀请] 获取邀请邮件失败: %s", e)

        if not invite_link:
            logger.error("[邀请] 未获取到邀请链接")
            return False

        logger.info("[邀请] 邀请链接: %s", invite_link)

        # Step 4: 关闭 ChatGPT API 浏览器，开新浏览器做注册
        chatgpt.stop()
        chatgpt = None

        logger.info("[邀请] 开始注册 ChatGPT 账号")

        with sync_playwright() as p:
            browser = p.chromium.launch(**get_playwright_launch_options())
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            result, pwd = register_with_invite(page, invite_link, email, mail_client)

            screenshot(page, "final.png")
            browser.close()

        if result:
            logger.info("[邀请] %s 已注册并加入 ChatGPT Team", email)
        else:
            logger.error("[邀请] 流程未完成，请查看 screenshots/ 目录")

        return result

    finally:
        if chatgpt:
            chatgpt.stop()
        # 不删除临时邮箱，保留账号
        if mail_client and account_id:
            logger.info("[邀请] 临时邮箱保留: %s (accountId=%s)", email, account_id)


def main():
    logger.info("ChatGPT Team 自动邀请 + 注册工具")
    result = run()
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
