"""Filesystem paths used by AutoTeam.

AutoTeam historically stored runtime state in the repository root (accounts.json, state.json, auths/, ...).
To support multiple independent "main accounts" on the same machine, we allow isolating all runtime state
into a configurable data directory via `AUTOTEAM_DATA_DIR`.

When `AUTOTEAM_DATA_DIR` is not set, we keep the legacy behavior (data lives in the repo root).
"""

from __future__ import annotations

import os
from pathlib import Path

from autoteam.textio import parse_env_line, read_text

# Repo root (pyproject.toml lives here).
CODE_ROOT = Path(__file__).resolve().parent.parent.parent


def _read_root_env_value(key: str) -> str:
    """Read a single key from CODE_ROOT/.env without mutating os.environ.

    This is used for bootstrapping `AUTOTEAM_DATA_DIR` because the data dir must be
    decided before the regular config loader runs.
    """

    env_file = CODE_ROOT / ".env"
    if not env_file.exists():
        return ""
    try:
        lines = read_text(env_file).splitlines()
    except Exception:
        return ""
    for line in lines:
        parsed = parse_env_line(line)
        if not parsed:
            continue
        k, v = parsed
        if k == key:
            return str(v or "").strip()
    return ""


def _resolve_data_dir() -> Path:
    raw = str(os.environ.get("AUTOTEAM_DATA_DIR") or "").strip()
    if not raw:
        raw = _read_root_env_value("AUTOTEAM_DATA_DIR")
    if not raw:
        return CODE_ROOT

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (CODE_ROOT / path).resolve()
    return path


DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)


def data_path(*parts: str) -> Path:
    """Return a path under the active data directory."""

    return DATA_DIR.joinpath(*parts)
