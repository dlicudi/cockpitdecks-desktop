# cockpitdecks-desktop

Desktop companion for Cockpitdecks setup, updates, diagnostics, and launch.

## Scope

This repository provides a Qt desktop app that orchestrates (does not duplicate)
existing Cockpitdecks tooling across repositories.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cockpitdecks-desktop
```

## Build (PyInstaller)

```bash
scripts/build_desktop.sh
```

`scripts/build_desktop.sh` bundles a `cockpitdecks` sidecar into the desktop app before running PyInstaller. By default it uses `../cockpitdecks/dist/cockpitdecks`, but you can override the source binary with `LAUNCHER_SRC=/path/to/cockpitdecks`.

## Automated macOS Apple Silicon release

GitHub Actions can build and publish a macOS arm64 desktop app from this repo, bundling a published launcher binary from the `cockpitdecks` GitHub releases.

- Workflow: `.github/workflows/release-desktop-macos-arm64.yml`
- Launcher manifest: `.github/desktop-macos-arm64.env`
- Trigger: push a tag matching `desktop-v*`
- Manual trigger: `workflow_dispatch` with required `release_tag` and optional `launcher_tag`
- Output artifact: `cockpitdecks-desktop-macos-arm64-<tag>.tar.gz`

The workflow downloads `cockpitdecks-macos-arm64-<launcher_tag>.tar.gz` from the configured launcher repository, verifies its SHA-256 checksum, unpacks `cockpitdecks`, bundles it into `Cockpitdecks Desktop.app`, and publishes the desktop artifact plus `build-metadata.json`.

## App icon

Bundled at `src/cockpitdecks_desktop/resources/app_icon.png` (**square**, 1024×1024 recommended; dock / window icon; also passed to PyInstaller as `EXE(icon=…)` where supported). Widescreen masters are letterboxed by macOS with black bars — after replacing the PNG, run `python3 scripts/square_app_icon.py` from the repo root to rebuild a padded square from the average corner color.

`icon_loader` also normalizes non-square PNGs at runtime. For a macOS `.app` Finder icon, generate an `.icns` from the square PNG (e.g. `iconutil`) and point your app bundle at it when packaging.

### Still seeing the old icon?

- **PyInstaller build:** The icon is baked in at link time. Rebuild with `pyinstaller --clean packaging/pyinstaller/desktop.spec` (or `scripts/build_desktop.sh`) after changing `app_icon.png`.
- **Editable `pip install -e .`:** The loader reads `src/cockpitdecks_desktop/resources/app_icon.png` next to the code first; if you ever installed a wheel without reinstalling, run `pip install -e .` again and fully quit the app (⌘Q), then relaunch.
- **macOS Dock / Finder cache:** Run `bash scripts/refresh_macos_icon_cache.sh` (optional: pass your `.app` path to `touch` it). Remove the app from the Dock and open it again from `dist/` if the tile stays stale.
