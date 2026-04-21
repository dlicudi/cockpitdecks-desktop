#!/usr/bin/env python3
"""Rewrite app_icon.png as a square master (macOS / PyInstaller expect square icons).

Widescreen PNGs are letterboxed by the OS with black bars. This script fits the
image inside a square canvas padded with the average corner color, then exports
1024×1024 for dock / .icns workflows.

Run from repo root: python3 scripts/square_app_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_PATH = ROOT / "src" / "cockpitdecks_desktop" / "resources" / "app_icon.png"
OUTPUT_SIDE = 1024


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap

    if not ICON_PATH.is_file():
        print(f"Missing {ICON_PATH}", file=sys.stderr)
        return 1

    _app = QGuiApplication(sys.argv)  # noqa: F841 — required for some pixmap backends

    pix = QPixmap(str(ICON_PATH))
    if pix.isNull():
        print(f"Could not load {ICON_PATH}", file=sys.stderr)
        return 1

    w, h = pix.width(), pix.height()
    img = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    corners = [
        img.pixelColor(0, 0),
        img.pixelColor(w - 1, 0),
        img.pixelColor(0, h - 1),
        img.pixelColor(w - 1, h - 1),
    ]
    bg = QColor(
        sum(c.red() for c in corners) // 4,
        sum(c.green() for c in corners) // 4,
        sum(c.blue() for c in corners) // 4,
        255,
    )

    inner_side = max(w, h)
    canvas = QPixmap(inner_side, inner_side)
    canvas.fill(bg)
    scaled = pix.scaled(inner_side, inner_side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    ox = (inner_side - scaled.width()) // 2
    oy = (inner_side - scaled.height()) // 2
    painter = QPainter(canvas)
    painter.drawPixmap(ox, oy, scaled)
    painter.end()

    if inner_side != OUTPUT_SIDE:
        canvas = canvas.scaled(
            OUTPUT_SIDE,
            OUTPUT_SIDE,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    if not canvas.save(str(ICON_PATH), "PNG"):
        print(f"Failed to write {ICON_PATH}", file=sys.stderr)
        return 1

    print(f"Wrote square {OUTPUT_SIDE}×{OUTPUT_SIDE} PNG → {ICON_PATH} (was {w}×{h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
