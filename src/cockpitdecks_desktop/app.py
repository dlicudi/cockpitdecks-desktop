from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from cockpitdecks_desktop.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Cockpitdecks Desktop")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
