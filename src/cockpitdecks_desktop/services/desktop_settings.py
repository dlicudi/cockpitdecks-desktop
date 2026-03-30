"""Persisted settings for Cockpitdecks Desktop (paths + API endpoints).

Values map to Cockpitdecks environment variables where noted; see cockpitdecks.constant.ENVIRON_KW.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
# Keys written into the child process environment when launching cockpitdecks.
LAUNCH_ENV_KEYS = (
    "COCKPITDECKS_PATH",
    "SIMULATOR_HOST",
    "API_HOST",
    "API_PORT",
)

DEFAULTS: dict[str, str] = {
    "COCKPITDECKS_PATH": "",
    "COCKPITDECKS_TARGET": "",
    "SIMULATOR_HOST": "",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8086",
    "COCKPIT_WEB_HOST": "127.0.0.1",
    "COCKPIT_WEB_PORT": "7777",
    # Desktop app only: optional path to cockpitdecks binary or script.
    "COCKPITDECKS_LAUNCHER_PATH": "",
    # Desktop app only: "1" to use the custom launcher path, "0" to use the managed install or bundled binary.
    "COCKPITDECKS_LAUNCHER_USE_CUSTOM": "0",
    # Desktop app only: optional file to append launcher/Cockpitdecks stdout/stderr.
    "COCKPITDECKS_LAUNCH_LOG_PATH": "",
    # Engine mode log level sent to cockpitdecks (DEBUG, INFO, WARNING, ERROR).
    "COCKPITDECKS_LOG_LEVEL": "INFO",
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


def managed_decks_dir() -> Path:
    return _config_dir() / "decks"


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
                # Migration: old COCKPITDECKS_LAUNCHER_MODE ("dev"/"custom") → USE_CUSTOM = "1"
                old_mode = str(raw.get("COCKPITDECKS_LAUNCHER_MODE") or "").strip().lower()
                if old_mode in ("dev", "custom"):
                    data["COCKPITDECKS_LAUNCHER_USE_CUSTOM"] = "1"
                # Migration: old separate dev path field → unified launcher path
                old_dev = str(raw.get("COCKPITDECKS_LAUNCHER_PATH_DEV") or "").strip()
                if old_dev and not data["COCKPITDECKS_LAUNCHER_PATH"]:
                    data["COCKPITDECKS_LAUNCHER_PATH"] = old_dev
        except (OSError, json.JSONDecodeError):
            pass
    return data


def save(values: dict[str, str]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULTS, **{k: (values.get(k) or "").strip() for k in DEFAULTS}}
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def launch_env_overlay(values: dict[str, str] | None = None) -> dict[str, str]:
    """Environment variables to merge when spawning cockpitdecks."""
    v = values or load()
    out: dict[str, str] = {}
    for key in LAUNCH_ENV_KEYS:
        s = (v.get(key) or "").strip()
        if s:
            out[key] = s
    # Always set engine mode when launched by the desktop app.
    out["COCKPITDECKS_ENGINE"] = "1"
    # Log level for cockpitdecks stdout (default INFO).
    log_level = (v.get("COCKPITDECKS_LOG_LEVEL") or "INFO").strip().upper()
    if log_level:
        out["COCKPITDECKS_LOG_LEVEL"] = log_level
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
    """Active launcher path from settings.

    Returns None to signal "use app defaults" (managed install / bundled binary).
    """
    v = values or load()
    if (v.get("COCKPITDECKS_LAUNCHER_USE_CUSTOM") or "0").strip() != "1":
        return None
    raw = (v.get("COCKPITDECKS_LAUNCHER_PATH") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()
