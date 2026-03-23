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
