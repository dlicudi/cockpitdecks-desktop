#!/usr/bin/env bash
# Generate app_icon.icns from app_icon.png (macOS only).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT_DIR/src/cockpitdecks_desktop/resources/app_icon.png"
DST="$ROOT_DIR/src/cockpitdecks_desktop/resources/app_icon.icns"
ICONSET="$(mktemp -d)/app_icon.iconset"

mkdir -p "$ICONSET"
sips -z   16   16 "$SRC" --out "$ICONSET/icon_16x16.png"      >/dev/null
sips -z   32   32 "$SRC" --out "$ICONSET/icon_16x16@2x.png"   >/dev/null
sips -z   32   32 "$SRC" --out "$ICONSET/icon_32x32.png"      >/dev/null
sips -z   64   64 "$SRC" --out "$ICONSET/icon_32x32@2x.png"   >/dev/null
sips -z  128  128 "$SRC" --out "$ICONSET/icon_128x128.png"    >/dev/null
sips -z  256  256 "$SRC" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z  256  256 "$SRC" --out "$ICONSET/icon_256x256.png"    >/dev/null
sips -z  512  512 "$SRC" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z  512  512 "$SRC" --out "$ICONSET/icon_512x512.png"    >/dev/null
sips -z 1024 1024 "$SRC" --out "$ICONSET/icon_512x512@2x.png" >/dev/null

iconutil -c icns "$ICONSET" -o "$DST"
rm -rf "$(dirname "$ICONSET")"

echo "[icns] generated: $DST"
