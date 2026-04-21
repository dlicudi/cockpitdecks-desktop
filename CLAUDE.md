# Cockpitdecks Desktop

Desktop companion app (PySide6/Qt) for setting up, updating, diagnosing, and launching Cockpitdecks.
It orchestrates existing Cockpitdecks tooling — it does **not** duplicate cockpitdecks core logic.

## Project layout

```
pyproject.toml                    # App package + build deps
src/cockpitdecks_desktop/
  app.py                          # Entry point (QApplication setup)
  icon_loader.py                  # App icon loading & non-square PNG normalization
  resources/                      # Bundled assets (app_icon.png)
  services/
    desktop_settings.py           # Persistent settings (paths, API URLs)
    live_apis.py                  # Polling cockpitdecks & X-Plane REST endpoints
    process_runner.py             # Subprocess streaming helper
    github_releases.py            # GitHub release fetch, download, install
  ui/
    main_window.py                # Main window: status dashboard, metrics, log, settings tab
    app_style.py                  # Global QSS stylesheet
    settings_dialog.py            # Settings form widget
packaging/pyinstaller/
  desktop.spec                    # PyInstaller spec (bundles launcher sidecar + icon)
scripts/
  build_desktop.sh                # One-shot PyInstaller build
```

## Development

```bash
uv sync
uv run cockpitdecks-desktop
```

## Build

```bash
uv sync --extra build
scripts/build_desktop.sh
```

Output goes to `dist/`.

## Key conventions

- Package manager: **uv**
- Build system: **Hatchling**
- Python >=3.12, PySide6 >=6.9
- UI style is set to **Fusion** (not native macOS) so QSS styling works consistently
- The app talks to cockpitdecks via its Flask endpoints (`/desktop-status`, `/desktop-metrics`) and to X-Plane via its REST Web API
- `resources/bin/cockpitdecks-launcher` is a sidecar binary — gitignored, bundled by PyInstaller at build time
- Debug artifacts (`variable-database-dump.yaml`, `ogimet_cache.sqlite`, `*.log`) are gitignored

## Related repos

- **cockpitdecks**: core framework monorepo (cockpitdecks + all extensions)
- **cockpitdecks-editor**: standalone config editor app (independent)
- **cockpitdecks-configs**: aircraft configuration files
- **cockpitdecks-docs**: documentation site
