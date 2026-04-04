#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/pyinstaller" ]]; then
  PYINSTALLER=".venv/bin/pyinstaller"
elif [[ -x ".venv/Scripts/pyinstaller.exe" ]]; then
  PYINSTALLER=".venv/Scripts/pyinstaller.exe"
else
  PYINSTALLER="pyinstaller"
fi

python -m pip install -e .

# Generate .icns for macOS app bundle (no-op if not on macOS).
if [[ "$(uname)" == "Darwin" ]]; then
  "$ROOT_DIR/scripts/generate_icns.sh"
fi

"$PYINSTALLER" --clean "packaging/pyinstaller/desktop.spec"

if [[ "$(uname)" == "Darwin" ]]; then
  echo "Build complete: $ROOT_DIR/dist/Cockpitdecks Desktop.app"
else
  echo "Build complete: $ROOT_DIR/dist/cockpitdecks-desktop"
fi
