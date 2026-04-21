#!/usr/bin/env bash
# After changing app_icon.png or rebuilding, macOS often keeps the old Dock / Finder icon.
# Run this, then start the app again (and remove + re-pin Dock icon if needed).
set -euo pipefail

echo "[icon] Restarting Dock (clears many in-memory icon caches)…"
killall Dock 2>/dev/null || true

if [[ -n "${1:-}" ]]; then
  APP="$1"
  if [[ -e "$APP" ]]; then
    echo "[icon] Touching: $APP"
    /usr/bin/touch "$APP"
  else
    echo "[icon] Path not found: $APP" >&2
  fi
fi

echo "[icon] Done. If the icon is still wrong:"
echo "    • PyInstaller: rebuild with  pyinstaller --clean packaging/pyinstaller/desktop.spec"
echo "    • Dev: run from this repo after  pip install -e .  and quit all old app instances"
echo "    • Remove the app from the Dock, then open it again from Finder/dist"
