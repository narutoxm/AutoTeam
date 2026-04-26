"""配置文件 - 从 .env 文件或环境变量加载"""

import os
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from autoteam.textio import parse_env_line, parse_env_value, read_text

# 项目根目录（pyproject.toml 所在位置）
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 加载 .env 文件（从项目根目录）
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in read_text(_env_file).splitlines():
        parsed = parse_env_line(line)
        if parsed:
            key, value = parsed
            os.environ.setdefault(key, value)


def _get_int_env(name: str, default: int) -> int:
    return int(parse_env_value(os.environ.get(name, str(default))))


def _get_bool_env(name: str, default: bool) -> bool:
    raw = str(parse_env_value(os.environ.get(name, str(default)))).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def normalize_email_provider(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in ("cloudmail", "cloud_mail"):
        return "cloudmail"
    if raw in (
        "cloudflare_temp_email",
        "cloudflare",
        "cf_temp_email",
        "cfe",
        "cf-email",
    ):
        return "cloudflare_temp_email"
    return "cloudflare_temp_email"


def _parse_domain_list(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        raw = str(value or "").replace("\r", "\n")
        for sep in (",", "，", ";", "；"):
            raw = raw.replace(sep, "\n")
        items = raw.split("\n")

    domains = []
    seen = set()
    for item in items:
        domain = str(item or "").strip().lower()
        if domain.startswith("@"):
            domain = domain[1:]
        if not domain or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains


# 邮箱 Provider 配置
EMAIL_PROVIDER = normalize_email_provider(os.environ.get("EMAIL_PROVIDER", "cloudflare_temp_email"))

# CloudMail 配置
CLOUDMAIL_BASE_URL = os.environ.get("CLOUDMAIL_BASE_URL", "")
CLOUDMAIL_EMAIL = os.environ.get("CLOUDMAIL_EMAIL", "")
CLOUDMAIL_PASSWORD = os.environ.get("CLOUDMAIL_PASSWORD", "")
CLOUDMAIL_DOMAIN = os.environ.get("CLOUDMAIL_DOMAIN", "")

# Cloudflare Temp Email 配置
CLOUDFLARE_TEMP_EMAIL_API_BASE = os.environ.get("CLOUDFLARE_TEMP_EMAIL_API_BASE", "").rstrip("/")
CLOUDFLARE_TEMP_EMAIL_ADMIN_PASSWORD = os.environ.get("CLOUDFLARE_TEMP_EMAIL_ADMIN_PASSWORD", "").strip()
_cloudflare_temp_email_domains = os.environ.get("CLOUDFLARE_TEMP_EMAIL_DOMAINS")
if _cloudflare_temp_email_domains is None:
    _cloudflare_temp_email_domains = os.environ.get("CLOUDFLARE_TEMP_EMAIL_DOMAIN", "")
CLOUDFLARE_TEMP_EMAIL_DOMAINS = _parse_domain_list(_cloudflare_temp_email_domains)
CLOUDFLARE_TEMP_EMAIL_DOMAIN = CLOUDFLARE_TEMP_EMAIL_DOMAINS[0] if CLOUDFLARE_TEMP_EMAIL_DOMAINS else ""
CLOUDFLARE_TEMP_EMAIL_PROXY = os.environ.get("CLOUDFLARE_TEMP_EMAIL_PROXY", "").strip()

# ChatGPT Team 配置
CHATGPT_ACCOUNT_ID = os.environ.get("CHATGPT_ACCOUNT_ID", "")

# CPA (CLIProxyAPI) 配置
CPA_URL = os.environ.get("CPA_URL", "")
CPA_KEY = os.environ.get("CPA_KEY", "")

# 轮询邮件间隔/超时（秒）
EMAIL_POLL_INTERVAL = _get_int_env("EMAIL_POLL_INTERVAL", 3)
EMAIL_POLL_TIMEOUT = _get_int_env("EMAIL_POLL_TIMEOUT", 300)

# API 鉴权（不设置则不启用）
API_KEY = os.environ.get("API_KEY", "")

# CPA 自动同步开关
CPA_SYNC_ENABLED = _get_bool_env("CPA_SYNC_ENABLED", True)

# 自动巡检配置
AUTO_CHECK_INTERVAL = _get_int_env("AUTO_CHECK_INTERVAL", 300)  # 巡检间隔（秒），默认 5 分钟
AUTO_CHECK_THRESHOLD = _get_int_env("AUTO_CHECK_THRESHOLD", 10)  # 额度低于此百分比触发轮转，默认 10%
AUTO_CHECK_MIN_LOW = _get_int_env("AUTO_CHECK_MIN_LOW", 2)  # 至少几个账号低于阈值才触发，默认 2

# Playwright 代理配置
PLAYWRIGHT_HEADLESS = _get_bool_env("PLAYWRIGHT_HEADLESS", False)
PLAYWRIGHT_PROXY_URL = os.environ.get("PLAYWRIGHT_PROXY_URL", "").strip()
PLAYWRIGHT_PROXY_SERVER = os.environ.get("PLAYWRIGHT_PROXY_SERVER", "").strip()
PLAYWRIGHT_PROXY_USERNAME = os.environ.get("PLAYWRIGHT_PROXY_USERNAME", "").strip()
PLAYWRIGHT_PROXY_PASSWORD = os.environ.get("PLAYWRIGHT_PROXY_PASSWORD", "").strip()
PLAYWRIGHT_PROXY_BYPASS = os.environ.get("PLAYWRIGHT_PROXY_BYPASS", "").strip()


def _format_proxy_host(hostname: str) -> str:
    if ":" in hostname and not hostname.startswith("["):
        return f"[{hostname}]"
    return hostname


def _parse_proxy_url(proxy_url: str):
    if "://" not in proxy_url:
        return {"server": proxy_url}

    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy_url}

    host = _format_proxy_host(parsed.hostname)
    server = f"{parsed.scheme}://{host}"
    if parsed.port:
        server = f"{server}:{parsed.port}"

    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def get_playwright_launch_options():
    """统一的 Playwright Chromium 启动参数。"""
    options = {
        "headless": PLAYWRIGHT_HEADLESS,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    }

    proxy = None
    if PLAYWRIGHT_PROXY_URL:
        proxy = _parse_proxy_url(PLAYWRIGHT_PROXY_URL)
    elif PLAYWRIGHT_PROXY_SERVER:
        proxy = {"server": PLAYWRIGHT_PROXY_SERVER}
        if PLAYWRIGHT_PROXY_USERNAME:
            proxy["username"] = PLAYWRIGHT_PROXY_USERNAME
        if PLAYWRIGHT_PROXY_PASSWORD:
            proxy["password"] = PLAYWRIGHT_PROXY_PASSWORD

    if proxy:
        if PLAYWRIGHT_PROXY_BYPASS:
            proxy["bypass"] = PLAYWRIGHT_PROXY_BYPASS
        options["proxy"] = proxy

    return options


def get_requests_proxy_dict():
    """将 Playwright 代理配置转换为 requests 可用的 proxies 结构。"""
    proxy_url = ""
    if PLAYWRIGHT_PROXY_URL:
        proxy_url = PLAYWRIGHT_PROXY_URL
    elif PLAYWRIGHT_PROXY_SERVER:
        server = PLAYWRIGHT_PROXY_SERVER
        if "://" not in server:
            server = f"http://{server}"

        parsed = urlsplit(server)
        if parsed.scheme and parsed.hostname:
            host = _format_proxy_host(parsed.hostname)
            netloc = host
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            if PLAYWRIGHT_PROXY_USERNAME:
                username = quote(PLAYWRIGHT_PROXY_USERNAME, safe="")
                if PLAYWRIGHT_PROXY_PASSWORD:
                    password = quote(PLAYWRIGHT_PROXY_PASSWORD, safe="")
                    netloc = f"{username}:{password}@{netloc}"
                else:
                    netloc = f"{username}@{netloc}"
            proxy_url = urlunsplit((parsed.scheme, netloc, parsed.path or "", parsed.query or "", parsed.fragment or ""))
        else:
            proxy_url = server

    proxy_url = str(proxy_url or "").strip()
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}
