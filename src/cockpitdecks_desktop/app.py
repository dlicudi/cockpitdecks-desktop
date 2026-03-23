from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from cockpitdecks_desktop.icon_loader import load_app_icon
from cockpitdecks_desktop.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    # Native "macintosh" style largely ignores QPushButton QSS; Fusion matches styled buttons on macOS.
    app.setStyle("Fusion")
    app.setApplicationName("Cockpitdecks Desktop")
    icon = load_app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    win = MainWindow()
    if icon is not None:
        win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
