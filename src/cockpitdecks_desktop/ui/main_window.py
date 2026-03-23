from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_desktop.services.desktop_settings import (
    cockpit_web_base,
    launcher_binary_path,
    load as load_desktop_settings,
    launch_env_overlay,
    settings_path,
    xplane_rest_base,
)
from cockpitdecks_desktop.services.live_apis import (
    cockpitdecks_metrics_json,
    cockpitdecks_metrics_status_line,
    cockpitdecks_session_status_line,
    cockpitdecks_web_status_line,
    xplane_capabilities_status_line,
)
from cockpitdecks_desktop.services.process_runner import stream_shell_command
from cockpitdecks_desktop.ui.app_style import MAIN_WINDOW_QSS
from cockpitdecks_desktop.ui.settings_dialog import SettingsFormWidget


@dataclass
class CommandStep:
    title: str
    command: str
    cwd: Path


def _shorten_filesystem_path(path: Path | str, *, max_len: int = 72) -> str:
    try:
        s = str(Path(path).expanduser().resolve())
    except OSError:
        s = str(Path(path).expanduser())
    home = str(Path.home())
    if s.startswith(home):
        s = "~" + s[len(home):]
    if len(s) <= max_len:
        return s
    head = max_len // 2 - 2
    tail = max_len - head - 3
    return s[:head] + "…" + s[-tail:]


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
    live_poll_done = Signal(str, str, str, str, str, object)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Cockpitdecks Desktop")
        self.resize(980, 640)
        self._thread: QThread | None = None
        self._worker: CommandWorker | None = None
        self._launcher_process = None
        self._launcher_log_thread: threading.Thread | None = None
        self._live_poll_lock = threading.Lock()

        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Cockpitdecks Desktop")
        title.setObjectName("title")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")

        subtitle = QLabel(
            "Use <b>Config</b> for paths and API settings. This tab shows live connectivity and launcher state."
        )
        subtitle.setWordWrap(True)
        subtitle.setTextFormat(Qt.RichText)
        subtitle.setStyleSheet("color: #5c5c5c; font-size: 13px;")

        self.info_desktop = QLabel("—")
        self.info_launcher = QLabel("—")
        self.info_xplane = QLabel("…")
        self.info_cockpit_web = QLabel("…")
        self.info_session = QLabel("…")
        self.info_live_poll_at = QLabel("—")
        self.info_last_check = QLabel("—")
        self.info_runtime_metrics = QLabel("—")
        for lab in (
            self.info_desktop,
            self.info_launcher,
            self.info_xplane,
            self.info_cockpit_web,
            self.info_session,
            self.info_live_poll_at,
            self.info_last_check,
            self.info_runtime_metrics,
        ):
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            lab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.info_panel = QFrame()
        self.info_panel.setObjectName("statusCard")
        self.info_panel.setStyleSheet(
            """
            QFrame#statusCard {
                background-color: #f4f5f7;
                border: 1px solid #dadde3;
                border-radius: 10px;
            }
            QLabel#statusKeyLabel {
                color: #4a4a4a;
                font-size: 12px;
                font-weight: 600;
            }
            """
        )
        info_layout = QVBoxLayout(self.info_panel)
        info_layout.setContentsMargins(0, 8, 0, 8)
        info_layout.setSpacing(0)

        def section_title(t: str) -> QLabel:
            w = QLabel(t)
            w.setStyleSheet(
                "font-size: 11px; font-weight: 700; color: #6e7380; letter-spacing: 0.04em; "
                "text-transform: uppercase; padding: 14px 16px 6px 16px;"
            )
            return w

        def add_row(key: str, value: QLabel) -> None:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(16, 8, 16, 8)
            hl.setSpacing(16)
            kl = QLabel(key)
            kl.setObjectName("statusKeyLabel")
            kl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            kl.setFixedWidth(200)
            kl.setWordWrap(True)
            hl.addWidget(kl, 0, Qt.AlignmentFlag.AlignTop)
            hl.addWidget(value, 1, Qt.AlignmentFlag.AlignTop)
            info_layout.addWidget(row)

        info_layout.addWidget(section_title("Application"))
        add_row("Desktop app", self.info_desktop)
        add_row("Launcher", self.info_launcher)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("color: #dadde3; max-height: 1px; margin-left: 12px; margin-right: 12px;")
        info_layout.addWidget(sep1)

        info_layout.addWidget(section_title("Live connectivity"))
        add_row("X-Plane Web API", self.info_xplane)
        add_row("Cockpitdecks web UI", self.info_cockpit_web)
        add_row("Loaded session", self.info_session)
        add_row("Last poll time", self.info_live_poll_at)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #dadde3; max-height: 1px; margin-left: 12px; margin-right: 12px;")
        info_layout.addWidget(sep2)

        info_layout.addWidget(section_title("Diagnostics"))
        add_row("Last preflight", self.info_last_check)
        add_row("Runtime metrics", self.info_runtime_metrics)

        self.metrics_card = QFrame()
        self.metrics_card.setObjectName("metricsCard")
        self.metrics_card.setStyleSheet(
            """
            QFrame#metricsCard {
                background-color: #f4f7fb;
                border: 1px solid #dbe4f0;
                border-radius: 10px;
            }
            """
        )
        metrics_layout = QVBoxLayout(self.metrics_card)
        metrics_layout.setContentsMargins(14, 12, 14, 12)
        metrics_layout.setSpacing(10)
        metrics_title = QLabel("Runtime Metrics")
        metrics_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #5f6b7a; text-transform: uppercase;")
        metrics_layout.addWidget(metrics_title)

        self.metric_cpu_label = QLabel("CPU —")
        self.metric_cpu_bar = QProgressBar()
        self.metric_cpu_bar.setRange(0, 100)
        self.metric_cpu_bar.setValue(0)
        self.metric_cpu_bar.setTextVisible(False)

        self.metric_mem_label = QLabel("Memory —")
        self.metric_mem_bar = QProgressBar()
        self.metric_mem_bar.setRange(0, 100)
        self.metric_mem_bar.setValue(0)
        self.metric_mem_bar.setTextVisible(False)

        self.metric_summary = QLabel("Threads —   |   Variables —   |   Datarefs —   |   Uptime —")
        self.metric_summary.setStyleSheet("color: #475569; font-size: 12px;")

        metrics_layout.addWidget(self.metric_cpu_label)
        metrics_layout.addWidget(self.metric_cpu_bar)
        metrics_layout.addWidget(self.metric_mem_label)
        metrics_layout.addWidget(self.metric_mem_bar)
        metrics_layout.addWidget(self.metric_summary)

        actions_outer = QFrame()
        actions_outer.setObjectName("actionBar")
        av = QVBoxLayout(actions_outer)
        av.setContentsMargins(12, 12, 12, 12)
        av.setSpacing(10)
        status_actions = QHBoxLayout()
        status_actions.setSpacing(10)
        self.btn_refresh = QPushButton("Refresh status")
        self.btn_check = QPushButton("Run Preflight")
        self.btn_update = QPushButton("Check Updates")
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("primaryButton")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart = QPushButton("Restart")
        self.btn_restart.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("stopButton")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        for b in (self.btn_refresh, self.btn_check, self.btn_update):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        status_actions.addWidget(self.btn_refresh)
        status_actions.addWidget(self.btn_check)
        status_actions.addWidget(self.btn_update)
        status_actions.addStretch(1)
        status_actions2 = QHBoxLayout()
        status_actions2.setSpacing(10)
        status_actions2.addWidget(self.btn_start)
        status_actions2.addWidget(self.btn_restart)
        status_actions2.addWidget(self.btn_stop)
        status_actions2.addStretch(1)
        av.addLayout(status_actions)
        av.addLayout(status_actions2)

        status_footer = QLabel(
            "Tip: Run <b>Preflight</b> to print the same checks to the Logs tab. "
            "If the web port is busy, change <b>Poll: Cockpitdecks web port</b> on the Config tab."
        )
        status_footer.setWordWrap(True)
        status_footer.setTextFormat(Qt.RichText)
        status_footer.setStyleSheet("color: #7a7f8c; font-size: 12px; padding: 8px 2px 0 2px;")

        self.status_feedback = QLabel("Last action: Ready")
        self.status_feedback.setWordWrap(True)
        self.status_feedback.setTextFormat(Qt.PlainText)
        self.status_feedback.setStyleSheet(
            "background: #f5f7fb; border: 1px solid #d7dceb; border-radius: 8px; "
            "padding: 8px 10px; color: #334155; font-size: 12px;"
        )

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Preflight, launch, and Cockpitdecks output will appear here…")
        self.log.setStyleSheet("font-family: Menlo, Monaco, monospace; font-size: 12px;")

        status_inner = QWidget()
        status_inner_layout = QVBoxLayout(status_inner)
        status_inner_layout.setContentsMargins(8, 8, 8, 12)
        status_inner_layout.setSpacing(12)
        status_inner_layout.addWidget(title)
        status_inner_layout.addWidget(subtitle)
        status_inner_layout.addWidget(actions_outer)
        status_inner_layout.addWidget(self.status_feedback)
        status_inner_layout.addWidget(self.info_panel)
        status_inner_layout.addWidget(self.metrics_card)
        status_inner_layout.addWidget(status_footer)

        status_scroll = QScrollArea()
        status_scroll.setWidgetResizable(True)
        status_scroll.setFrameShape(QFrame.Shape.NoFrame)
        status_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        status_scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        status_scroll.setWidget(status_inner)

        tab_status = QWidget()
        tab_status_layout = QVBoxLayout(tab_status)
        tab_status_layout.setContentsMargins(0, 0, 0, 0)
        tab_status_layout.setSpacing(0)
        tab_status_layout.addWidget(status_scroll, 1)

        tab_config = QWidget()
        tab_config_layout = QVBoxLayout(tab_config)
        tab_config_layout.setContentsMargins(0, 0, 0, 0)
        tab_config_layout.setSpacing(0)
        self.settings_form = SettingsFormWidget(tab_config, load_desktop_settings())
        settings_scroll = QScrollArea(tab_config)
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_scroll.setWidget(self.settings_form)
        tab_config_layout.addWidget(settings_scroll, 1)

        tab_logs = QWidget()
        tab_logs_layout = QVBoxLayout(tab_logs)
        tab_logs_layout.setContentsMargins(8, 8, 8, 8)
        tab_logs_layout.setSpacing(8)
        logs_bar = QHBoxLayout()
        self.btn_clear_logs = QPushButton("Clear log")
        logs_bar.addWidget(self.btn_clear_logs)
        logs_bar.addStretch(1)
        tab_logs_layout.addLayout(logs_bar)
        tab_logs_layout.addWidget(self.log, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(tab_status, "Status")
        self.tabs.addTab(tab_config, "Config")
        self.tabs.addTab(tab_logs, "Logs")

        root.addWidget(self.tabs, 1)

        status = QStatusBar(self)
        status.showMessage("Ready")
        self.setStatusBar(status)

        self.btn_refresh.clicked.connect(self.refresh_info_panel)
        self.btn_check.clicked.connect(self.run_preflight)
        self.btn_update.clicked.connect(self.check_updates)
        self.btn_start.clicked.connect(self.start_cockpitdecks)
        self.btn_restart.clicked.connect(self.restart_cockpitdecks)
        self.btn_stop.clicked.connect(self.stop_cockpitdecks)
        self.btn_clear_logs.clicked.connect(self.log.clear)
        self.log_line.connect(self._append)
        self.live_poll_done.connect(self._apply_live_poll)
        self.settings_form.settings_saved.connect(self._on_settings_saved)

        self.log.setAlignment(Qt.AlignTop)
        self.setStyleSheet(MAIN_WINDOW_QSS)
        self.btn_clear_logs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_info_panel()
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._schedule_live_poll)
        self._live_timer.start(4000)

    def _append(self, text: str) -> None:
        self.log.append(text)
        self._set_status_feedback(text)

    def _set_status_feedback(self, text: str) -> None:
        msg = (text or "").strip()
        if not msg:
            return
        # Keep status page concise; clip very long lines from verbose logs.
        if len(msg) > 220:
            msg = msg[:217] + "..."
        self.status_feedback.setText(f"Last action: {msg}")

    def _workspace(self) -> Path:
        return Path.home() / "GitHub"

    def _repo(self, name: str) -> Path:
        return self._workspace() / name

    def _resolve_launcher_binary(self) -> Path:
        """Resolve cockpitdecks-launcher: Config override, else bundled (frozen), else dev dist."""
        override = launcher_binary_path(load_desktop_settings())
        if override is not None:
            return override

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

    def _command_worker_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _refresh_start_stop_buttons(self, *, busy: bool | None = None) -> None:
        """Dedicated Start / Stop: enabled based on process state, web port, and optional command-worker busy."""
        cmd_busy = self._command_worker_busy() if busy is None else busy
        launcher_ok = self._resolve_launcher_binary().exists()
        running = self._launcher_is_running()
        port_listener = self._cockpit_web_port_listener()
        port_in_use = port_listener is not None
        self.btn_stop.setEnabled(not cmd_busy and running)
        can_restart = not cmd_busy and launcher_ok and (running or not port_in_use)
        self.btn_restart.setEnabled(can_restart)
        can_start = not cmd_busy and not running and launcher_ok and not port_in_use
        self.btn_start.setEnabled(can_start)
        wport = self._web_listen_port()
        if can_start:
            self.btn_start.setToolTip("Start cockpitdecks-launcher using paths and env from the Config tab.")
        elif cmd_busy:
            self.btn_start.setToolTip("Disabled while another task is running (e.g. Preflight). Wait for it to finish.")
        elif running:
            self.btn_start.setToolTip("Launcher was already started from this app. Use Stop, then you can Start again.")
        elif not launcher_ok:
            p = self._resolve_launcher_binary()
            self.btn_start.setToolTip(
                f"Launcher binary not found:\n{p}\n\n"
                f"Set “cockpitdecks-launcher path” on the Config tab, build the launcher in the cockpitdecks repo, "
                f"or use a desktop build that bundles it."
            )
        elif port_in_use:
            pid, name = port_listener if port_listener else (0, "?")
            self.btn_start.setToolTip(
                f"Port :{wport} is already in use (pid {pid}, {name}).\n\n"
                f"Stop that process, or change “Poll: Cockpitdecks web port” on the Config tab if Cockpitdecks uses another port."
            )
        else:
            self.btn_start.setToolTip("Start is unavailable.")
        if can_restart:
            self.btn_restart.setToolTip("Restart cockpitdecks-launcher.")
        elif cmd_busy:
            self.btn_restart.setToolTip("Disabled while another task is running.")
        elif not launcher_ok:
            self.btn_restart.setToolTip("Launcher binary not found.")
        elif port_in_use and not running:
            pid, name = port_listener if port_listener else (0, "?")
            self.btn_restart.setToolTip(f"Port :{wport} in use by pid {pid} ({name}); cannot restart from this app.")
        else:
            self.btn_restart.setToolTip("Restart is unavailable.")

    def _set_busy(self, busy: bool) -> None:
        self.btn_refresh.setEnabled(not busy)
        self.btn_check.setEnabled(not busy)
        self.btn_update.setEnabled(not busy)
        self._refresh_start_stop_buttons(busy=busy)
        self.statusBar().showMessage("Working..." if busy else "Ready")
        if busy:
            self._set_status_feedback("Working...")
        else:
            self._set_status_feedback("Ready")

    def _launcher_is_running(self) -> bool:
        return self._launcher_process is not None and self._launcher_process.poll() is None

    def _web_listen_port(self) -> int:
        try:
            return int((load_desktop_settings().get("COCKPIT_WEB_PORT") or "7777").strip() or "7777")
        except ValueError:
            return 7777

    def _cockpit_web_port_listener(self) -> tuple[int, str] | None:
        """Return (pid, process_name) if a process listens on the configured Cockpitdecks web TCP port."""
        port = self._web_listen_port()
        cmd = ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError:
            return None
        if proc.returncode != 0 or not proc.stdout:
            return None
        pid: int | None = None
        cmd_name = "unknown"
        for raw in proc.stdout.splitlines():
            if raw.startswith("p"):
                try:
                    pid = int(raw[1:])
                except ValueError:
                    pid = None
            elif raw.startswith("c"):
                cmd_name = raw[1:] or "unknown"
            if pid is not None:
                return (pid, cmd_name)
        return None

    def _on_settings_saved(self) -> None:
        self.statusBar().showMessage(f"Settings saved — {settings_path().name}", 4000)
        self.refresh_info_panel()

    def _desktop_app_version(self) -> str:
        try:
            return importlib.metadata.version("cockpitdecks-desktop")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    @staticmethod
    def _style_status_value(label: QLabel, text: str) -> None:
        """Semantic colors for status value labels (does not affect row keys)."""
        t = text.lower().strip()
        base = "font-size: 13px;"
        if any(k in t for k in ("unreachable", "missing", "http error", "connection refused", "errno")):
            label.setStyleSheet(base + "color: #b00020; font-weight: 500;")
        elif any(
            k in t
            for k in (
                "not running",
                "could not read",
                "invalid json",
                "desktop-status missing",
                "update cockpitdecks",
            )
        ):
            label.setStyleSheet(base + "color: #757575;")
        elif t.startswith("ok (") or (len(t) > 1 and t[0] == "v" and t[1].isdigit()):
            label.setStyleSheet(base + "color: #1b5e20; font-weight: 500;")
        elif t.startswith("running") or (t.startswith("ready") and "unreachable" not in t):
            label.setStyleSheet(base + "color: #1565c0; font-weight: 500;")
        else:
            label.setStyleSheet(base + "color: #242424;")

    def _refresh_status_value_styles(self) -> None:
        self._style_status_value(self.info_desktop, self.info_desktop.text())
        self._style_status_value(self.info_launcher, self.info_launcher.text())
        self._style_status_value(self.info_xplane, self.info_xplane.text())
        self._style_status_value(self.info_cockpit_web, self.info_cockpit_web.text())
        self._style_status_value(self.info_session, self.info_session.text())
        self._style_status_value(self.info_live_poll_at, self.info_live_poll_at.text())
        self._style_status_value(self.info_last_check, self.info_last_check.text())
        self._style_status_value(self.info_runtime_metrics, self.info_runtime_metrics.text())

    def _apply_metrics_visuals(self, metrics: dict | None) -> None:
        if not isinstance(metrics, dict):
            self.metric_cpu_label.setText("CPU —")
            self.metric_mem_label.setText("Memory —")
            self.metric_cpu_bar.setValue(0)
            self.metric_mem_bar.setValue(0)
            self.metric_summary.setText("Threads —   |   Variables —   |   Datarefs —   |   Uptime —")
            return

        p = metrics.get("process") if isinstance(metrics.get("process"), dict) else {}
        c = metrics.get("cockpit") if isinstance(metrics.get("cockpit"), dict) else {}
        s = metrics.get("simulator") if isinstance(metrics.get("simulator"), dict) else {}

        cpu = p.get("cpu_percent")
        rss_mb = p.get("max_rss_mb")
        threads = p.get("thread_count")
        vars_n = c.get("registered_variables")
        drefs = s.get("datarefs_monitored")
        uptime_s = metrics.get("uptime_s")

        cpu_val = int(max(0.0, min(100.0, float(cpu)))) if isinstance(cpu, (int, float)) else 0
        self.metric_cpu_bar.setValue(cpu_val)
        self.metric_cpu_label.setText(f"CPU {float(cpu):.1f}%" if isinstance(cpu, (int, float)) else "CPU —")
        if cpu_val >= 85:
            self.metric_cpu_bar.setStyleSheet("QProgressBar::chunk { background-color: #d14343; }")
        elif cpu_val >= 60:
            self.metric_cpu_bar.setStyleSheet("QProgressBar::chunk { background-color: #d28a2e; }")
        else:
            self.metric_cpu_bar.setStyleSheet("QProgressBar::chunk { background-color: #2f9e44; }")

        if isinstance(rss_mb, (int, float)):
            mem_pct = int(max(0.0, min(100.0, (float(rss_mb) / 4096.0) * 100.0)))
            self.metric_mem_bar.setValue(mem_pct)
            self.metric_mem_label.setText(f"Memory {float(rss_mb):.1f} MB")
        else:
            self.metric_mem_bar.setValue(0)
            self.metric_mem_label.setText("Memory —")
        self.metric_mem_bar.setStyleSheet("QProgressBar::chunk { background-color: #3b82f6; }")

        threads_s = str(threads) if isinstance(threads, int) else "—"
        vars_s = str(vars_n) if isinstance(vars_n, int) else "—"
        drefs_s = str(drefs) if isinstance(drefs, int) else "—"
        if isinstance(uptime_s, (int, float)):
            uptime_i = int(uptime_s)
            h, rem = divmod(uptime_i, 3600)
            m, sec = divmod(rem, 60)
            uptime_txt = f"{h:d}:{m:02d}:{sec:02d}"
        else:
            uptime_txt = "—"
        self.metric_summary.setText(f"Threads {threads_s}   |   Variables {vars_s}   |   Datarefs {drefs_s}   |   Uptime {uptime_txt}")

    def refresh_info_panel(self) -> None:
        self.info_desktop.setText(f"v{self._desktop_app_version()}")

        launcher = self._resolve_launcher_binary()
        wport = self._web_listen_port()
        tip_lines: list[str] = [f"Full path:\n{launcher}"]
        path_disp = _shorten_filesystem_path(launcher)
        if launcher.exists():
            launcher_status = "Running" if self._launcher_is_running() else "Ready"
            listener = self._cockpit_web_port_listener()
            if listener is not None:
                tip_lines.append(f"Listener on port {wport}: pid {listener[0]} ({listener[1]})")
                self.info_launcher.setText(
                    f"{launcher_status}  ·  {path_disp}  ·  port {wport} in use (pid {listener[0]})"
                )
            else:
                self.info_launcher.setText(f"{launcher_status}  ·  {path_disp}")
        else:
            self.info_launcher.setText(f"Missing  ·  {path_disp}")
        self.info_launcher.setToolTip("\n\n".join(tip_lines))

        self._refresh_status_value_styles()
        self._refresh_start_stop_buttons()
        self._schedule_live_poll()

    def _schedule_live_poll(self) -> None:
        if not self._live_poll_lock.acquire(blocking=False):
            return

        def work() -> None:
            try:
                st = load_desktop_settings()
                xp_base = xplane_rest_base(st)
                web_base = cockpit_web_base(st)
                xp_line, _ = xplane_capabilities_status_line(base_url=xp_base)
                web_line, _ = cockpitdecks_web_status_line(url=f"{web_base}/")
                session_line = cockpitdecks_session_status_line(base_url=web_base)
                metrics_line = cockpitdecks_metrics_status_line(base_url=web_base)
                metrics_obj, _ = cockpitdecks_metrics_json(base_url=web_base)
                ts = datetime.now().strftime("%H:%M:%S")
                self.live_poll_done.emit(xp_line, web_line, session_line, metrics_line, ts, metrics_obj)
            finally:
                self._live_poll_lock.release()

        threading.Thread(target=work, name="LiveApiPoll", daemon=True).start()

    def _apply_live_poll(
        self,
        xplane_line: str,
        cockpit_web_line: str,
        session_line: str,
        metrics_line: str,
        polled_at: str,
        metrics_obj: dict | None,
    ) -> None:
        self.info_xplane.setText(xplane_line)
        self.info_cockpit_web.setText(cockpit_web_line)
        self.info_session.setText(session_line)
        self.info_runtime_metrics.setText(metrics_line)
        self._apply_metrics_visuals(metrics_obj)
        self.info_live_poll_at.setText(polled_at)
        self._refresh_status_value_styles()

    def _mark_preflight_time(self) -> None:
        self.info_last_check.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

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
        self.refresh_info_panel()

    def run_preflight(self) -> None:
        self._mark_preflight_time()
        self._append("[preflight] running checks...")
        self._append(f"[preflight] desktop app: v{self._desktop_app_version()}")
        launcher = self._resolve_launcher_binary()
        self._append(f"[preflight] launcher binary: {'OK' if launcher.exists() else 'MISSING'} ({launcher})")
        st = load_desktop_settings()
        wport = self._web_listen_port()
        listener = self._cockpit_web_port_listener()
        if listener is not None:
            self._append(f"[preflight] TCP :{wport}: IN USE pid={listener[0]} ({listener[1]})")
        else:
            self._append(f"[preflight] TCP :{wport}: free")
        xp_line, _ = xplane_capabilities_status_line(base_url=xplane_rest_base(st))
        self._append(f"[preflight] X-Plane API: {xp_line}")
        web_line, _ = cockpitdecks_web_status_line(url=f"{cockpit_web_base(st)}/")
        self._append(f"[preflight] Cockpitdecks web: {web_line}")
        self._append(f"[preflight] Loaded session: {cockpitdecks_session_status_line(base_url=cockpit_web_base(st))}")
        self._append("[preflight] complete.")
        self.refresh_info_panel()

    def check_updates(self) -> None:
        self._append("[update] In-app updates are not implemented yet.")
        self._append("[update] Install a newer Cockpitdecks Desktop release when available.")

    def start_cockpitdecks(self) -> None:
        launcher = self._resolve_launcher_binary()
        if not launcher.exists():
            self._append(f"[launch] launcher not found: {launcher}")
            self.refresh_info_panel()
            return
        wport = self._web_listen_port()
        listener = self._cockpit_web_port_listener()
        if listener is not None:
            self._append(
                f"[launch] blocked: TCP :{wport} already in use by pid={listener[0]} ({listener[1]}). "
                "Stop that process or change COCKPIT_WEB_PORT in Settings, then try Start again."
            )
            self.refresh_info_panel()
            return
        if self._launcher_is_running():
            self._append(f"[launch] already running (pid={self._launcher_process.pid})")
            self.refresh_info_panel()
            return
        child_env = os.environ.copy()
        child_env.update(launch_env_overlay())
        self._launcher_process = subprocess.Popen(
            [str(launcher)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=child_env,
        )
        self._append(f"[launch] started cockpitdecks (pid={self._launcher_process.pid})")
        self._start_launcher_log_stream()
        self.refresh_info_panel()

    def stop_cockpitdecks(self) -> None:
        if not self._launcher_is_running():
            self._append("[launch] no running cockpitdecks process")
            self.refresh_info_panel()
            return
        assert self._launcher_process is not None
        self._append(f"[launch] stopping cockpitdecks (pid={self._launcher_process.pid})")
        self._launcher_process.terminate()
        try:
            self._launcher_process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self._append("[launch] stop timeout, sending kill")
            self._launcher_process.kill()
            try:
                self._launcher_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self.refresh_info_panel()

    def restart_cockpitdecks(self) -> None:
        self._append("[launch] restart requested")
        if self._launcher_is_running():
            self._append("[launch] restarting cockpitdecks...")
            assert self._launcher_process is not None
            self._launcher_process.terminate()
            try:
                self._launcher_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._append("[launch] restart timeout on stop, sending kill")
                self._launcher_process.kill()
                try:
                    self._launcher_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        else:
            self._append("[launch] no managed process running; restart acts like Start")
        self.start_cockpitdecks()

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
                self.log_line.emit("[launch] tip: open the Status tab and use Refresh status to update the panel")

        self._launcher_log_thread = threading.Thread(target=_reader, name="LauncherLogStream", daemon=True)
        self._launcher_log_thread.start()
