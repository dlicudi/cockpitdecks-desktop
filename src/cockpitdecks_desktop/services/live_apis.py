"""Lightweight HTTP probes for live status (X-Plane Web API, Cockpitdecks web UI)."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_XPLANE_BASE = "http://127.0.0.1:8086"
DEFAULT_COCKPIT_WEB = "http://127.0.0.1:7777/"


def _unwrap_v3_payload(payload: dict[str, Any]) -> dict[str, Any]:
    inner = payload.get("data")
    if isinstance(inner, dict):
        return inner
    return payload


def _fetch_json(url: str, *, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None, "capabilities: not a JSON object"
    return data, None


def _xplane_capability_paths(api_version: str = "v3") -> list[str]:
    """Candidate capability endpoints (newest first, then compatibility fallbacks)."""
    primary = f"/api/{api_version}/capabilities"
    candidates = [
        primary,
        "/api/v3/capabilities",
        "/api/v2/capabilities",
        "/api/v1/capabilities",
        "/api/capabilities",
        "/capabilities",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def fetch_xplane_capabilities_json(
    *,
    base_url: str = DEFAULT_XPLANE_BASE,
    api_version: str = "v3",
    timeout: float = 2.0,
) -> tuple[dict[str, Any] | None, str | None]:
    base = base_url.rstrip("/")
    tried_404: list[str] = []
    for path in _xplane_capability_paths(api_version=api_version):
        url = f"{base}{path}"
        try:
            data, err = _fetch_json(url, timeout=timeout)
            if err is not None:
                return None, f"{err} ({path})"
            return _unwrap_v3_payload(data), None
        except HTTPError as exc:
            if exc.code == 404:
                tried_404.append(path)
                continue
            return None, f"{exc} ({path})"
        except URLError as exc:
            return None, str(exc.reason)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            return None, str(exc)
    checked = ", ".join(tried_404) if tried_404 else "no endpoints"
    return None, f"HTTP Error 404: Not Found (checked {checked})"


def summarize_xplane_capabilities(caps: dict[str, Any]) -> str:
    parts: list[str] = []
    api = caps.get("api")
    if isinstance(api, dict):
        versions = api.get("versions")
        if isinstance(versions, list) and versions:
            parts.append("REST " + ",".join(str(v) for v in versions))
    xp = caps.get("x-plane") or caps.get("xplane")
    if isinstance(xp, dict):
        ver = xp.get("version")
        if ver is not None:
            parts.append(f"X-Plane {ver}")
        host = xp.get("hostname") or xp.get("host")
        if host:
            parts.append(str(host))
    if not parts:
        keys = sorted(caps.keys())
        if keys:
            parts.append("keys: " + ",".join(keys[:6]) + ("…" if len(keys) > 6 else ""))
        else:
            parts.append("(empty capabilities)")
    return " | ".join(parts)


def xplane_capabilities_status_line(
    *,
    base_url: str = DEFAULT_XPLANE_BASE,
    api_version: str = "v3",
    timeout: float = 2.0,
) -> tuple[str, str | None]:
    """Return (display_line, error_or_none)."""
    caps, err = fetch_xplane_capabilities_json(base_url=base_url, api_version=api_version, timeout=timeout)
    if err is not None:
        return f"unreachable ({err})", err
    return summarize_xplane_capabilities(caps), None


def cockpitdecks_session_status_line(*, base_url: str = "http://127.0.0.1:7777", timeout: float = 1.5) -> str:
    """GET /desktop-status (Cockpitdecks ≥ with route). Returns one-line summary or placeholder."""
    url = f"{base_url.rstrip('/')}/desktop-status"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return "— (invalid JSON)"
        name = (data.get("aircraft_name") or "").strip() or "—"
        dcp = (data.get("deckconfig_path") or "").strip() or "—"
        decks = data.get("deck_names")
        if isinstance(decks, list) and decks:
            deck_part = f"{len(decks)} deck(s): {', '.join(str(d) for d in decks[:4])}" + ("…" if len(decks) > 4 else "")
        else:
            deck_part = "no decks"
        ver = (data.get("cockpitdecks_version") or "").strip()
        ver_part = f"v{ver} | " if ver else ""
        return f"{ver_part}{name} | {deck_part} | {dcp}"
    except HTTPError as exc:
        if exc.code == 404:
            return "— (update Cockpitdecks: /desktop-status missing)"
        return f"— (HTTP {exc.code})"
    except URLError:
        return "— (Cockpitdecks not running)"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return "— (could not read session)"


def cockpitdecks_metrics_json(*, base_url: str = "http://127.0.0.1:7777", timeout: float = 1.5) -> tuple[dict[str, Any] | None, str | None]:
    """GET /desktop-metrics and return parsed object."""
    url = f"{base_url.rstrip('/')}/desktop-metrics"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None, "metrics: not a JSON object"
        return data, None
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except URLError:
        return None, "Cockpitdecks not running"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None, "could not read metrics"


def cockpitdecks_metrics_status_line(*, base_url: str = "http://127.0.0.1:7777", timeout: float = 1.5) -> str:
    """GET /desktop-metrics and summarize runtime/perf in one line."""
    data, err = cockpitdecks_metrics_json(base_url=base_url, timeout=timeout)
    if err is None and isinstance(data, dict):
        p = data.get("process") if isinstance(data.get("process"), dict) else {}
        c = data.get("cockpit") if isinstance(data.get("cockpit"), dict) else {}
        s = data.get("simulator") if isinstance(data.get("simulator"), dict) else {}
        cpu = p.get("cpu_percent")
        rss = p.get("max_rss_mb")
        thr = p.get("thread_count")
        vars_n = c.get("registered_variables")
        drefs = s.get("datarefs_monitored")
        parts: list[str] = []
        if isinstance(cpu, (int, float)):
            parts.append(f"CPU {cpu:.1f}%")
        if isinstance(rss, (int, float)):
            parts.append(f"RSS {rss:.1f} MB")
        if isinstance(thr, int):
            parts.append(f"threads {thr}")
        if isinstance(vars_n, int):
            parts.append(f"vars {vars_n}")
        if isinstance(drefs, int):
            parts.append(f"drefs {drefs}")
        return " | ".join(parts) if parts else "— (no metrics yet)"
    if err == "HTTP 404":
        return "— (update Cockpitdecks: /desktop-metrics missing)"
    return f"— ({err})" if err else "— (could not read metrics)"


def cockpitdecks_web_status_line(*, url: str = DEFAULT_COCKPIT_WEB, timeout: float = 1.5) -> tuple[str, str | None]:
    """Cheap check: GET / and discard body (Flask returns HTML)."""
    try:
        req = Request(url, headers={"Accept": "*/*"})
        with urlopen(req, timeout=timeout) as resp:
            _ = resp.read(512)
        code = getattr(resp, "status", None) or resp.getcode()
        return f"OK (HTTP {code})", None
    except HTTPError as exc:
        return f"unreachable (HTTP {exc.code})", str(exc)
    except URLError as exc:
        return f"unreachable ({exc.reason})", str(exc.reason)
    except (OSError, ValueError) as exc:
        return f"unreachable ({exc})", str(exc)
