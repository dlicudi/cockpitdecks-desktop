"""Persisted settings for Cockpitdecks Desktop (paths + API endpoints).

Values map to Cockpitdecks environment variables where noted; see cockpitdecks.constant.ENVIRON_KW.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
# Keys written into the child process environment when launching cockpitdecks-launcher.
LAUNCH_ENV_KEYS = (
    "SIMULATOR_HOME",
    "COCKPITDECKS_PATH",
    "SIMULATOR_HOST",
    "API_HOST",
    "API_PORT",
)

DEFAULTS: dict[str, str] = {
    "SIMULATOR_HOME": "",
    "COCKPITDECKS_PATH": "",
    "SIMULATOR_HOST": "",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8086",
    "COCKPIT_WEB_HOST": "127.0.0.1",
    "COCKPIT_WEB_PORT": "7777",
    # Desktop app only: optional path to cockpitdecks-launcher (empty = bundled or dev default).
    "COCKPITDECKS_LAUNCHER_PATH": "",
}


def _config_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "CockpitdecksDesktop"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "CockpitdecksDesktop"
    return home / ".config" / "cockpitdecks-desktop"


def settings_path() -> Path:
    return _config_dir() / "settings.json"


def load() -> dict[str, str]:
    path = settings_path()
    data: dict[str, str] = {k: str(v) for k, v in DEFAULTS.items()}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in DEFAULTS:
                    if k in raw and raw[k] is not None:
                        data[k] = str(raw[k]).strip()
        except (OSError, json.JSONDecodeError):
            pass
    return data


def save(values: dict[str, str]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULTS, **{k: (values.get(k) or "").strip() for k in DEFAULTS}}
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def launch_env_overlay(values: dict[str, str] | None = None) -> dict[str, str]:
    """Environment variables to merge when spawning cockpitdecks-launcher."""
    v = values or load()
    out: dict[str, str] = {}
    for key in LAUNCH_ENV_KEYS:
        s = (v.get(key) or "").strip()
        if s:
            out[key] = s
    return out


def xplane_rest_base(values: dict[str, str] | None = None) -> str:
    v = values or load()
    host = (v.get("API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (v.get("API_PORT") or "8086").strip() or "8086"
    return f"http://{host}:{port}"


def cockpit_web_base(values: dict[str, str] | None = None) -> str:
    v = values or load()
    host = (v.get("COCKPIT_WEB_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (v.get("COCKPIT_WEB_PORT") or "7777").strip() or "7777"
    return f"http://{host}:{port}"


def launcher_binary_path(values: dict[str, str] | None = None) -> Path | None:
    """Explicit launcher path from settings, or None to use app defaults (bundle / dev dist)."""
    v = values or load()
    raw = (v.get("COCKPITDECKS_LAUNCHER_PATH") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()
