"""首次启动初始化向导 - 交互式填写 .env 中的必填配置。"""

from __future__ import annotations

import importlib
import logging
import os
import re
import secrets
import sys

from autoteam.config import DATA_DIR, PROJECT_ROOT, normalize_email_provider
from autoteam.mail_provider import EMAIL_PROVIDER_OPTIONS, get_provider_label
from autoteam.textio import parse_env_line, read_text, write_text

logger = logging.getLogger(__name__)

ENV_FILE = DATA_DIR / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

EMAIL_PROVIDER_FIELD = ("EMAIL_PROVIDER", "邮箱服务商", "cloudflare_temp_email", False)

PROVIDER_CONFIGS = {
    "cloudmail": [
        ("CLOUDMAIL_BASE_URL", "CloudMail API 地址", "", False),
        ("CLOUDMAIL_EMAIL", "CloudMail 登录邮箱", "", False),
        ("CLOUDMAIL_PASSWORD", "CloudMail 登录密码", "", False),
        ("CLOUDMAIL_DOMAIN", "CloudMail 邮箱域名（如 @example.com）", "", False),
    ],
    "cloudflare_temp_email": [
        ("CLOUDFLARE_TEMP_EMAIL_API_BASE", "Cloudflare Temp Email API 地址", "", False),
        ("CLOUDFLARE_TEMP_EMAIL_ADMIN_PASSWORD", "Cloudflare Temp Email 管理密码（可选）", "", True),
        (
            "CLOUDFLARE_TEMP_EMAIL_DOMAINS",
            "Cloudflare Temp Email 域名列表（可用逗号或换行分隔）",
            "",
            False,
        ),
        ("CLOUDFLARE_TEMP_EMAIL_PROXY", "Cloudflare Temp Email 请求代理（可选）", "", True),
    ],
}

COMMON_CONFIGS = [
    ("TEAM_INVITE_ROLE", "邀请成员角色（standard-user/account-admin）", "account-admin", True),
    ("CPA_URL", "CPA (CLIProxyAPI) 地址", "http://127.0.0.1:8317", False),
    ("CPA_KEY", "CPA 管理密钥", "", False),
    ("CPA_SYNC_ENABLED", "CPA 自动同步（true/false）", "true", True),
    ("PLAYWRIGHT_HEADLESS", "Playwright 无头模式（true/false）", "false", True),
    ("PLAYWRIGHT_PROXY_URL", "Playwright 浏览器代理 URL（可选，如 socks5://host:port）", "", True),
    ("PLAYWRIGHT_PROXY_BYPASS", "Playwright 代理绕过列表（可选，如 localhost,127.0.0.1）", "", True),
    ("CHATGPT_SESSION_IMPORT_BACKEND", "管理员 session_token 导入后端（uc/playwright）", "uc", True),
    ("SELENIUMBASE_UC_RECONNECT_TIME", "SeleniumBase UC 重连等待秒数", "6", True),
    ("API_KEY", "API 鉴权密钥（回车自动生成）", "", False),
]

# 兼容旧代码引用。
REQUIRED_CONFIGS = [EMAIL_PROVIDER_FIELD, *PROVIDER_CONFIGS["cloudflare_temp_email"], *COMMON_CONFIGS]


def _selected_email_provider(values: dict[str, str] | None = None) -> str:
    source = values or {}
    provider = source.get("EMAIL_PROVIDER", "") or os.environ.get("EMAIL_PROVIDER", "cloudflare_temp_email")
    return normalize_email_provider(provider)


def get_setup_fields(values: dict[str, str] | None = None) -> list[dict[str, object]]:
    provider = _selected_email_provider(values)
    fields = [
        {
            "key": EMAIL_PROVIDER_FIELD[0],
            "prompt": EMAIL_PROVIDER_FIELD[1],
            "default": EMAIL_PROVIDER_FIELD[2],
            "optional": EMAIL_PROVIDER_FIELD[3],
            "provider": None,
            "type": "select",
            "options": list(EMAIL_PROVIDER_OPTIONS),
        }
    ]

    for provider_name, configs in PROVIDER_CONFIGS.items():
        for key, prompt, default, optional in configs:
            fields.append(
                {
                    "key": key,
                    "prompt": prompt,
                    "default": default,
                    "optional": optional if provider_name == provider else True,
                    "provider": provider_name,
                    "type": "password" if "PASSWORD" in key else "text",
                }
            )

    for key, prompt, default, optional in COMMON_CONFIGS:
        field_type = "password" if "PASSWORD" in key or "KEY" in key else "text"
        options = None
        if key in {"PLAYWRIGHT_HEADLESS", "CPA_SYNC_ENABLED"}:
            field_type = "select"
            options = [
                {"value": "true", "label": "开启"},
                {"value": "false", "label": "关闭"},
            ]
            if key == "PLAYWRIGHT_HEADLESS":
                options = [
                    {"value": "false", "label": "关闭（有界面）"},
                    {"value": "true", "label": "开启（无头）"},
                ]
        elif key == "TEAM_INVITE_ROLE":
            field_type = "select"
            options = [
                {"value": "account-admin", "label": "管理员"},
                {"value": "standard-user", "label": "普通成员"},
            ]
        elif key == "CHATGPT_SESSION_IMPORT_BACKEND":
            field_type = "select"
            options = [
                {"value": "uc", "label": "UC（SeleniumBase）"},
                {"value": "playwright", "label": "Playwright"},
            ]
        fields.append(
            {
                "key": key,
                "prompt": prompt,
                "default": default,
                "optional": optional,
                "provider": None,
                "type": field_type,
                "options": options,
            }
        )

    return fields


def _read_env() -> dict[str, str]:
    """读取 .env 文件为 dict。"""
    result = {}
    if ENV_FILE.exists():
        for line in read_text(ENV_FILE).splitlines():
            parsed = parse_env_line(line)
            if parsed:
                key, value = parsed
                result[key] = value
    return result


def _write_env(key: str, value: str):
    """写入或更新 .env 中的某个 key。"""
    if ENV_FILE.exists():
        content = read_text(ENV_FILE)
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip() + f"\n{key}={value}\n"
        write_text(ENV_FILE, content)
    else:
        if ENV_EXAMPLE.exists():
            content = read_text(ENV_EXAMPLE)
            pattern = rf"^{re.escape(key)}=.*$"
            if re.search(pattern, content, re.MULTILINE):
                content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
            write_text(ENV_FILE, content)
        else:
            write_text(ENV_FILE, f"{key}={value}\n")


def _is_interactive() -> bool:
    """检测是否有终端交互能力（Docker 等非交互环境返回 False）。"""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _reload_runtime_modules():
    import autoteam.config

    importlib.reload(autoteam.config)
    for module_name in (
        "autoteam.cloudflare_temp_email",
        "autoteam.mail_provider",
        "autoteam.cloudmail",
    ):
        try:
            module = importlib.import_module(module_name)
            importlib.reload(module)
        except Exception:
            pass


def check_and_setup(interactive: bool = True) -> bool:
    """
    检查必填配置是否齐全，缺失时交互式提示输入。
    返回 True 表示配置完整，False 表示用户中断或非交互模式下缺配置。
    """
    interactive = interactive and _is_interactive()
    env = _read_env()
    fields = get_setup_fields(env)
    missing = []

    for field in fields:
        key = str(field["key"])
        val = env.get(key, "") or os.environ.get(key, "")
        if not val and not field["optional"]:
            missing.append(field)

    if not missing:
        if not _verify_email_provider():
            logger.error("[验证] %s 配置有误，请修改 .env 后重新启动", get_provider_label(_selected_email_provider(env)))
            sys.exit(1)
        if not _verify_cpa():
            logger.error("[验证] CPA 配置有误，请修改 .env 后重新启动")
            sys.exit(1)
        return True

    if not interactive:
        for field in missing:
            logger.warning("[配置] 缺少必填项: %s (%s)", field["key"], field["prompt"])
        logger.warning("[配置] 请通过 Web 面板或编辑 .env 文件填入配置")
        return False

    if not (env.get("EMAIL_PROVIDER") or os.environ.get("EMAIL_PROVIDER")):
        labels = ", ".join(f"{opt['value']}({opt['label']})" for opt in EMAIL_PROVIDER_OPTIONS)
        try:
            provider_input = input(f"  {EMAIL_PROVIDER_FIELD[1]} [{EMAIL_PROVIDER_FIELD[2]}] 可选: {labels}: ").strip()
        except KeyboardInterrupt:
            print("\n\n已取消配置。")
            raise SystemExit(130)
        provider_value = normalize_email_provider(provider_input or EMAIL_PROVIDER_FIELD[2])
        _write_env("EMAIL_PROVIDER", provider_value)
        os.environ["EMAIL_PROVIDER"] = provider_value
        env["EMAIL_PROVIDER"] = provider_value
        fields = get_setup_fields(env)
        missing = []
        for field in fields:
            key = str(field["key"])
            val = env.get(key, "") or os.environ.get(key, "")
            if not val and not field["optional"]:
                missing.append(field)

    provider = _selected_email_provider(env)
    print("\n=== AutoTeam 首次配置 ===\n")
    print(f"当前邮箱服务商: {get_provider_label(provider)}")
    print("检测到以下配置项需要填写，直接回车使用默认值（如有）:\n")

    for field in missing:
        key = str(field["key"])
        prompt = str(field["prompt"])
        default = str(field["default"] or "")
        hint = f" [{default}]" if default else ""
        if key == "API_KEY":
            hint = " [回车自动生成]"
        elif key == "EMAIL_PROVIDER":
            labels = ", ".join(f"{opt['value']}({opt['label']})" for opt in EMAIL_PROVIDER_OPTIONS)
            hint = f" [{default}] 可选: {labels}"

        try:
            value = input(f"  {prompt}{hint}: ").strip()
        except KeyboardInterrupt:
            print("\n\n已取消配置。")
            raise SystemExit(130)

        if not value:
            if key == "API_KEY":
                value = secrets.token_urlsafe(24)
                print(f"    -> 已自动生成: {value}")
            elif default:
                value = default
                print(f"    -> 使用默认值: {value}")
            else:
                print("    -> 跳过（必填项，后续可在 .env 中补充）")
                continue

        if key == "EMAIL_PROVIDER":
            value = normalize_email_provider(value)

        _write_env(key, value)
        os.environ[key] = value

    print("\n配置已保存到 .env\n")
    _reload_runtime_modules()

    if not _verify_email_provider():
        logger.error("[验证] %s 配置有误，请修改 .env 后重新启动", get_provider_label(_selected_email_provider()))
        sys.exit(1)
    if not _verify_cpa():
        logger.error("[验证] CPA 配置有误，请修改 .env 后重新启动")
        sys.exit(1)

    return True


def _verify_email_provider(provider: str | None = None):
    selected_provider = normalize_email_provider(provider or os.environ.get("EMAIL_PROVIDER", "cloudflare_temp_email"))
    if selected_provider == "cloudmail":
        return _verify_cloudmail()
    return _verify_cloudflare_temp_email()


def _verify_cloudmail():
    """验证 CloudMail 配置是否正确：登录 + 创建测试邮箱 + 删除。"""
    base_url = os.environ.get("CLOUDMAIL_BASE_URL", "")
    email = os.environ.get("CLOUDMAIL_EMAIL", "")
    password = os.environ.get("CLOUDMAIL_PASSWORD", "")
    domain = os.environ.get("CLOUDMAIL_DOMAIN", "")

    if not all([base_url, email, password, domain]):
        return False

    logger.info("[验证] CloudMail 配置...")

    try:
        from autoteam.cloudmail import LegacyCloudMailClient

        client = LegacyCloudMailClient()
        client.login()
        logger.info("[验证] CloudMail 登录成功")
    except Exception as exc:
        logger.error("[验证] CloudMail 登录失败: %s", exc)
        logger.error("[验证] 请检查 CLOUDMAIL_BASE_URL、CLOUDMAIL_EMAIL、CLOUDMAIL_PASSWORD")
        return False

    test_account_id = None
    try:
        import uuid as _uuid

        test_account_id, test_email = client.create_temp_email(prefix=f"at-test-{_uuid.uuid4().hex[:6]}")
        logger.info("[验证] CloudMail 创建测试邮箱成功: %s", test_email)
    except Exception as exc:
        logger.error("[验证] CloudMail 创建邮箱失败: %s", exc)
        logger.error("[验证] 请检查 CLOUDMAIL_DOMAIN 是否正确")
        return False

    try:
        if test_account_id:
            client.delete_account(test_account_id)
            logger.info("[验证] CloudMail 测试邮箱已清理")
    except Exception as exc:
        logger.warning("[验证] CloudMail 清理测试邮箱失败: %s（不影响使用）", exc)

    logger.info("[验证] CloudMail 配置验证通过")
    return True


def _verify_cloudflare_temp_email():
    """验证 Cloudflare Temp Email 配置是否正确：创建测试邮箱 + 读取收件箱。"""
    api_base = os.environ.get("CLOUDFLARE_TEMP_EMAIL_API_BASE", "").strip()
    domains = os.environ.get("CLOUDFLARE_TEMP_EMAIL_DOMAINS", "") or os.environ.get(
        "CLOUDFLARE_TEMP_EMAIL_DOMAIN",
        "",
    )
    if not api_base or not domains.strip():
        return False

    logger.info("[验证] Cloudflare Temp Email 配置...")

    try:
        import uuid as _uuid

        from autoteam.cloudflare_temp_email import CloudflareTempEmailClient

        client = CloudflareTempEmailClient()
        mailbox, test_email = client.create_temp_email(prefix=f"at-test-{_uuid.uuid4().hex[:6]}")
        logger.info("[验证] Cloudflare Temp Email 创建测试邮箱成功: %s", test_email)
        client.search_emails_by_recipient(test_email, size=1, account_id=mailbox)
        logger.info("[验证] Cloudflare Temp Email 收件箱读取成功")
    except Exception as exc:
        logger.error("[验证] Cloudflare Temp Email 验证失败: %s", exc)
        logger.error("[验证] 请检查 CLOUDFLARE_TEMP_EMAIL_API_BASE、CLOUDFLARE_TEMP_EMAIL_DOMAINS")
        return False

    logger.info("[验证] Cloudflare Temp Email 配置验证通过")
    return True


def _verify_cpa():
    """验证 CPA 配置是否正确：获取认证文件列表。"""
    cpa_url = os.environ.get("CPA_URL", "")
    cpa_key = os.environ.get("CPA_KEY", "")

    if not cpa_url or not cpa_key:
        return True

    logger.info("[验证] CPA 配置...")

    try:
        import requests

        response = requests.get(
            f"{cpa_url}/v0/management/auth-files",
            headers={"Authorization": f"Bearer {cpa_key}"},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            count = len(data.get("files", []))
            logger.info("[验证] CPA 连接成功（当前 %d 个认证文件）", count)
            return True
        if response.status_code == 401:
            logger.error("[验证] CPA 连接失败: 密钥无效 (401)")
            logger.error("[验证] 请检查 CPA_KEY 是否正确")
            return False
        logger.error("[验证] CPA 连接失败: HTTP %d", response.status_code)
        logger.error("[验证] 请检查 CPA_URL 是否正确")
        return False
    except requests.exceptions.ConnectionError:
        logger.error("[验证] CPA 连接失败: 无法连接到 %s", cpa_url)
        logger.error("[验证] 请检查 CPA_URL 是否正确，CPA 服务是否已启动")
        return False
    except Exception as exc:
        logger.error("[验证] CPA 连接失败: %s", exc)
        return False
