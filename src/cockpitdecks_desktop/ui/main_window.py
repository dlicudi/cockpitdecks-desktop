from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import html as html_mod
import importlib.metadata
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCharFormat, QColor, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
    cockpitdecks_web_status_line,
    fetch_session_info,
    reload_decks as api_reload_decks,
    SessionInfo,
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
    live_poll_done = Signal(str, str, object, str, str, object)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Cockpitdecks Desktop")
        self.resize(980, 680)
        self._thread: QThread | None = None
        self._worker: CommandWorker | None = None
        self._launcher_process = None
        self._launcher_log_thread: threading.Thread | None = None
        self._live_poll_lock = threading.Lock()

        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Status value labels (referenced by refresh_info_panel / _apply_live_poll) ──
        self.info_desktop = QLabel("—")
        self.info_launcher = QLabel("—")
        self.info_xplane = QLabel("…")
        self.info_cockpit_web = QLabel("…")
        self.info_session = QLabel("…")
        self.info_live_poll_at = QLabel("—")
        self.info_last_check = QLabel("—")
        self.info_runtime_metrics = QLabel("—")
        _val_style = "font-size: 13px; border: none; padding: 0;"
        for lab in (
            self.info_desktop, self.info_launcher, self.info_xplane,
            self.info_cockpit_web, self.info_session, self.info_live_poll_at,
            self.info_last_check, self.info_runtime_metrics,
        ):
            lab.setWordWrap(False)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lab.setStyleSheet(_val_style)
            _sp = lab.sizePolicy()
            _sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            lab.setSizePolicy(_sp)

        # ── Helper: status indicator dot ──
        def _dot(color: str = "#94a3b8") -> QLabel:
            d = QLabel()
            d.setFixedSize(8, 8)
            d.setStyleSheet(f"background-color: {color}; border-radius: 4px; border: none;")
            return d

        # ── Helper: card frame ──
        def _card(bg: str = "#ffffff", border: str = "#e2e5eb") -> QFrame:
            f = QFrame()
            f.setStyleSheet(
                f"QFrame {{ background-color: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
            )
            return f

        # ── Helper: section heading inside a card ──
        def _section_heading(text: str) -> QLabel:
            h = QLabel(text)
            h.setStyleSheet(
                "font-size: 11px; font-weight: 700; color: #6b7280; letter-spacing: 0.05em; "
                "text-transform: uppercase; border: none; padding: 0; margin: 0;"
            )
            return h

        # ════════════════════════════════════════
        #  HEADER BAR
        # ════════════════════════════════════════
        header = QFrame()
        header.setStyleSheet(
            "QFrame { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #1e293b, stop:1 #334155); border: none; }"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 14, 20, 14)
        header_layout.setSpacing(10)
        title = QLabel("Cockpitdecks Desktop")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f1f5f9; border: none;")
        self._header_version = QLabel("")
        self._header_version.setStyleSheet("font-size: 12px; color: #94a3b8; border: none;")
        header_layout.addWidget(title)
        header_layout.addWidget(self._header_version)
        header_layout.addStretch(1)
        self._header_poll_time = QLabel("")
        self._header_poll_time.setStyleSheet("font-size: 11px; color: #64748b; border: none;")
        header_layout.addWidget(self._header_poll_time)

        # ════════════════════════════════════════
        #  ACTION BAR (below header, shared across all tabs)
        # ════════════════════════════════════════
        action_bar = QFrame()
        action_bar.setObjectName("actionBar")
        action_bar.setStyleSheet(
            "QFrame#actionBar { background-color: #f8fafc; border: none; "
            "border-bottom: 1px solid #e2e5eb; }"
        )
        ab_layout = QHBoxLayout(action_bar)
        ab_layout.setContentsMargins(20, 10, 20, 10)
        ab_layout.setSpacing(8)

        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("primaryButton")
        self.btn_restart = QPushButton("Restart")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("stopButton")
        self.btn_reload = QPushButton("Reload")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_check = QPushButton("Preflight")
        self.btn_update = QPushButton("Updates")
        for b in (self.btn_start, self.btn_restart, self.btn_stop,
                  self.btn_reload, self.btn_refresh, self.btn_check, self.btn_update):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        ab_layout.addWidget(self.btn_start)
        ab_layout.addWidget(self.btn_restart)
        ab_layout.addWidget(self.btn_stop)
        ab_layout.addWidget(self.btn_reload)
        ab_sep = QFrame()
        ab_sep.setFrameShape(QFrame.Shape.VLine)
        ab_sep.setStyleSheet("color: #cbd5e1; max-width: 1px; border: none;")
        ab_layout.addWidget(ab_sep)
        ab_layout.addWidget(self.btn_refresh)
        ab_layout.addWidget(self.btn_check)
        ab_layout.addWidget(self.btn_update)
        ab_layout.addStretch(1)

        # ════════════════════════════════════════
        #  LAST ACTION FEEDBACK
        # ════════════════════════════════════════
        self.status_feedback = QLabel("Ready")
        self.status_feedback.setWordWrap(False)
        self.status_feedback.setTextFormat(Qt.PlainText)
        self.status_feedback.setStyleSheet(
            "background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; "
            "padding: 6px 12px; color: #166534; font-size: 12px;"
        )
        self.status_feedback.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sp = self.status_feedback.sizePolicy()
        sp.setHorizontalPolicy(sp.Policy.Ignored)
        self.status_feedback.setSizePolicy(sp)

        # ════════════════════════════════════════
        #  CONNECTIVITY CARD (left)
        # ════════════════════════════════════════
        conn_card = _card()
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(16, 14, 16, 14)
        conn_layout.setSpacing(0)
        conn_layout.addWidget(_section_heading("Connectivity"))
        conn_layout.addSpacing(10)

        self._dot_launcher = _dot()
        self._dot_xplane = _dot()
        self._dot_cockpit_web = _dot()

        _key_qss = "font-size: 11px; font-weight: 600; color: #6b7280; border: none; padding: 0;"

        def _status_item(dot: QLabel, key: str, value: QLabel) -> QWidget:
            """Dot + key on one line, value below — compact and never wraps the key."""
            item = QWidget()
            vl = QVBoxLayout(item)
            vl.setContentsMargins(0, 6, 0, 6)
            vl.setSpacing(2)
            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(6)
            top.addWidget(dot)
            kl = QLabel(key)
            kl.setStyleSheet(_key_qss)
            top.addWidget(kl)
            top.addStretch(1)
            vl.addLayout(top)
            vl.addWidget(value)
            return item

        conn_layout.addWidget(_status_item(self._dot_launcher, "Launcher", self.info_launcher))
        conn_layout.addWidget(_status_item(self._dot_xplane, "X-Plane API", self.info_xplane))
        conn_layout.addWidget(_status_item(self._dot_cockpit_web, "Cockpitdecks Web", self.info_cockpit_web))

        # Session sub-section — split into individual fields
        sess_sep = QFrame()
        sess_sep.setFrameShape(QFrame.Shape.HLine)
        sess_sep.setStyleSheet("color: #e5e7eb; max-height: 1px; border: none;")
        conn_layout.addSpacing(4)
        conn_layout.addWidget(sess_sep)
        conn_layout.addSpacing(6)

        conn_layout.addWidget(_section_heading("Session"))
        conn_layout.addSpacing(6)

        self.info_sess_aircraft = QLabel("—")
        self.info_sess_decks = QLabel("—")
        self.info_sess_config = QLabel("—")
        self.info_sess_version = QLabel("—")
        for sl in (self.info_sess_aircraft, self.info_sess_decks, self.info_sess_config, self.info_sess_version):
            sl.setWordWrap(False)
            sl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            sl.setStyleSheet(_val_style)
            _sp = sl.sizePolicy()
            _sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            sl.setSizePolicy(_sp)

        def _kv_row(key: str, value: QLabel) -> QWidget:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(8)
            kl = QLabel(key)
            kl.setStyleSheet(_key_qss)
            kl.setFixedWidth(70)
            kl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(kl)
            rl.addWidget(value, 1)
            return row

        conn_layout.addWidget(_kv_row("Aircraft", self.info_sess_aircraft))
        conn_layout.addWidget(_kv_row("Decks", self.info_sess_decks))
        conn_layout.addWidget(_kv_row("Config", self.info_sess_config))
        conn_layout.addWidget(_kv_row("Version", self.info_sess_version))

        conn_layout.addStretch(1)

        # Keep info_session for backward compat (hidden)
        self.info_session.setVisible(False)

        # ════════════════════════════════════════
        #  METRICS CARD (right)
        # ════════════════════════════════════════
        self.metrics_card = _card()
        metrics_layout = QVBoxLayout(self.metrics_card)
        metrics_layout.setContentsMargins(16, 14, 16, 14)
        metrics_layout.setSpacing(8)
        metrics_layout.addWidget(_section_heading("Runtime Metrics"))

        _bar_height = "QProgressBar { max-height: 8px; border-radius: 4px; background: #e5e7eb; border: none; }"

        self.metric_cpu_label = QLabel("CPU —")
        self.metric_cpu_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #374151; border: none;")
        self.metric_cpu_bar = QProgressBar()
        self.metric_cpu_bar.setRange(0, 100)
        self.metric_cpu_bar.setValue(0)
        self.metric_cpu_bar.setTextVisible(False)
        self.metric_cpu_bar.setStyleSheet(_bar_height)

        self.metric_mem_label = QLabel("Memory —")
        self.metric_mem_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #374151; border: none;")
        self.metric_mem_bar = QProgressBar()
        self.metric_mem_bar.setRange(0, 100)
        self.metric_mem_bar.setValue(0)
        self.metric_mem_bar.setTextVisible(False)
        self.metric_mem_bar.setStyleSheet(_bar_height)

        metrics_layout.addWidget(self.metric_cpu_label)
        metrics_layout.addWidget(self.metric_cpu_bar)
        metrics_layout.addSpacing(2)
        metrics_layout.addWidget(self.metric_mem_label)
        metrics_layout.addWidget(self.metric_mem_bar)

        # Metric counters in a 2x2 grid
        metrics_sep = QFrame()
        metrics_sep.setFrameShape(QFrame.Shape.HLine)
        metrics_sep.setStyleSheet("color: #e5e7eb; max-height: 1px; border: none;")
        metrics_layout.addSpacing(4)
        metrics_layout.addWidget(metrics_sep)
        metrics_layout.addSpacing(4)

        self.metric_threads = QLabel("—")
        self.metric_variables = QLabel("—")
        self.metric_datarefs = QLabel("—")
        self.metric_dataref_rate = QLabel("—")
        self.metric_queue_depth = QLabel("—")
        self.metric_dirty_rendered = QLabel("—")
        self.metric_uptime = QLabel("—")
        self._prev_dataref_values_processed: int | None = None
        self._prev_dataref_poll_ts: float | None = None
        self._prev_dirty_rendered: int | None = None
        self._prev_dirty_poll_ts: float | None = None
        _counter_val_qss = "font-size: 20px; font-weight: 700; color: #1e293b; border: none;"
        _counter_lbl_qss = "font-size: 11px; color: #6b7280; border: none;"
        for cv in (self.metric_threads, self.metric_variables, self.metric_datarefs,
                   self.metric_dataref_rate, self.metric_queue_depth, self.metric_dirty_rendered, self.metric_uptime):
            cv.setStyleSheet(_counter_val_qss)
            cv.setAlignment(Qt.AlignmentFlag.AlignCenter)

        counters_grid = QGridLayout()
        counters_grid.setSpacing(6)
        _counters = [
            (self.metric_threads, "Threads"),
            (self.metric_variables, "Variables"),
            (self.metric_datarefs, "Datarefs"),
            (self.metric_dataref_rate, "Dataref/s"),
            (self.metric_queue_depth, "Queue"),
            (self.metric_dirty_rendered, "Render/s"),
            (self.metric_uptime, "Uptime"),
        ]
        _cols = 4
        for idx, (val_label, caption) in enumerate(_counters):
            row, col = divmod(idx, _cols)
            cap = QLabel(caption)
            cap.setStyleSheet(_counter_lbl_qss)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            counters_grid.addWidget(val_label, row * 2, col)
            counters_grid.addWidget(cap, row * 2 + 1, col)
        metrics_layout.addLayout(counters_grid)
        metrics_layout.addStretch(1)

        # Keep self.metric_summary for compatibility with _apply_metrics_visuals
        self.metric_summary = QLabel("")
        self.metric_summary.setVisible(False)

        # Also keep info_runtime_metrics for compatibility
        self.info_runtime_metrics.setVisible(False)

        # ════════════════════════════════════════
        #  DIAGNOSTICS ROW
        # ════════════════════════════════════════
        diag_card = _card(bg="#fafafa", border="#e5e7eb")
        diag_layout = QHBoxLayout(diag_card)
        diag_layout.setContentsMargins(16, 10, 16, 10)
        diag_layout.setSpacing(10)
        diag_lbl = QLabel("Last preflight")
        diag_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #6b7280; border: none;")
        diag_layout.addWidget(diag_lbl)
        diag_layout.addWidget(self.info_last_check, 1)

        # ════════════════════════════════════════
        #  ASSEMBLE STATUS TAB
        # ════════════════════════════════════════
        status_inner = QWidget()
        si = QVBoxLayout(status_inner)
        si.setContentsMargins(20, 16, 20, 20)
        si.setSpacing(12)
        si.addWidget(self.status_feedback)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        cards_row.addWidget(conn_card, 3)
        cards_row.addWidget(self.metrics_card, 2)
        si.addLayout(cards_row, 1)

        si.addWidget(diag_card)

        status_scroll = QScrollArea()
        status_scroll.setWidgetResizable(True)
        status_scroll.setFrameShape(QFrame.Shape.NoFrame)
        status_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        status_scroll.setWidget(status_inner)

        tab_status = QWidget()
        tab_status_layout = QVBoxLayout(tab_status)
        tab_status_layout.setContentsMargins(0, 0, 0, 0)
        tab_status_layout.setSpacing(0)
        tab_status_layout.addWidget(status_scroll, 1)

        # ════════════════════════════════════════
        #  CONFIG TAB
        # ════════════════════════════════════════
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

        # ════════════════════════════════════════
        #  LOGS TAB
        # ════════════════════════════════════════
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Preflight, launch, and Cockpitdecks output will appear here…")
        self.log.setStyleSheet(
            "QTextEdit { font-family: Menlo, Monaco, monospace; font-size: 12px;"
            " background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; border-radius: 4px; padding: 4px; }"
            " QTextEdit::selection { background-color: #264f78; color: #ffffff; }"
        )

        tab_logs = QWidget()
        tab_logs_layout = QVBoxLayout(tab_logs)
        tab_logs_layout.setContentsMargins(8, 8, 8, 8)
        tab_logs_layout.setSpacing(8)
        logs_bar = QHBoxLayout()
        self.btn_clear_logs = QPushButton("Clear log")
        self.btn_copy_logs = QPushButton("Copy selected")
        self.btn_copy_logs.setToolTip("Copy selected text to clipboard (or all if nothing selected)")
        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems(["All", "DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level_combo.setCurrentText("WARNING")
        self._log_level_combo.setToolTip("Minimum log level to display (desktop messages always shown)")
        logs_bar.addWidget(self.btn_clear_logs)
        logs_bar.addWidget(self.btn_copy_logs)
        logs_bar.addWidget(QLabel("Log level:"))
        logs_bar.addWidget(self._log_level_combo)
        logs_bar.addStretch(1)
        tab_logs_layout.addLayout(logs_bar)
        tab_logs_layout.addWidget(self.log, 1)

        # Search bar (hidden by default, toggled with Cmd+F)
        self._log_search_bar = QFrame()
        self._log_search_bar.setStyleSheet(
            "QFrame { background: #2d2d2d; border: 1px solid #444; border-radius: 4px; }"
        )
        self._log_search_bar.setVisible(False)
        search_layout = QHBoxLayout(self._log_search_bar)
        search_layout.setContentsMargins(8, 4, 8, 4)
        search_layout.setSpacing(6)
        self._log_search_input = QLineEdit()
        self._log_search_input.setPlaceholderText("Search logs…")
        self._log_search_input.setStyleSheet(
            "QLineEdit { background: #1e1e1e; color: #d4d4d4; border: 1px solid #555;"
            " border-radius: 3px; padding: 3px 6px; font-family: Menlo, Monaco, monospace; font-size: 12px; }"
        )
        self._log_search_count = QLabel("")
        self._log_search_count.setStyleSheet("color: #888; font-size: 11px; min-width: 70px;")
        btn_prev = QPushButton("Prev")
        btn_next = QPushButton("Next")
        btn_close_search = QPushButton("Close")
        for b in (btn_prev, btn_next, btn_close_search):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(24)
            b.setStyleSheet(
                "QPushButton { background: #3c3c3c; color: #d4d4d4; border: 1px solid #555;"
                " border-radius: 3px; padding: 2px 8px; font-size: 11px; }"
                " QPushButton:hover { background: #4c4c4c; }"
            )
        self._btn_filter = QPushButton("Filter")
        self._btn_filter.setCheckable(True)
        self._btn_filter.setToolTip("Toggle grep mode: show only matching lines")
        self._btn_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_filter.setFixedHeight(24)
        self._btn_filter.setStyleSheet(
            "QPushButton { background: #3c3c3c; color: #d4d4d4; border: 1px solid #555;"
            " border-radius: 3px; padding: 2px 8px; font-size: 11px; }"
            " QPushButton:hover { background: #4c4c4c; }"
            " QPushButton:checked { background: #1a5276; color: #60a5fa; border-color: #60a5fa; }"
        )
        search_layout.addWidget(self._log_search_input, 1)
        search_layout.addWidget(self._log_search_count)
        search_layout.addWidget(self._btn_filter)
        search_layout.addWidget(btn_prev)
        search_layout.addWidget(btn_next)
        search_layout.addWidget(btn_close_search)
        tab_logs_layout.addWidget(self._log_search_bar)

        self._log_search_input.textChanged.connect(self._log_search_apply)
        self._log_search_input.returnPressed.connect(self._log_search_next)
        self._btn_filter.toggled.connect(lambda _: self._log_search_apply(self._log_search_input.text()))
        btn_next.clicked.connect(self._log_search_next)
        btn_prev.clicked.connect(self._log_search_prev)
        btn_close_search.clicked.connect(self._log_search_close)

        # ════════════════════════════════════════
        #  TAB WIDGET + ROOT ASSEMBLY
        # ════════════════════════════════════════
        self.tabs = QTabWidget()
        self.tabs.addTab(tab_status, "Status")
        self.tabs.addTab(tab_config, "Config")
        self.tabs.addTab(tab_logs, "Logs")

        root.addWidget(header)
        root.addWidget(action_bar)
        root.addWidget(self.tabs, 1)

        status = QStatusBar(self)
        status.showMessage("Ready")
        self.setStatusBar(status)

        # ── Connections ──
        self.btn_refresh.clicked.connect(self.refresh_info_panel)
        self.btn_check.clicked.connect(self.run_preflight)
        self.btn_update.clicked.connect(self.check_updates)
        self.btn_start.clicked.connect(self.start_cockpitdecks)
        self.btn_restart.clicked.connect(self.restart_cockpitdecks)
        self.btn_stop.clicked.connect(self.stop_cockpitdecks)
        self.btn_reload.clicked.connect(self.reload_decks)
        self.btn_clear_logs.clicked.connect(self.log.clear)
        self.btn_copy_logs.clicked.connect(self._copy_log_selection)
        self.log_line.connect(self._append)
        self.live_poll_done.connect(self._apply_live_poll)
        self.settings_form.settings_saved.connect(self._on_settings_saved)

        self.log.setAlignment(Qt.AlignTop)
        self.setStyleSheet(MAIN_WINDOW_QSS)
        self.btn_clear_logs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_logs.setCursor(Qt.CursorShape.PointingHandCursor)

        # Cmd+F / Ctrl+F to toggle log search, Escape to close
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self._log_search_toggle)
        esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self._log_search_bar)
        esc_shortcut.activated.connect(self._log_search_close)
        self.refresh_info_panel()
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._schedule_live_poll)
        self._live_timer.start(4000)

    # Color mapping for desktop [tag] prefixes and Python logging levels.
    _LOG_COLORS: dict[str, str] = {
        # Desktop tags
        "error": "#ef4444",
        "launch": "#3b82f6",
        "preflight": "#8b5cf6",
        "reload": "#06b6d4",
        "update": "#a3a3a3",
        "ok": "#22c55e",
        "desktop": "#f59e0b",
        # Python logging levels (from launcher stdout)
        "CRITICAL": "#ef4444",
        "ERROR": "#ef4444",
        "WARNING": "#f59e0b",
        "INFO": "#60a5fa",
        "DEBUG": "#6b7280",
        "DEPRECATION": "#a78bfa",
        "SPAM": "#6b7280",
    }
    _LOG_TAG_RE = re.compile(r"^\[([a-z]+)\]")
    _LOG_LEVEL_RE = re.compile(r"^\[\S+\]\s+(CRITICAL|ERROR|WARNING|INFO|DEBUG|DEPRECATION|SPAM)\b")

    # Lines matching any of these patterns are always suppressed (noise).
    _LOG_NOISE_RE = re.compile(
        r'"(GET|POST|PUT|DELETE|HEAD|OPTIONS|PATCH) /\S* HTTP/\d\.\d"'  # Flask request logs
        r"|_internal\.py:_log:\d+"                                       # Werkzeug internal logger tag
    )

    # Numeric priority for log-level filtering (higher = more severe).
    _LOG_LEVEL_PRIORITY: dict[str, int] = {
        "SPAM": 0,
        "DEBUG": 10,
        "DEPRECATION": 12,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
        "CRITICAL": 50,
    }

    def _append(self, text: str) -> None:
        if not text:
            return

        # Always suppress noise lines.
        if self._LOG_NOISE_RE.search(text):
            return

        # Desktop [tag] messages (e.g. [launch], [preflight]) are always shown.
        is_desktop_tag = self._LOG_TAG_RE.match(text) is not None

        # Apply log-level filter to launcher log lines (not desktop tags).
        if not is_desktop_tag:
            m_level = self._LOG_LEVEL_RE.match(text)
            if m_level:
                line_level = m_level.group(1)
                min_level = self._log_level_combo.currentText()
                if min_level != "All":
                    line_pri = self._LOG_LEVEL_PRIORITY.get(line_level, 20)
                    min_pri = self._LOG_LEVEL_PRIORITY.get(min_level, 0)
                    if line_pri < min_pri:
                        return

        escaped = html_mod.escape(text)
        low = text.lower()

        # Priority 1: error keywords anywhere
        if any(k in low for k in ("error", "fail", "missing", "blocked", "kill")):
            color = self._LOG_COLORS["error"]
        else:
            color = None
            # Priority 2: desktop [tag] prefix
            m = self._LOG_TAG_RE.match(text)
            if m and m.group(1) in self._LOG_COLORS:
                color = self._LOG_COLORS[m.group(1)]
            # Priority 3: Python logging level from launcher output
            if color is None:
                m2 = self._LOG_LEVEL_RE.match(text)
                if m2:
                    color = self._LOG_COLORS[m2.group(1)]
            if color is None:
                color = "#d4d4d4"

        self.log.append(f'<span style="color:{color}">{escaped}</span>')
        self._set_status_feedback(text)

    def _copy_log_selection(self) -> None:
        cursor = self.log.textCursor()
        text = cursor.selectedText() if cursor.hasSelection() else self.log.toPlainText()
        if text:
            # QTextEdit uses U+2029 (paragraph separator) instead of \n in selections
            text = text.replace("\u2029", "\n")
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
                n = len(text.splitlines())
                self._set_status_feedback(f"Copied {n} line(s) to clipboard")

    # ── Log search ──

    def _log_search_toggle(self) -> None:
        if self._log_search_bar.isVisible():
            self._log_search_close()
        else:
            self._log_search_bar.setVisible(True)
            self._log_search_input.setFocus()
            self._log_search_input.selectAll()

    def _log_search_close(self) -> None:
        self._log_search_bar.setVisible(False)
        self._log_search_input.clear()
        self._log_search_count.setText("")
        self._btn_filter.setChecked(False)
        self.log.setExtraSelections([])
        self._log_show_all_blocks()

    def _log_show_all_blocks(self) -> None:
        """Make all text blocks visible again."""
        block = self.log.document().begin()
        while block.isValid():
            block.setVisible(True)
            block = block.next()
        self.log.viewport().update()
        self.log.document().markContentsDirty(0, self.log.document().characterCount())

    def _log_search_apply(self, query: str) -> None:
        """Dispatch to highlight or filter mode based on toggle state."""
        if self._btn_filter.isChecked():
            self._log_search_filter(query)
        else:
            self._log_search_highlight(query)

    def _log_search_highlight(self, query: str) -> None:
        """Highlight all matches and update the count label."""
        self._log_show_all_blocks()
        self.log.setExtraSelections([])
        if not query:
            self._log_search_count.setText("")
            return

        selections = []
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#5a4a00"))
        fmt.setForeground(QColor("#ffdd57"))

        doc = self.log.document()
        cursor = QTextCursor(doc)
        while True:
            cursor = doc.find(query, cursor)
            if cursor.isNull():
                break
            sel = QTextEdit.ExtraSelection()
            sel.cursor = QTextCursor(cursor)
            sel.format = fmt
            selections.append(sel)

        self.log.setExtraSelections(selections)
        n = len(selections)
        self._log_search_count.setText(f"{n} match{'es' if n != 1 else ''}" if n else "no matches")

    def _log_search_filter(self, query: str) -> None:
        """Grep mode: hide lines that don't match, show only those that do."""
        self.log.setExtraSelections([])
        if not query:
            self._log_show_all_blocks()
            self._log_search_count.setText("")
            return

        query_lower = query.lower()
        total = 0
        matched = 0
        block = self.log.document().begin()
        while block.isValid():
            total += 1
            text = block.text()
            if query_lower in text.lower():
                block.setVisible(True)
                matched += 1
            else:
                block.setVisible(False)
            block = block.next()

        self.log.viewport().update()
        self.log.document().markContentsDirty(0, self.log.document().characterCount())
        self._log_search_count.setText(f"{matched}/{total} lines")

    def _log_search_next(self) -> None:
        query = self._log_search_input.text()
        if not query:
            return
        found = self.log.find(query)
        if not found:
            cursor = self.log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.log.setTextCursor(cursor)
            self.log.find(query)

    def _log_search_prev(self) -> None:
        query = self._log_search_input.text()
        if not query:
            return
        from PySide6.QtGui import QTextDocument
        found = self.log.find(query, QTextDocument.FindFlag.FindBackward)
        if not found:
            cursor = self.log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.log.setTextCursor(cursor)
            self.log.find(query, QTextDocument.FindFlag.FindBackward)

    def _set_status_feedback(self, text: str) -> None:
        msg = (text or "").strip()
        if not msg:
            return
        # Keep status page concise; clip very long lines from verbose logs.
        if len(msg) > 220:
            msg = msg[:217] + "..."
        self.status_feedback.setText(msg)
        low = msg.lower()
        if any(k in low for k in ("error", "fail", "missing", "blocked", "kill")):
            self.status_feedback.setStyleSheet(
                "background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; "
                "padding: 6px 12px; color: #991b1b; font-size: 12px;"
            )
        elif any(k in low for k in ("started", "complete", "ok", "saved", "ready")):
            self.status_feedback.setStyleSheet(
                "background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; "
                "padding: 6px 12px; color: #166534; font-size: 12px;"
            )
        else:
            self.status_feedback.setStyleSheet(
                "background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; "
                "padding: 6px 12px; color: #334155; font-size: 12px;"
            )

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
        self.btn_stop.setEnabled(not cmd_busy and (running or port_in_use))
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

        cockpit_reachable = running or port_in_use
        can_reload = not cmd_busy and cockpit_reachable
        self.btn_reload.setEnabled(can_reload)
        if can_reload:
            self.btn_reload.setToolTip("Reload deck configurations.")
        elif cmd_busy:
            self.btn_reload.setToolTip("Disabled while another task is running.")
        else:
            self.btn_reload.setToolTip("Cockpitdecks is not running.")

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

    def _set_dot(self, dot: QLabel, state: str) -> None:
        """Set indicator dot color: 'ok' green, 'warn' amber, 'error' red, else gray."""
        colors = {"ok": "#22c55e", "warn": "#f59e0b", "error": "#ef4444"}
        c = colors.get(state, "#94a3b8")
        dot.setStyleSheet(f"background-color: {c}; border-radius: 5px; border: none;")

    def _apply_metrics_visuals(self, metrics: dict | None) -> None:
        _bar_base = "QProgressBar {{ max-height: 8px; border-radius: 4px; background: #e5e7eb; border: none; }} QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}"
        if not isinstance(metrics, dict):
            self.metric_cpu_label.setText("CPU —")
            self.metric_mem_label.setText("Memory —")
            self.metric_cpu_bar.setValue(0)
            self.metric_mem_bar.setValue(0)
            self.metric_threads.setText("—")
            self.metric_variables.setText("—")
            self.metric_datarefs.setText("—")
            self.metric_dataref_rate.setText("—")
            self.metric_queue_depth.setText("—")
            self.metric_dirty_rendered.setText("—")
            self.metric_uptime.setText("—")
            self._prev_dataref_values_processed = None
            self._prev_dataref_poll_ts = None
            self._prev_dirty_rendered = None
            self._prev_dirty_poll_ts = None
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
            self.metric_cpu_bar.setStyleSheet(_bar_base.format(color="#ef4444"))
        elif cpu_val >= 60:
            self.metric_cpu_bar.setStyleSheet(_bar_base.format(color="#f59e0b"))
        else:
            self.metric_cpu_bar.setStyleSheet(_bar_base.format(color="#22c55e"))

        if isinstance(rss_mb, (int, float)):
            mem_pct = int(max(0.0, min(100.0, (float(rss_mb) / 4096.0) * 100.0)))
            self.metric_mem_bar.setValue(mem_pct)
            self.metric_mem_label.setText(f"Memory {float(rss_mb):.1f} MB")
        else:
            self.metric_mem_bar.setValue(0)
            self.metric_mem_label.setText("Memory —")
        self.metric_mem_bar.setStyleSheet(_bar_base.format(color="#3b82f6"))

        self.metric_threads.setText(str(threads) if isinstance(threads, int) else "—")
        self.metric_variables.setText(str(vars_n) if isinstance(vars_n, int) else "—")
        self.metric_datarefs.setText(str(drefs) if isinstance(drefs, int) else "—")

        # Dataref/s rate from traffic counters
        traffic = metrics.get("dataref_traffic") if isinstance(metrics.get("dataref_traffic"), dict) else {}
        cur_vals = traffic.get("dataref_values_processed")
        now_ts = time.time()
        if isinstance(cur_vals, (int, float)) and self._prev_dataref_values_processed is not None and self._prev_dataref_poll_ts is not None:
            dt = now_ts - self._prev_dataref_poll_ts
            if dt > 0.5:
                rate = (cur_vals - self._prev_dataref_values_processed) / dt
                self.metric_dataref_rate.setText(f"{rate:.0f}")
            # else keep previous display
        else:
            self.metric_dataref_rate.setText("—")
        if isinstance(cur_vals, (int, float)):
            self._prev_dataref_values_processed = int(cur_vals)
            self._prev_dataref_poll_ts = now_ts

        # Event queue depth and Render/s rate
        queue_depth = c.get("event_queue_depth")
        self.metric_queue_depth.setText(str(queue_depth) if isinstance(queue_depth, int) else "—")

        cur_rendered = c.get("dirty_rendered")
        if isinstance(cur_rendered, (int, float)) and self._prev_dirty_rendered is not None and self._prev_dirty_poll_ts is not None:
            dt = now_ts - self._prev_dirty_poll_ts
            if dt > 0.5:
                render_rate = (cur_rendered - self._prev_dirty_rendered) / dt
                self.metric_dirty_rendered.setText(f"{render_rate:.0f}")
        else:
            self.metric_dirty_rendered.setText("—")
        if isinstance(cur_rendered, (int, float)):
            self._prev_dirty_rendered = int(cur_rendered)
            self._prev_dirty_poll_ts = now_ts

        if isinstance(uptime_s, (int, float)):
            uptime_i = int(uptime_s)
            h, rem = divmod(uptime_i, 3600)
            m, sec = divmod(rem, 60)
            self.metric_uptime.setText(f"{h:d}:{m:02d}:{sec:02d}")
        else:
            self.metric_uptime.setText("—")

    def refresh_info_panel(self) -> None:
        ver = self._desktop_app_version()
        self.info_desktop.setText(f"v{ver}")
        self._header_version.setText(f"v{ver}")

        launcher = self._resolve_launcher_binary()
        wport = self._web_listen_port()
        tip_lines: list[str] = [f"Full path:\n{launcher}"]
        path_disp = _shorten_filesystem_path(launcher)
        if launcher.exists():
            running = self._launcher_is_running()
            launcher_status = "Running" if running else "Ready"
            listener = self._cockpit_web_port_listener()
            if listener is not None:
                tip_lines.append(f"Listener on port {wport}: pid {listener[0]} ({listener[1]})")
                self.info_launcher.setText(
                    f"{launcher_status}  ·  port {wport} (pid {listener[0]})"
                )
                self._set_dot(self._dot_launcher, "ok")
            else:
                self.info_launcher.setText(f"{launcher_status}  ·  {path_disp}")
                self._set_dot(self._dot_launcher, "ok" if running else "warn")
        else:
            self.info_launcher.setText(f"Missing  ·  {path_disp}")
            self._set_dot(self._dot_launcher, "error")
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
                session_info = fetch_session_info(base_url=web_base)
                metrics_line = cockpitdecks_metrics_status_line(base_url=web_base)
                metrics_obj, _ = cockpitdecks_metrics_json(base_url=web_base)
                ts = datetime.now().strftime("%H:%M:%S")
                self.live_poll_done.emit(xp_line, web_line, session_info, metrics_line, ts, metrics_obj)
            finally:
                self._live_poll_lock.release()

        threading.Thread(target=work, name="LiveApiPoll", daemon=True).start()

    def _apply_live_poll(
        self,
        xplane_line: str,
        cockpit_web_line: str,
        session_info: SessionInfo,
        metrics_line: str,
        polled_at: str,
        metrics_obj: dict | None,
    ) -> None:
        self.info_xplane.setText(xplane_line)
        self.info_cockpit_web.setText(cockpit_web_line)
        self.info_session.setText(session_info.one_line())

        # Populate split session fields
        if session_info.ok:
            self.info_sess_aircraft.setText(session_info.aircraft)
            self.info_sess_decks.setText(session_info.decks)
            self.info_sess_config.setText(session_info.config_path)
            self.info_sess_version.setText(f"v{session_info.version}" if session_info.version else "—")
        else:
            err = f"— ({session_info.error})"
            self.info_sess_aircraft.setText(err)
            self.info_sess_decks.setText("—")
            self.info_sess_config.setText("—")
            self.info_sess_version.setText("—")

        self.info_runtime_metrics.setText(metrics_line)
        self._apply_metrics_visuals(metrics_obj)
        self.info_live_poll_at.setText(polled_at)
        self._header_poll_time.setText(f"Last poll {polled_at}")
        self._refresh_status_value_styles()

        # Update connectivity dots
        xp_lower = xplane_line.lower()
        self._set_dot(self._dot_xplane, "error" if "unreachable" in xp_lower else "ok")
        web_lower = cockpit_web_line.lower()
        self._set_dot(self._dot_cockpit_web, "error" if "unreachable" in web_lower else "ok")

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
        self._append(f"[preflight] Loaded session: {fetch_session_info(base_url=cockpit_web_base(st)).one_line()}")
        self._append("[preflight] complete.")
        self.refresh_info_panel()

    def reload_decks(self) -> None:
        self._append("[reload] requesting deck config reload...")
        st = load_desktop_settings()
        ok, msg = api_reload_decks(base_url=cockpit_web_base(st))
        tag = "reload" if ok else "error"
        self._append(f"[{tag}] {msg}")

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

    def _kill_port_listener(self) -> bool:
        """Terminate whatever process is listening on the cockpitdecks web port. Returns True if a signal was sent."""
        listener = self._cockpit_web_port_listener()
        if listener is None:
            return False
        pid, name = listener
        self._append(f"[launch] terminating orphan on port {self._web_listen_port()} (pid={pid}, {name})")
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            self._append(f"[launch] failed to terminate pid {pid}: {exc}")
            return False
        return True

    def stop_cockpitdecks(self) -> None:
        if self._launcher_is_running():
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
        elif self._kill_port_listener():
            pass  # orphan handled
        else:
            self._append("[launch] no running cockpitdecks process")
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
        elif self._kill_port_listener():
            import time

            time.sleep(1)  # brief pause for port to free up
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

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._launcher_is_running():
            self._launcher_process.terminate()
            try:
                self._launcher_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._launcher_process.kill()
                try:
                    self._launcher_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        super().closeEvent(event)
