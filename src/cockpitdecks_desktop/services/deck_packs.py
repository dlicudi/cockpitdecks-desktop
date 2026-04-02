"""Deck-pack service — fetch releases from cockpitdecks-configs and download zip assets."""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

GITHUB_REPO = "dlicudi/cockpitdecks-configs"

_API_BASE = "https://api.github.com"
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def fetch_readme(pack_id: str, repo: str = GITHUB_REPO) -> str:
    """Fetch README.md for a pack from GitHub raw content. Raises on failure."""
    url = f"https://raw.githubusercontent.com/{repo}/main/decks/{pack_id}/README.md"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8")


def fetch_pack_releases(repo: str = GITHUB_REPO) -> list[dict]:
    """Fetch all releases from the cockpitdecks-configs GitHub repo."""
    url = f"{_API_BASE}/repos/{repo}/releases"
    req = urllib.request.Request(url, headers=_API_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def find_zip_asset(release: dict) -> dict | None:
    """Return the first .zip asset in a release, or None."""
    for asset in release.get("assets", []):
        if asset.get("name", "").endswith(".zip"):
            return asset
    return None


def download_zip(
    asset: dict,
    dest_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> Path:
    """Download a zip asset to *dest_dir* and return the local path.

    Raises RuntimeError on failure.
    """
    log = on_log or (lambda _: None)
    name = asset["name"]
    url = asset["browser_download_url"]
    dest = dest_dir / name

    log(f"[packs] downloading {name} ({asset.get('size', 0):,} bytes)")

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        with open(dest, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)

    log(f"[packs] download complete ({done:,} bytes)")
    return dest
