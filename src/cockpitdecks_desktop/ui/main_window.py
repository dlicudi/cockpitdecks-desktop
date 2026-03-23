from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import threading
from urllib.error import URLError
from urllib.request import urlopen

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_desktop.services.process_runner import stream_shell_command


@dataclass
class CommandStep:
    title: str
    command: str
    cwd: Path


class CommandWorker(QObject):
    line = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, steps: list[CommandStep]) -> None:
        super().__init__()
        self._steps = steps

    def run(self) -> None:
        for step in self._steps:
            self.line.emit(f"$ ({step.cwd}) {step.command}")
            rc = stream_shell_command(step.command, cwd=step.cwd, on_output=self.line.emit)
            if rc != 0:
                self.finished.emit(False, f"{step.title} failed (exit={rc})")
                return
        self.finished.emit(True, "All steps completed.")


class MainWindow(QMainWindow):
    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Cockpitdecks Desktop")
        self.resize(980, 640)
        self._thread: QThread | None = None
        self._worker: CommandWorker | None = None
        self._launcher_process = None
        self._launcher_log_thread: threading.Thread | None = None

        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Cockpitdecks Desktop")
        title.setObjectName("title")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")

        subtitle = QLabel("Setup, update, diagnose, and launch Cockpitdecks from one place.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #666;")

        actions = QHBoxLayout()
        self.btn_check = QPushButton("Run Preflight")
        self.btn_install = QPushButton("Install / Update")
        self.btn_launch = QPushButton("Launch Cockpitdecks")
        actions.addWidget(self.btn_check)
        actions.addWidget(self.btn_install)
        actions.addWidget(self.btn_launch)
        actions.addStretch(1)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Action logs will appear here...")

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(actions)
        root.addWidget(self.log, 1)

        status = QStatusBar(self)
        status.showMessage("Ready")
        self.setStatusBar(status)

        self.btn_check.clicked.connect(self.run_preflight)
        self.btn_install.clicked.connect(self.run_install_update)
        self.btn_launch.clicked.connect(self.launch_cockpitdecks)
        self.log_line.connect(self._append)

        self.log.setAlignment(Qt.AlignTop)

    def _append(self, text: str) -> None:
        self.log.append(text)

    def _workspace(self) -> Path:
        return Path.home() / "GitHub"

    def _repo(self, name: str) -> Path:
        return self._workspace() / name

    def _resolve_launcher_binary(self) -> Path:
        """Resolve cockpitdecks-launcher path for frozen and dev modes."""
        # Frozen desktop app: prefer bundled sidecar launcher.
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates = [
                exe_dir / "cockpitdecks-launcher",
                exe_dir / "resources" / "cockpitdecks-launcher",
                Path(getattr(sys, "_MEIPASS", exe_dir)) / "cockpitdecks-launcher",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return candidate
            # Return first candidate for clearer error messages if not found.
            return candidates[0]

        # Dev mode: run the local launcher built in cockpitdecks repo.
        return self._repo("cockpitdecks") / "dist" / "cockpitdecks-launcher"

    def _set_busy(self, busy: bool) -> None:
        self.btn_check.setEnabled(not busy)
        self.btn_install.setEnabled(not busy)
        self.btn_launch.setEnabled(not busy)
        self.statusBar().showMessage("Working..." if busy else "Ready")

    def _start_steps(self, steps: list[CommandStep]) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._append("[desktop] another task is already running")
            return
        self._set_busy(True)
        self._thread = QThread(self)
        self._worker = CommandWorker(steps)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.line.connect(self._append)
        self._worker.finished.connect(self._on_steps_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(lambda: self._set_busy(False))
        self._thread.start()

    def _on_steps_finished(self, ok: bool, message: str) -> None:
        tag = "ok" if ok else "error"
        self._append(f"[{tag}] {message}")

    def run_preflight(self) -> None:
        self._append("[preflight] running checks...")
        required_paths = {
            "cockpitdecks": self._repo("cockpitdecks"),
            "cockpitdecks_xp": self._repo("cockpitdecks_xp"),
            "xplane-webapi": self._repo("xplane-webapi"),
            "cockpitdecks-configs": self._repo("cockpitdecks-configs"),
        }
        missing = []
        for name, path in required_paths.items():
            exists = path.exists()
            self._append(f"[preflight] {name}: {'OK' if exists else 'MISSING'} ({path})")
            if not exists:
                missing.append(name)
        launcher = self._resolve_launcher_binary()
        self._append(f"[preflight] launcher binary: {'OK' if launcher.exists() else 'MISSING'} ({launcher})")
        try:
            with urlopen("http://127.0.0.1:8086/api/v3/capabilities", timeout=1.5) as response:
                self._append(f"[preflight] X-Plane Web API: OK ({response.status})")
        except URLError as exc:
            self._append(f"[preflight] X-Plane Web API: unavailable ({exc.reason})")
        if missing:
            self._append("[preflight] missing required repositories.")
        else:
            self._append("[preflight] complete.")

    def run_install_update(self) -> None:
        cockpit = self._repo("cockpitdecks")
        steps = [
            CommandStep("Install python-loupedeck-live", "python3 -m pip install -e ../python-loupedeck-live", cockpit),
            CommandStep("Install cockpitdecks-ld", "python3 -m pip install -e ../cockpitdecks_ld", cockpit),
            CommandStep("Install cockpitdecks", "python3 -m pip install -e .", cockpit),
            CommandStep("Build launcher", ".venv/bin/pyinstaller --clean cockpitdecks-launcher.spec", cockpit),
        ]
        self._start_steps(steps)

    def launch_cockpitdecks(self) -> None:
        launcher = self._resolve_launcher_binary()
        if not launcher.exists():
            self._append(f"[launch] launcher not found: {launcher}")
            return
        if self._launcher_process is not None and self._launcher_process.poll() is None:
            self._append(f"[launch] already running (pid={self._launcher_process.pid})")
            return
        self._launcher_process = subprocess.Popen(
            [str(launcher)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._append(f"[launch] started cockpitdecks (pid={self._launcher_process.pid})")
        self._start_launcher_log_stream()

    def _start_launcher_log_stream(self) -> None:
        proc = self._launcher_process
        if proc is None or proc.stdout is None:
            return

        def _reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                msg = line.rstrip("\n")
                if msg:
                    self.log_line.emit(msg)
            rc = proc.poll()
            if rc is not None:
                self.log_line.emit(f"[launch] cockpitdecks exited (code={rc})")

        self._launcher_log_thread = threading.Thread(target=_reader, name="LauncherLogStream", daemon=True)
        self._launcher_log_thread.start()
