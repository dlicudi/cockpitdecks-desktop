"""Load the application window icon from package resources or PyInstaller bundle."""

from __future__ import annotations

import importlib.resources
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap


def _pixmap_to_square(pix: QPixmap, *, max_side: int = 1024) -> QPixmap:
    """Pad non-square artwork to a square using corner-averaged fill (avoids OS black letterboxing)."""
    if pix.isNull():
        return pix
    w, h = pix.width(), pix.height()
    if w == h:
        out = pix
    else:
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
        side = max(w, h)
        canvas = QPixmap(side, side)
        canvas.fill(bg)
        scaled = pix.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        ox = (side - scaled.width()) // 2
        oy = (side - scaled.height()) // 2
        painter = QPainter(canvas)
        painter.drawPixmap(ox, oy, scaled)
        painter.end()
        out = canvas

    if out.width() > max_side:
        return out.scaled(max_side, max_side, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return out


def _read_icon_bytes() -> bytes | None:
    """Resolve PNG bytes from checkout / bundle (avoid stale importlib.resources from old installs)."""
    here = Path(__file__).resolve().parent
    candidates = [
        here / "resources" / "app_icon.png",
    ]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)  # noqa: SLF001
        candidates.extend(
            [
                meipass / "cockpitdecks_desktop" / "resources" / "app_icon.png",
                meipass / "resources" / "app_icon.png",
            ]
        )

    for path in candidates:
        if path.is_file():
            try:
                return path.read_bytes()
            except OSError:
                continue

    try:
        ref = importlib.resources.files("cockpitdecks_desktop.resources").joinpath("app_icon.png")
        return ref.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, TypeError, OSError):
        return None


def load_app_icon() -> QIcon | None:
    """Return QIcon for dock / window chrome, or None if asset missing."""
    data = _read_icon_bytes()
    if data is None:
        return None
    pix = QPixmap()
    if not pix.loadFromData(data):
        return None
    pix = _pixmap_to_square(pix)
    return QIcon(pix)
