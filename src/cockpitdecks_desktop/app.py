from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from cockpitdecks_desktop.icon_loader import load_app_icon
from cockpitdecks_desktop.ui.main_window import MainWindow


def _macos_set_foreground_app() -> None:
    """Tell macOS this process is a regular foreground GUI app.

    Without this, PyInstaller one-file bundles start as "accessory" apps —
    the Dock icon bounces, the window appears then hides, and finally
    reappears once the OS reclassifies the process.  Setting the activation
    policy explicitly before showing any window avoids the cycle.
    """
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyRegular  # type: ignore[import-not-found]

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyRegular)
    except ImportError:
        pass


def main() -> int:
    if sys.platform == "darwin":
        _macos_set_foreground_app()

    app = QApplication(sys.argv)
    # Native "macintosh" style largely ignores QPushButton QSS; Fusion matches styled buttons on macOS.
    app.setStyle("Fusion")
    app.setApplicationName("Cockpitdecks Desktop")
    app.setApplicationDisplayName("Cockpitdecks Desktop")

    # Set icon before creating the window so it is used from the first frame.
    icon = load_app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    win = MainWindow()
    win.show()

    if sys.platform == "darwin":
        # Bring window to front after show() so it doesn't sit behind other apps.
        try:
            from AppKit import NSApplication  # type: ignore[import-not-found]

            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except ImportError:
            win.raise_()
            win.activateWindow()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
