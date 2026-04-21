"""GitHub releases service — fetch, download, and install GitHub release assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

GITHUB_REPO = "dlicudi/cockpitdecks"
DESKTOP_GITHUB_REPO = "dlicudi/cockpitdecks-desktop"
ASSET_PLATFORM = "windows-x64" if sys.platform == "win32" else "macos-arm64"
DESKTOP_ASSET_PLATFORM = ASSET_PLATFORM
if sys.platform == "win32":
    INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CockpitdecksDesktop" / "bin"
elif sys.platform == "darwin":
    INSTALL_DIR = Path.home() / "Library" / "Application Support" / "CockpitdecksDesktop" / "bin"
else:
    INSTALL_DIR = Path.home() / ".cockpitdecks" / "bin"
DESKTOP_DOWNLOAD_DIR = Path.home() / "Downloads"
BINARY_NAME = "cockpitdecks.exe" if sys.platform == "win32" else "cockpitdecks"
VERSION_FILE = INSTALL_DIR / "version"

AUTO_REFRESH_INTERVAL_SECS = 30 * 60
MANUAL_REFRESH_MIN_INTERVAL_SECS = 60

_API_BASE = "https://api.github.com"
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def fetch_releases(repo: str = GITHUB_REPO) -> list[dict]:
    """Fetch all releases from the GitHub API."""
    url = f"{_API_BASE}/repos/{repo}/releases"
    req = urllib.request.Request(url, headers=_API_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def version_sort_key(tag: str) -> tuple:
    """Parse a v-prefixed semver-ish tag into a sortable tuple."""
    tag = tag.removeprefix("v")
    parts = tag.split("-", 1)
    base = parts[0]
    pre = parts[1] if len(parts) > 1 else ""
    try:
        base_tuple = tuple(int(x) for x in base.split("."))
    except ValueError:
        base_tuple = (0,)
    if pre:
        pre_parts = pre.split(".")
        pre_name = pre_parts[0]
        pre_num = int(pre_parts[1]) if len(pre_parts) > 1 and pre_parts[1].isdigit() else 0
        pre_order = {"alpha": 0, "beta": 1, "rc": 2}.get(pre_name, -1)
        return base_tuple + (0, pre_order, pre_num)
    return base_tuple + (1, 0, 0)


def _releases_cache_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CockpitdecksDesktop"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "CockpitdecksDesktop"
    else:
        root = Path.home() / ".config" / "cockpitdecks-desktop"
    return root / "cache"


def _repo_cache_key(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", repo)


def _releases_cache_paths(repo: str) -> tuple[Path, Path]:
    cache_dir = _releases_cache_dir()
    key = _repo_cache_key(repo)
    return cache_dir / f"{key}.json", cache_dir / f"{key}.meta.json"


def _load_cached_releases(repo: str) -> tuple[list[dict] | None, float | None]:
    data_path, meta_path = _releases_cache_paths(repo)
    try:
        raw = json.loads(data_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(raw, list):
        return None, None
    cached_at = meta.get("cached_at") if isinstance(meta, dict) else None
    try:
        cached_ts = float(cached_at) if cached_at is not None else None
    except (TypeError, ValueError):
        cached_ts = None
    return raw, cached_ts


def _save_cached_releases(repo: str, releases: list[dict]) -> float:
    data_path, meta_path = _releases_cache_paths(repo)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    cached_at = time.time()
    data_path.write_text(json.dumps(releases), encoding="utf-8")
    meta_path.write_text(json.dumps({"cached_at": cached_at}), encoding="utf-8")
    return cached_at


def fetch_releases_cached(repo: str = GITHUB_REPO, *, force_refresh: bool = False, min_interval: int | None = None) -> tuple[list[dict], dict]:
    cached, cached_at = _load_cached_releases(repo)
    now = time.time()
    min_age = AUTO_REFRESH_INTERVAL_SECS if min_interval is None else max(0, int(min_interval))
    if cached is not None and not force_refresh and cached_at is not None and (now - cached_at) < min_age:
        return cached, {"source": "cache", "cached_at": cached_at, "stale": False, "error": ""}
    if cached is not None and force_refresh and cached_at is not None and (now - cached_at) < min_age:
        return cached, {"source": "cache", "cached_at": cached_at, "stale": False, "error": f"Refresh limited: try again in {max(1, int(min_age - (now - cached_at)))}s"}
    try:
        releases = fetch_releases(repo=repo)
    except Exception as exc:
        if cached is not None:
            return cached, {"source": "cache", "cached_at": cached_at, "stale": True, "error": str(exc)}
        raise
    saved_at = _save_cached_releases(repo, releases)
    return releases, {"source": "network", "cached_at": saved_at, "stale": False, "error": ""}


def _format_cached_at(ts: float | None) -> str:
    if not ts:
        return "unknown time"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _binary_path_for_tag(tag: str) -> Path:
    return INSTALL_DIR / f"{BINARY_NAME}-{tag}"


def installed_versions() -> dict[str, Path]:
    """Return {tag: binary_path} for all managed installed versions."""
    out: dict[str, Path] = {}
    if not INSTALL_DIR.exists():
        return out
    for candidate in INSTALL_DIR.iterdir():
        if not candidate.is_file():
            continue
        if candidate.name.startswith(f"{BINARY_NAME}-"):
            tag = candidate.name[len(f"{BINARY_NAME}-") :]
            if tag:
                out[tag] = candidate
    active_tag = installed_version()
    legacy_binary = INSTALL_DIR / BINARY_NAME
    if active_tag and legacy_binary.exists() and active_tag not in out:
        out[active_tag] = legacy_binary
    return out


def activate_installed_version(tag: str) -> Path:
    """Mark an already-downloaded managed version as the active launcher binary."""
    versions = installed_versions()
    path = versions.get(tag)
    if path is None or not path.exists():
        raise RuntimeError(f"installed version not found: {tag}")
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(tag + "\n")
    return path


def remove_installed_version(tag: str) -> None:
    """Remove one cached managed version."""
    path = installed_versions().get(tag)
    if path is None or not path.exists():
        raise RuntimeError(f"installed version not found: {tag}")
    path.unlink()
    if installed_version() == tag:
        if VERSION_FILE.exists():
            VERSION_FILE.unlink()


def installed_version() -> str | None:
    """Return the installed version tag, or None if not installed."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip() or None
    return None


def installed_binary() -> Path:
    tag = installed_version()
    if tag:
        versioned = _binary_path_for_tag(tag)
        if versioned.exists():
            return versioned
    return INSTALL_DIR / BINARY_NAME


def has_binary_asset(release: dict) -> bool:
    return _find_binary_asset(release) is not None


def _find_asset(release: dict, suffix: str) -> dict | None:
    tag = release["tag_name"]
    name = f"cockpitdecks-{ASSET_PLATFORM}-{tag}{suffix}"
    for asset in release.get("assets", []):
        if asset["name"] == name:
            return asset
    return None


def _find_binary_asset(release: dict) -> dict | None:
    suffix = ".zip" if sys.platform == "win32" else ".tar.gz"
    return _find_asset(release, suffix)


def _find_desktop_asset(release: dict, suffix: str) -> dict | None:
    tag = release["tag_name"]
    name = f"cockpitdecks-desktop-{DESKTOP_ASSET_PLATFORM}-{tag}{suffix}"
    for asset in release.get("assets", []):
        if asset["name"] == name:
            return asset
    return None


def latest_desktop_release_info(repo: str = DESKTOP_GITHUB_REPO, *, force_refresh: bool = False, min_interval: int | None = None) -> tuple[dict | None, dict]:
    """Return newest matching desktop release plus fetch metadata."""
    suffix = ".zip"
    releases, meta = fetch_releases_cached(repo=repo, force_refresh=force_refresh, min_interval=min_interval)
    candidates = [r for r in releases if _find_desktop_asset(r, suffix) is not None]
    if not candidates:
        return None, meta
    candidates.sort(key=lambda r: version_sort_key(r.get("tag_name", "")), reverse=True)
    return candidates[0], meta


def latest_desktop_release(repo: str = DESKTOP_GITHUB_REPO) -> dict | None:
    release, _ = latest_desktop_release_info(repo=repo)
    return release


class DownloadCancelledError(Exception):
    """Raised when the user cancels a download."""


def desktop_download_dir() -> Path:
    return DESKTOP_DOWNLOAD_DIR


def desktop_default_extract_dir(tag: str) -> Path:
    return desktop_download_dir() / f"cockpitdecks-desktop-{tag}"


def download_and_extract_desktop_release(
    release: dict,
    dest_dir: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Download, verify, and extract a desktop release into the destination folder."""
    tag = release["tag_name"]
    log = on_log or (lambda msg: None)
    target = (dest_dir or desktop_default_extract_dir(tag)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(f"Destination already exists and is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)

    archive_asset = _find_desktop_asset(release, ".zip")
    sha256_asset = _find_desktop_asset(release, ".zip.sha256")
    if not archive_asset:
        raise RuntimeError(f"No desktop asset found for {tag}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        archive_path = tmp / archive_asset["name"]
        log(f"[desktop] downloading {archive_asset['name']} ({archive_asset['size']:,} bytes)")

        req = urllib.request.Request(archive_asset["browser_download_url"])
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            with open(archive_path, "wb") as fh:
                while True:
                    if should_cancel and should_cancel():
                        raise DownloadCancelledError()
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)

        log(f"[desktop] download complete: {archive_path.name}")

        if sha256_asset:
            log("[desktop] verifying SHA-256 checksum")
            req = urllib.request.Request(sha256_asset["browser_download_url"])
            with urllib.request.urlopen(req, timeout=15) as resp:
                expected = resp.read().decode("utf-8").split()[0]
            actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError(f"SHA-256 mismatch: expected {expected}, got {actual}")
            log("[desktop] checksum OK")
        else:
            log("[desktop] warning: no checksum asset found")

        log(f"[desktop] extracting to {target}")
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(target)

    return target


def download_and_install(
    release: dict,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Download, verify SHA-256, extract, and install the cockpitdecks binary.

    Raises RuntimeError on any failure, DownloadCancelledError if cancelled.
    Calls on_progress(bytes_done, total_bytes) and on_log(message) throughout.
    """
    tag = release["tag_name"]
    log = on_log or (lambda msg: None)

    binary_asset = _find_binary_asset(release)
    archive_suffix = ".zip" if sys.platform == "win32" else ".tar.gz"
    sha256_asset = _find_asset(release, f"{archive_suffix}.sha256")

    if not binary_asset:
        raise RuntimeError(f"No {ASSET_PLATFORM} asset found for {tag}")

    log(f"[releases] downloading {binary_asset['name']} ({binary_asset['size']:,} bytes)")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        archive_path = tmp / binary_asset["name"]

        # Download tarball with progress
        req = urllib.request.Request(binary_asset["browser_download_url"])
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            with open(archive_path, "wb") as fh:
                while True:
                    if should_cancel and should_cancel():
                        raise DownloadCancelledError()
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)

        log(f"[releases] download complete ({done:,} bytes)")

        # Verify SHA-256
        if sha256_asset:
            log("[releases] verifying SHA-256 checksum")
            sha256_path = tmp / sha256_asset["name"]
            req = urllib.request.Request(sha256_asset["browser_download_url"])
            with urllib.request.urlopen(req, timeout=10) as resp:
                sha256_path.write_bytes(resp.read())
            expected = sha256_path.read_text().split()[0]
            actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError(f"SHA-256 mismatch: expected {expected}, got {actual}")
            log("[releases] checksum OK")
        else:
            log("[releases] warning: no SHA-256 asset found, skipping verification")

        log("[releases] extracting binary")
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        binary_path = _binary_path_for_tag(tag)
        if sys.platform == "win32":
            with zipfile.ZipFile(archive_path) as zf:
                binary_member = next((m for m in zf.namelist() if m.endswith(f"/{BINARY_NAME}") or m == BINARY_NAME), None)
                if not binary_member:
                    raise RuntimeError(f"'{BINARY_NAME}' not found in zip archive")
                with zf.open(binary_member) as extracted, binary_path.open("wb") as out:
                    shutil.copyfileobj(extracted, out)
        else:
            with tarfile.open(archive_path) as tf:
                binary_member = next(
                    (m for m in tf.getmembers() if m.name.endswith(f"/{BINARY_NAME}") or m.name == BINARY_NAME),
                    None,
                )
                if not binary_member:
                    raise RuntimeError(f"'{BINARY_NAME}' not found in tarball")
                extracted = tf.extractfile(binary_member)
                if not extracted:
                    raise RuntimeError("Failed to extract binary from tarball")
                binary_path.write_bytes(extracted.read())
                binary_path.chmod(0o755)

        VERSION_FILE.write_text(tag + "\n")
        log(f"[releases] installed {tag} → {binary_path}")
        return binary_path
