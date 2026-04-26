import importlib

from autoteam import config


def test_get_playwright_launch_options_uses_headless_env(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "true")
    reloaded = importlib.reload(config)

    assert reloaded.get_playwright_launch_options()["headless"] is True
