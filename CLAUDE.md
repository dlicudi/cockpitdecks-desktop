# Cockpitdecks Monorepo

Monorepo for Cockpitdecks and all first-party extensions, managed with uv workspaces.

## Project layout

```
pyproject.toml                         # uv workspace root (members = packages/*)
packages/
  cockpitdecks/                        # Core library + Flask server
    cockpitdecks/                      # Source package (flat layout)
    pyproject.toml
  cockpitdecks_xp/                     # X-Plane simulator interface
  cockpitdecks_wm/                     # Weather module
  cockpitdecks_ext/                    # Extra button/deck types
  cockpitdecks_ld/                     # Loupedeck deck driver integration
  cockpitdecks_sd/                     # Stream Deck driver integration
  cockpitdecks_bx/                     # Behringer X-Touch Mini integration
  xpwebapi/                            # X-Plane REST Web API client
  cockpitdecks_desktop/                # PySide6 desktop companion app
    src/cockpitdecks_desktop/
      app.py                           # Entry point (QApplication setup)
      icon_loader.py                   # App icon loading & non-square PNG normalization
      resources/                       # Bundled assets (app_icon.png)
      services/
        desktop_settings.py            # Persistent settings (paths, API URLs)
        live_apis.py                   # Polling cockpitdecks & X-Plane REST endpoints
        process_runner.py              # Subprocess streaming helper
      ui/
        main_window.py                 # Main window: status dashboard, metrics, log, settings tab
        app_style.py                   # Global QSS stylesheet
        settings_dialog.py             # Settings form widget
    packaging/pyinstaller/
      desktop.spec                     # PyInstaller spec (bundles launcher sidecar + icon)
    scripts/
      build_desktop.sh                 # One-shot PyInstaller build
  cockpitdecks_editor/                 # PySide6 config editor
    src/cockpitdecks_editor/
```

## Development

```bash
uv sync
uv run cockpitdecks-desktop
```

To work on a specific package:

```bash
uv run --package cockpitdecks-desktop cockpitdecks-desktop
```

## Build (desktop app)

```bash
packages/cockpitdecks_desktop/scripts/build_desktop.sh
```

Output goes to `packages/cockpitdecks_desktop/dist/`.

## Key conventions

- Package manager: **uv** with workspaces
- Build system: **Hatchling** (all packages)
- Cross-package dependencies use `[tool.uv.sources]` workspace references — no git URLs between workspace members
- Hardware driver libs (`python-loupedeck-live`, `python-elgato-streamdeck`) remain external git dependencies
- UI style is set to **Fusion** (not native macOS) so QSS styling works consistently
- The desktop app talks to cockpitdecks via its Flask endpoints (`/desktop-status`, `/desktop-metrics`) and to X-Plane via its REST Web API
- `packages/cockpitdecks_desktop/resources/bin/cockpitdecks-launcher` is a sidecar binary — gitignored, bundled by PyInstaller at build time
- Debug artifacts (`variable-database-dump.yaml`, `ogimet_cache.sqlite`, `*.log`) are gitignored

## Separate repos (not in monorepo)

- **cockpitdecks-configs**: aircraft configuration files (content, not code)
- **cockpitdecks-docs**: documentation site
- **python-loupedeck-live**: Loupedeck hardware driver
- **python-elgato-streamdeck**: Stream Deck hardware driver
