#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COCKPITDECKS_REPO="${COCKPITDECKS_REPO:-$HOME/GitHub/cockpitdecks}"
LAUNCHER_SRC="$COCKPITDECKS_REPO/dist/cockpitdecks-launcher"
LAUNCHER_DST="$ROOT_DIR/resources/bin/cockpitdecks-launcher"
LAUNCHER_BUILD_SCRIPT="$COCKPITDECKS_REPO/scripts/build_launcher.sh"

mkdir -p "$(dirname "$LAUNCHER_DST")"

if [[ ! -x "$LAUNCHER_SRC" ]]; then
  echo "[build] launcher binary not found at $LAUNCHER_SRC"
  echo "[build] building launcher in $COCKPITDECKS_REPO"
  if [[ ! -x "$LAUNCHER_BUILD_SCRIPT" ]]; then
    echo "[build] expected launcher build script not found: $LAUNCHER_BUILD_SCRIPT"
    exit 1
  fi
  (cd "$COCKPITDECKS_REPO" && "$LAUNCHER_BUILD_SCRIPT")
fi

cp "$LAUNCHER_SRC" "$LAUNCHER_DST"
chmod +x "$LAUNCHER_DST"
echo "[build] bundled launcher sidecar: $LAUNCHER_DST"

python3 -m pip install -e .
pyinstaller --clean "packaging/pyinstaller/desktop.spec"

echo "Build complete: $ROOT_DIR/dist/cockpitdecks-desktop"
