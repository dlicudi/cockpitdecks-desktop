from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys
import traceback

from PySide6.QtWidgets import QApplication

from cockpitdecks_desktop.icon_loader import load_app_icon
from cockpitdecks_desktop.services.desktop_settings import settings_path
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


def _crash_log_path() -> Path:
    return settings_path().with_name("crash.log")


def _write_crash_log(exc: BaseException) -> Path | None:
    path = _crash_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(f"=== {datetime.now().isoformat(timespec='seconds')} ===\n")
            fp.write(f"Python: {sys.version}\n")
            fp.write(f"Executable: {sys.executable}\n")
            fp.write(f"Platform: {sys.platform}\n")
            fp.write(f"CWD: {os.getcwd()}\n")
            fp.write("Traceback:\n")
            fp.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            fp.write("\n")
        return path
    except OSError:
        return None


def main() -> int:
    try:
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
    except Exception as exc:
        crash_log = _write_crash_log(exc)
        msg = "Cockpitdecks Desktop crashed during startup."
        if crash_log is not None:
            msg += f" Crash log: {crash_log}"
        print(msg, file=sys.stderr)
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
