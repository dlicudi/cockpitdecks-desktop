from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCharFormat, QColor, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_desktop.services.desktop_settings import (
    cockpit_web_base,
    launcher_binary_path,
    load as load_desktop_settings,
    launch_env_overlay,
    managed_decks_dir,
    save as save_desktop_settings,
    settings_path,
    xplane_rest_base,
)
from cockpitdecks_desktop.services.live_apis import (
    cockpitdecks_metrics_json,
    cockpitdecks_metrics_status_line,
    cockpitdecks_web_status_line,
    fetch_session_info,
    reload_decks as api_reload_decks,
    set_target as api_set_target,
    SessionInfo,
    xplane_capabilities_status_line,
)
from cockpitdecks_desktop.services.process_runner import stream_shell_command
from cockpitdecks_desktop.ui.app_style import MAIN_WINDOW_QSS
from cockpitdecks_desktop.ui.deck_packs_tab import DeckPacksTab
from cockpitdecks_desktop.ui.diagnostics_tab import DiagnosticsTab
from cockpitdecks_desktop.ui.topology_tab import TopologyTab
from cockpitdecks_desktop.ui.releases_tab import ReleasesTab
from cockpitdecks_desktop.ui.settings_dialog import SettingsFormWidget
from cockpitdecks_desktop.ui.sparkline import SparklineWidget


def _path_key(p: str) -> str:
    try:
        return str(Path(p).expanduser().resolve())
    except OSError:
        return str(Path(p).expanduser())


@dataclass
class CommandStep:
    title: str
    command: str
    cwd: Path


@dataclass
class LaunchTargetInfo:
    aircraft_name: str
    path: str
    root: str
    deck_count: int
    deck_names: list[str]
    config_ok: bool
    config_error: str = ""
    # manifest.yaml fields
    has_manifest: bool = False
    config_name: str = ""        # manifest `name` (short label, e.g. "Cirrus SR22")
    version: str = ""
    icao: str = ""
    manifest_status: str = ""
    description: str = ""
    layout_infos: list[tuple[str, str]] = None  # (layout_id, status) from manifest layouts[]

    def __post_init__(self) -> None:
        if self.layout_infos is None:
            self.layout_infos = []


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
        from cockpitdecks_desktop import __version__
        self.setWindowTitle(f"Cockpitdecks Desktop {__version__}")
        self.resize(980, 680)
        self._thread: QThread | None = None
        self._worker: CommandWorker | None = None
        self._launcher_process = None
        self._launcher_log_thread: threading.Thread | None = None
        self._live_poll_lock = threading.Lock()
        self._launch_targets: list[LaunchTargetInfo] = []
        self._last_launcher_exit_code: int | None = None

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
        self.info_diag_warning = QLabel("—")
        self.diag_health_runtime = QLabel("—")

        _val_style = "font-size: 13px; border: none; padding: 0;"
        for lab in (
            self.info_desktop, self.info_launcher, self.info_xplane,
            self.info_cockpit_web, self.info_session, self.info_live_poll_at,
            self.info_last_check, self.info_runtime_metrics, self.info_diag_warning,
            self.diag_health_runtime,
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
                "font-size: 11px; font-weight: 700; color: #6b7280; "
                "border: none; padding: 0; margin: 0;"
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
        self.btn_reload.setToolTip("Reload cockpitdecks so new config takes effect.")
        self.btn_check = QPushButton("Preflight")
        self.btn_check.setToolTip("Check launcher, ports, X-Plane and Cockpitdecks connectivity.")
        for b in (self.btn_start, self.btn_restart, self.btn_stop,
                  self.btn_reload, self.btn_check):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        ab_layout.addWidget(self.btn_start)
        ab_layout.addWidget(self.btn_restart)
        ab_layout.addWidget(self.btn_stop)
        ab_layout.addWidget(self.btn_reload)
        ab_sep = QFrame()
        ab_sep.setFrameShape(QFrame.Shape.VLine)
        ab_sep.setStyleSheet("color: #cbd5e1; max-width: 1px; border: none;")
        ab_layout.addWidget(ab_sep)
        ab_layout.addWidget(self.btn_check)
        ab_sep2 = QFrame()
        ab_sep2.setFrameShape(QFrame.Shape.VLine)
        ab_sep2.setStyleSheet("color: #cbd5e1; max-width: 1px; border: none;")
        ab_layout.addWidget(ab_sep2)
        self.btn_export = QPushButton("Export Diagnostics…")
        self.btn_export.setToolTip("Save a diagnostics bundle to a JSON file.")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.clicked.connect(self.export_diagnostics_bundle)
        ab_layout.addWidget(self.btn_export)

        ab_layout.addStretch(1)

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

        _lbl_qss = "font-size: 12px; font-weight: 600; color: #374151; border: none;"

        self.metric_cpu_label = QLabel("CPU (process) —")
        self.metric_cpu_label.setStyleSheet(_lbl_qss)
        self.metric_cpu_spark = SparklineWidget(
            max_points=60, fixed_max=100.0, color=QColor("#22c55e"),
        )

        self.metric_mem_label = QLabel("Memory (RSS) —")
        self.metric_mem_label.setStyleSheet(_lbl_qss)
        self.metric_mem_spark = SparklineWidget(
            max_points=60, color=QColor("#3b82f6"),
        )

        metrics_layout.addWidget(self.metric_cpu_label)
        metrics_layout.addWidget(self.metric_cpu_spark)
        metrics_layout.addSpacing(2)
        metrics_layout.addWidget(self.metric_mem_label)
        metrics_layout.addWidget(self.metric_mem_spark)

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
        self.metric_ws_rate = QLabel("—")
        self.metric_marks_per_flush = QLabel("—")
        self.metric_uptime = QLabel("—")
        self._prev_dataref_values_processed: int | None = None
        self._prev_dataref_poll_ts: float | None = None
        self._prev_dirty_rendered: int | None = None
        self._prev_dirty_poll_ts: float | None = None
        self._prev_ws_messages_received: int | None = None
        self._prev_dirty_marks: int | None = None
        self._prev_dirty_flushes: int | None = None
        self._prev_rate_poll_ts: float | None = None
        self._last_session_info: SessionInfo | None = None
        self._last_metrics_obj: dict | None = None

        # Log-analysis state (populated by _parse_log_line)
        self._log_init_start_ts: float | None = None
        self._log_init_end_ts: float | None = None
        self._log_extensions: list[str] = []
        self._log_missing_ext: list[str] = []
        self._log_hardware: dict[str, int] = {}
        self._log_last_usb: str = ""
        _counter_val_qss = "font-size: 20px; font-weight: 700; color: #1e293b; border: none;"
        _counter_lbl_qss = "font-size: 11px; color: #6b7280; border: none;"
        for cv in (self.metric_threads, self.metric_variables, self.metric_datarefs,
                   self.metric_dataref_rate, self.metric_queue_depth, self.metric_dirty_rendered,
                   self.metric_ws_rate, self.metric_marks_per_flush, self.metric_uptime):
            cv.setStyleSheet(_counter_val_qss)
            cv.setAlignment(Qt.AlignmentFlag.AlignCenter)

        counters_grid = QGridLayout()
        counters_grid.setSpacing(6)
        _counters = [
            (self.metric_queue_depth, "Queue"),
            (self.metric_uptime, "Uptime"),
            (self.metric_dataref_rate, "Dataref/s"),
            (self.metric_dirty_rendered, "Render/s"),
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
        self._diag_card = _card(bg="#fafafa", border="#e5e7eb")
        diag_card = self._diag_card
        diag_layout = QHBoxLayout(diag_card)
        diag_layout.setContentsMargins(16, 10, 16, 10)
        diag_layout.setSpacing(10)
        diag_lbl = QLabel("Last preflight")
        diag_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #6b7280; border: none;")
        diag_layout.addWidget(diag_lbl)
        diag_layout.addWidget(self.info_last_check, 1)
        diag_warn_lbl = QLabel("Diagnostics")
        diag_warn_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #6b7280; border: none;")
        diag_layout.addWidget(diag_warn_lbl)
        diag_layout.addWidget(self.info_diag_warning, 2)

        # ════════════════════════════════════════
        #  ASSEMBLE STATUS TAB
        # ════════════════════════════════════════
        status_inner = QWidget()
        si = QVBoxLayout(status_inner)
        si.setContentsMargins(20, 16, 20, 20)
        si.setSpacing(12)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        cards_row.addWidget(conn_card, 3)
        cards_row.addWidget(self.metrics_card, 2)
        si.addLayout(cards_row, 1)

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
        #  DIAGNOSTICS TAB
        # ════════════════════════════════════════
        self.diag_tab = DiagnosticsTab()
        tab_diag = self.diag_tab

        # ════════════════════════════════════════
        #  DECKS TAB
        # ════════════════════════════════════════
        tab_decks = QWidget()
        tab_decks_layout = QVBoxLayout(tab_decks)
        tab_decks_layout.setContentsMargins(12, 12, 12, 12)
        tab_decks_layout.setSpacing(10)

        # ── Segmented toggle: Installed | Available ──
        from PySide6.QtWidgets import QButtonGroup

        seg_container = QWidget()
        seg_container.setFixedHeight(32)
        seg_container.setStyleSheet(
            "QWidget { background: #f1f5f9; border-radius: 8px; }"
        )
        seg_inner = QHBoxLayout(seg_container)
        seg_inner.setContentsMargins(3, 3, 3, 3)
        seg_inner.setSpacing(2)

        self._decks_seg_installed = QPushButton("Installed")
        self._decks_seg_available = QPushButton("Available")
        self._decks_seg_archive = QPushButton("Archive")
        for btn in (self._decks_seg_installed, self._decks_seg_available, self._decks_seg_archive):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            seg_inner.addWidget(btn)

        self._decks_seg_installed.setChecked(True)
        seg_group = QButtonGroup(self)
        seg_group.setExclusive(True)
        seg_group.addButton(self._decks_seg_installed, 0)
        seg_group.addButton(self._decks_seg_available, 1)
        seg_group.addButton(self._decks_seg_archive, 2)

        self._apply_seg_styles(0)

        decks_toggle_row = QHBoxLayout()
        decks_toggle_row.setContentsMargins(0, 0, 0, 0)
        decks_toggle_row.addWidget(seg_container)
        decks_toggle_row.addStretch(1)
        tab_decks_layout.addLayout(decks_toggle_row)

        # ── Stacked content: page 0 = Installed, page 1 = Available ──
        from PySide6.QtWidgets import QStackedWidget

        self._decks_stack = QStackedWidget()

        # Page 0: Installed decks
        installed_page = QWidget()
        installed_layout = QVBoxLayout(installed_page)
        installed_layout.setContentsMargins(0, 0, 0, 0)
        installed_layout.setSpacing(8)

        decks_toolbar = QHBoxLayout()
        decks_toolbar.setSpacing(8)
        self.btn_decks_import = QPushButton("Import Deck…")
        self.btn_decks_select = QPushButton("Use Selected")
        self.btn_decks_reveal = QPushButton("Reveal Folder")
        for b in (
            self.btn_decks_import,
            self.btn_decks_select,
            self.btn_decks_reveal,
        ):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        decks_toolbar.addWidget(self.btn_decks_import)
        decks_toolbar.addWidget(self.btn_decks_select)
        decks_toolbar.addWidget(self.btn_decks_reveal)
        decks_toolbar.addStretch(1)
        installed_layout.addLayout(decks_toolbar)

        self._selected_deck_path: str = ""
        self._deck_grid_container = QWidget()
        self._deck_grid_container.setStyleSheet("background: transparent;")
        self._deck_grid_layout = QGridLayout(self._deck_grid_container)
        self._deck_grid_layout.setContentsMargins(4, 4, 4, 4)
        self._deck_grid_layout.setSpacing(10)
        for _c in range(4):
            self._deck_grid_layout.setColumnStretch(_c, 1)
        self._deck_grid_area = QScrollArea()
        self._deck_grid_area.setWidgetResizable(True)
        self._deck_grid_area.setFrameShape(QFrame.Shape.NoFrame)
        self._deck_grid_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._deck_grid_area.setStyleSheet(
            "QScrollArea { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; }"
        )
        self._deck_grid_area.setWidget(self._deck_grid_container)
        installed_layout.addWidget(self._deck_grid_area, 1)

        self.decks_summary = QLabel("No targets discovered yet.")
        self.decks_summary.setStyleSheet("font-size: 12px; color: #64748b; border: none;")
        installed_layout.addWidget(self.decks_summary)

        self._decks_stack.addWidget(installed_page)  # index 0

        # Page 1: Available packs (latest per pack)
        self.deck_packs_tab = DeckPacksTab(show_all_versions=False)
        self.deck_packs_tab.installed.connect(self._on_pack_installed)
        self.deck_packs_tab.uninstalled.connect(self._on_pack_uninstalled)
        self.deck_packs_tab.log_line.connect(self._append)
        self._decks_stack.addWidget(self.deck_packs_tab)  # index 1

        # Page 2: Archive (all versions)
        self.deck_packs_archive = DeckPacksTab(show_all_versions=True)
        self.deck_packs_archive.installed.connect(self._on_pack_installed)
        self.deck_packs_archive.uninstalled.connect(self._on_pack_uninstalled)
        self.deck_packs_archive.log_line.connect(self._append)
        self._decks_stack.addWidget(self.deck_packs_archive)  # index 2

        tab_decks_layout.addWidget(self._decks_stack, 1)

        seg_group.idClicked.connect(self._on_decks_segment_changed)

        # ════════════════════════════════════════
        #  LOGS TAB
        # ════════════════════════════════════════
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Preflight, launch, and Cockpitdecks output will appear here...")
        self.log.setStyleSheet(
            "QPlainTextEdit { font-family: Menlo, Monaco, monospace; font-size: 12px;"
            " background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; border-radius: 4px; padding: 4px; }"
            " QPlainTextEdit::selection { background-color: #264f78; color: #ffffff; }"
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
        saved_level = load_desktop_settings().get("COCKPITDECKS_LOG_LEVEL", "INFO").upper()
        self._log_level_combo.setCurrentText(saved_level if saved_level in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO")
        self._log_level_combo.setToolTip("Log level sent to cockpitdecks on next start (also filters display)")
        self._log_level_combo.currentTextChanged.connect(self._on_log_level_changed)
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
        self._log_search_input.setPlaceholderText("Search logs...")
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
        self.releases_tab = ReleasesTab()
        self.releases_tab.installed.connect(self._on_release_installed)
        self.releases_tab.log_line.connect(self._append)

        self.topology_tab = TopologyTab()

        self.tabs = QTabWidget()
        self.tabs.addTab(tab_status, "Status")
        self.tabs.addTab(self.topology_tab, "Topology")
        self.tabs.addTab(tab_decks, "Decks")
        self.tabs.addTab(tab_config, "Config")
        self.tabs.addTab(self.releases_tab, "Releases")
        self.tabs.addTab(tab_diag, "Diagnostics")
        self.tabs.addTab(tab_logs, "Logs")

        root.addWidget(header)
        root.addWidget(action_bar)
        root.addWidget(self.tabs, 1)

        status = QStatusBar(self)
        status.showMessage("Ready")
        self.setStatusBar(status)

        # ── Connections ──
        self.btn_check.clicked.connect(self.run_preflight)

        self.btn_start.clicked.connect(self.start_cockpitdecks)
        self.btn_restart.clicked.connect(self.restart_cockpitdecks)
        self.btn_stop.clicked.connect(self.stop_cockpitdecks)
        self.btn_reload.clicked.connect(self.reload_decks)
        self.btn_decks_import.clicked.connect(self._import_deck_zip)
        self.btn_decks_select.clicked.connect(self._use_selected_decks_target)
        self.btn_decks_reveal.clicked.connect(self._reveal_selected_decks_target)
        self.btn_clear_logs.clicked.connect(self.log.clear)
        self.btn_copy_logs.clicked.connect(self._copy_log_selection)
        self.log_line.connect(self._append)
        self.live_poll_done.connect(self._apply_live_poll)
        self.settings_form.settings_saved.connect(self._on_settings_saved)

        self.setStyleSheet(MAIN_WINDOW_QSS)
        self.btn_clear_logs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_logs.setCursor(Qt.CursorShape.PointingHandCursor)

        # Cmd+F / Ctrl+F to toggle log search, Escape to close
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self._log_search_toggle)
        esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self._log_search_bar)
        esc_shortcut.activated.connect(self._log_search_close)
        self._refresh_launch_targets()
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
    _LOG_LEVEL_RE = re.compile(r"^\[.+?\]\s+(CRITICAL|ERROR|WARNING|INFO|DEBUG|DEPRECATION|SPAM)\b")

    # Log-analysis patterns (matched against raw launcher stdout lines)
    _LOG_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\]")
    _LOG_INIT_START_RE = re.compile(r"Initializing Cockpitdecks\.\.")
    _LOG_INIT_END_RE = re.compile(r"\.\.initialized")
    _LOG_EXTENSIONS_RE = re.compile(r"loaded extensions (.+)")
    _LOG_MISSING_EXT_RE = re.compile(r"package (\S+) not found")
    _LOG_HW_FOUND_RE = re.compile(r"found (\d+) (\w+)")
    _LOG_USB_CONNECT_RE = re.compile(r"new usb device (.+?) \(serial")
    _LOG_USB_DISCONNECT_RE = re.compile(r"usb device (.+?) was removed")

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

    _SAFE_TEXT_TRANSLATION = str.maketrans({
        "\u2014": "-",
        "\u2026": "...",
        "\u00b7": "|",
        "\u221e": "inf",
        "\u00a9": "(c)",
    })

    def _on_log_level_changed(self, level: str) -> None:
        if level == "All":
            return
        settings = load_desktop_settings()
        settings["COCKPITDECKS_LOG_LEVEL"] = level
        save_desktop_settings(settings)

    def _parse_log_line(self, text: str) -> None:
        """Extract diagnostic events from raw launcher log lines and update the diagnostics tab."""
        from datetime import datetime as _dt

        def _ts(line: str) -> float | None:
            m = self._LOG_TS_RE.match(line)
            if m:
                try:
                    return _dt.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    pass
            return None

        dirty = False

        if self._LOG_INIT_START_RE.search(text):
            self._log_init_start_ts = _ts(text)
            self._log_init_end_ts = None
            self._log_extensions = []
            self._log_missing_ext = []
            self._log_hardware = {}
            self._log_last_usb = ""
            dirty = True

        elif self._LOG_INIT_END_RE.search(text):
            self._log_init_end_ts = _ts(text)
            dirty = True

        elif m := self._LOG_EXTENSIONS_RE.search(text):
            self._log_extensions = [e.strip() for e in m.group(1).split(",")]
            dirty = True

        elif m := self._LOG_MISSING_EXT_RE.search(text):
            pkg = m.group(1)
            if pkg not in self._log_missing_ext:
                self._log_missing_ext.append(pkg)
            dirty = True

        elif m := self._LOG_HW_FOUND_RE.search(text):
            count, kind = int(m.group(1)), m.group(2).lower()
            self._log_hardware[kind] = count
            dirty = True

        elif m := self._LOG_USB_CONNECT_RE.search(text):
            self._log_last_usb = f"\u2191 {m.group(1).strip()}"
            dirty = True

        elif m := self._LOG_USB_DISCONNECT_RE.search(text):
            self._log_last_usb = f"\u2193 {m.group(1).strip()}"
            dirty = True

        if dirty and hasattr(self, "diag_tab"):
            init_s: float | None = None
            if self._log_init_start_ts is not None and self._log_init_end_ts is not None:
                init_s = self._log_init_end_ts - self._log_init_start_ts
            self.diag_tab.update_log_analysis(
                init_s, self._log_extensions, self._log_missing_ext,
                self._log_hardware, self._log_last_usb,
            )

    def _append(self, text: str) -> None:
        if not text:
            return

        self._parse_log_line(text)

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

        safe_text = text.translate(self._SAFE_TEXT_TRANSLATION)
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

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.log.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(safe_text, fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()
        self._set_status_feedback(safe_text)

    def _copy_log_selection(self) -> None:
        cursor = self.log.textCursor()
        text = cursor.selectedText() if cursor.hasSelection() else self.log.toPlainText()
        if text:
            # QPlainTextEdit selections may use U+2029 instead of \n
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
        if len(msg) > 160:
            msg = msg[:157] + "..."
        self.statusBar().showMessage(msg)

    def _workspace(self) -> Path:
        return Path.home() / "GitHub"

    def _repo(self, name: str) -> Path:
        return self._workspace() / name

    def _settings_with_updates(self, **updates: str) -> dict[str, str]:
        settings = load_desktop_settings()
        settings.update({k: v for k, v in updates.items()})
        return settings

    def _managed_decks_dir(self) -> Path:
        return managed_decks_dir()

    def _normalize_cd_path_entries(self, raw: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for chunk in raw.replace(";", ":").split(":"):
            s = chunk.strip()
            if not s:
                continue
            key = _path_key(s)
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out

    def _ensure_search_root(self, root: Path) -> None:
        settings = load_desktop_settings()
        paths = self._normalize_cd_path_entries(settings.get("COCKPITDECKS_PATH", ""))
        key = _path_key(str(root))
        if key not in {_path_key(p) for p in paths}:
            paths.append(str(root))
            save_desktop_settings(self._settings_with_updates(COCKPITDECKS_PATH=":".join(paths)))
            self.settings_form.reload_from_disk()

    def _extract_manifest_id(self, manifest_path: Path) -> str:
        try:
            import yaml

            with manifest_path.open("r", encoding="utf-8") as fp:
                loaded = yaml.safe_load(fp) or {}
            data = loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            raise ValueError(f"could not read manifest.yaml: {exc}") from exc
        deck_id = str(data.get("id") or "").strip()
        if not deck_id:
            raise ValueError("manifest.yaml is missing required 'id'")
        return deck_id

    def _import_deck_zip(self) -> None:
        zip_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Deck Zip",
            str(Path.home()),
            "Deck zip (*.zip);;All files (*)",
        )
        if not zip_path:
            return
        try:
            imported = self._install_deck_zip(Path(zip_path))
        except Exception as exc:
            self._append(f"[error] deck import failed: {exc}")
            QMessageBox.critical(self, "Import Deck", str(exc))
            return
        self._append(f"[ok] imported deck: {imported}")
        self._refresh_launch_targets()
        selected = imported / "deckconfig"
        if selected.exists():
            self._select_launch_target(str(imported))
        self.refresh_info_panel()

    def _install_deck_zip(self, zip_path: Path) -> Path:
        library = self._managed_decks_dir()
        library.mkdir(parents=True, exist_ok=True)
        temp_dir = library / ".import-tmp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(temp_dir)
            manifest_candidates = sorted(temp_dir.rglob("manifest.yaml"))
            if not manifest_candidates:
                raise ValueError("zip does not contain manifest.yaml")
            manifest_path = manifest_candidates[0]
            deck_root = manifest_path.parent
            if not (deck_root / "deckconfig").is_dir():
                raise ValueError("zip does not contain deckconfig/ next to manifest.yaml")
            deck_id = self._extract_manifest_id(manifest_path)
            target_dir = library / deck_id
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.move(str(deck_root), str(target_dir))
            self._ensure_search_root(library)
            return target_dir
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _is_managed_target(self, target: Path | str) -> bool:
        try:
            target_path = Path(target).expanduser().resolve()
            managed_root = self._managed_decks_dir().resolve()
            target_path.relative_to(managed_root)
            return True
        except (OSError, ValueError):
            return False

    def _source_label(self, info: LaunchTargetInfo) -> str:
        if self._is_managed_target(info.path):
            return "Managed Library"
        return _shorten_filesystem_path(info.root, max_len=40)

    def _configured_launch_target(self) -> str:
        return (load_desktop_settings().get("COCKPITDECKS_TARGET") or "").strip()

    def _launch_log_path(self) -> Path | None:
        raw = (load_desktop_settings().get("COCKPITDECKS_LAUNCH_LOG_PATH") or "").strip()
        return Path(raw).expanduser() if raw else None

    def _crash_log_path(self) -> Path:
        return settings_path().with_name("crash.log")

    def _cockpitdecks_search_roots(self) -> list[Path]:
        raw = (load_desktop_settings().get("COCKPITDECKS_PATH") or "").strip()
        roots: list[Path] = []
        seen: set[str] = set()
        for chunk in raw.replace(";", ":").split(":"):
            s = chunk.strip()
            if not s:
                continue
            p = Path(s).expanduser()
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            if p.is_dir():
                roots.append(p)
        return roots

    def _parse_simple_yaml_meta(self, path: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        current_multiline: str | None = None
        multiline_parts: list[str] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for raw in lines:
            if current_multiline is not None:
                if raw.startswith(" ") or raw.startswith("\t"):
                    multiline_parts.append(raw.strip())
                    continue
                out[current_multiline] = " ".join(part for part in multiline_parts if part).strip()
                current_multiline = None
                multiline_parts = []
            s = raw.strip()
            if not s or s.startswith("#") or ":" not in s:
                continue
            key, value = s.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value in {">", "|"}:
                current_multiline = key
                multiline_parts = []
                continue
            out[key] = value.strip("'\"")
        if current_multiline is not None:
            out[current_multiline] = " ".join(part for part in multiline_parts if part).strip()
        return out

    def _parse_target_metadata(self, aircraft_dir: Path, root: Path) -> LaunchTargetInfo:
        config_path = aircraft_dir / "deckconfig" / "config.yaml"
        aircraft_name = aircraft_dir.name
        deck_names: list[str] = []
        config_ok = True
        config_error = ""
        version = ""
        icao = ""
        manifest_status = ""
        description = ""
        inside_decks = False
        decks_indent = 0

        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return LaunchTargetInfo(
                aircraft_name=aircraft_name,
                path=str(aircraft_dir),
                root=str(root),
                deck_count=0,
                deck_names=[],
                config_ok=False,
                config_error=str(exc),
            )

        for raw in lines:
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            stripped = raw.strip()
            if stripped.startswith("aircraft:"):
                value = stripped.split(":", 1)[1].strip()
                if value:
                    aircraft_name = value.strip("'\"")
                continue
            if stripped == "decks:":
                inside_decks = True
                decks_indent = indent
                continue
            if inside_decks and indent <= decks_indent and not stripped.startswith("- "):
                inside_decks = False
            if inside_decks and stripped.startswith("- name:"):
                value = stripped.split(":", 1)[1].strip()
                if value:
                    deck_names.append(value.strip("'\""))

        if not deck_names:
            config_ok = False
            config_error = "no deck entries found"

        has_manifest = False
        config_name = ""
        layout_infos: list[tuple[str, str]] = []

        manifest_path = aircraft_dir / "manifest.yaml"
        if manifest_path.is_file():
            has_manifest = True
            meta = self._parse_simple_yaml_meta(manifest_path)
            version = (meta.get("version") or "").strip()
            icao = (meta.get("icao") or "").strip()
            manifest_status = (meta.get("status") or "").strip()
            description = (meta.get("description") or "").strip()
            config_name = (meta.get("name") or "").strip()
            manifest_aircraft = (meta.get("aircraft") or config_name or "").strip()
            if manifest_aircraft:
                aircraft_name = manifest_aircraft
            layout_infos = self._parse_manifest_layouts(manifest_path)

        return LaunchTargetInfo(
            aircraft_name=aircraft_name,
            path=str(aircraft_dir),
            root=str(root),
            deck_count=len(deck_names),
            deck_names=deck_names,
            config_ok=config_ok,
            config_error=config_error,
            has_manifest=has_manifest,
            config_name=config_name,
            version=version,
            icao=icao,
            manifest_status=manifest_status,
            description=description,
            layout_infos=layout_infos,
        )

    def _parse_manifest_layouts(self, manifest_path: Path) -> list[tuple[str, str]]:
        """Return (layout_id, status) pairs from the layouts: list in manifest.yaml."""
        results: list[tuple[str, str]] = []
        in_layouts = False
        current_id = ""
        current_status = ""

        try:
            lines = manifest_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return results

        for raw in lines:
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip())
            stripped = raw.strip()

            if stripped == "layouts:":
                in_layouts = True
                continue

            if in_layouts:
                if indent == 0:
                    if current_id:
                        results.append((current_id, current_status))
                        current_id = ""
                        current_status = ""
                    in_layouts = False
                elif stripped.startswith("- id:"):
                    if current_id:
                        results.append((current_id, current_status))
                    current_id = stripped.split(":", 1)[1].strip().strip("'\"")
                    current_status = ""
                elif current_id and stripped.startswith("status:"):
                    current_status = stripped.split(":", 1)[1].strip().strip("'\"")

        if current_id:
            results.append((current_id, current_status))

        return results

    def _launch_target_label(self, info: LaunchTargetInfo) -> str:
        root = Path(info.root)
        path = Path(info.path)
        try:
            rel_disp = path.relative_to(root).as_posix()
        except ValueError:
            rel_disp = path.name
        return f"{info.aircraft_name}  ·  {_shorten_filesystem_path(root / rel_disp, max_len=78)}"

    def _discover_launch_targets(self) -> list[LaunchTargetInfo]:
        targets: list[LaunchTargetInfo] = []
        seen: set[str] = set()
        for root in self._cockpitdecks_search_roots():
            try:
                deckconfigs = sorted(root.rglob("deckconfig"))
            except OSError:
                continue
            for deckconfig_dir in deckconfigs:
                if not deckconfig_dir.is_dir() or not (deckconfig_dir / "config.yaml").exists():
                    continue
                aircraft_dir = deckconfig_dir.parent
                try:
                    resolved = str(aircraft_dir.resolve())
                except OSError:
                    resolved = str(aircraft_dir)
                if resolved in seen:
                    continue
                seen.add(resolved)
                targets.append(self._parse_target_metadata(Path(resolved), root))
        targets.sort(key=lambda item: (item.aircraft_name.lower(), item.path.lower()))
        return targets

    def _refresh_launch_targets(self) -> None:
        selected = self._configured_launch_target()
        self._launch_targets = self._discover_launch_targets()
        self._populate_decks_list()

    def _selected_launch_target(self) -> Path | None:
        raw = self._configured_launch_target()
        return Path(raw) if raw else None

    def _matching_launch_targets(self) -> list[LaunchTargetInfo]:
        return list(self._launch_targets)

    def _build_deck_item_widget(self, info: LaunchTargetInfo, *, is_active: bool = False, is_selected: bool = False) -> QWidget:
        managed = self._is_managed_target(info.path)

        # Background = active, border = selected, chips = source
        bg = "#f0fdf4" if is_active else "#f8fafc"
        border = "#3b82f6" if is_selected else "#e2e8f0"

        card = QFrame()
        card.setObjectName("deckcard")
        card.setStyleSheet(
            f"QFrame#deckcard {{ background: {bg};"
            f" border: 2px solid {border}; border-radius: 8px; }}"
        )
        card.setFixedHeight(130)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(3)

        # ── Row 1: aircraft name ──────────────────────────────────
        name_lbl = QLabel(info.aircraft_name)
        name_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #1e293b;")
        name_lbl.setWordWrap(True)
        cl.addWidget(name_lbl)

        # ── Row 2: meta line — version · status · ICAO ───────────
        meta_parts: list[str] = []
        version = str(info.version or "").strip()
        if version:
            meta_parts.append(version if version.startswith("v") else f"v{version}")
        if info.manifest_status:
            meta_parts.append(info.manifest_status)
        if info.icao:
            meta_parts.append(info.icao)
        if meta_parts:
            meta_lbl = QLabel(" · ".join(meta_parts))
            meta_lbl.setStyleSheet("font-size: 10px; color: #64748b;")
            cl.addWidget(meta_lbl)

        # ── Row 3: chips — source + state ─────────────────────────
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 2, 0, 0)
        chips.setSpacing(4)

        src_text = "Imported" if managed else "Local"
        src_bg = "#dbeafe" if managed else "#fef3c7"
        src_fg = "#1d4ed8" if managed else "#92400e"
        src_chip = QLabel(src_text)
        src_chip.setStyleSheet(
            f"font-size: 9px; font-weight: 600; color: {src_fg};"
            f" background: {src_bg}; border-radius: 4px; padding: 1px 5px;"
        )
        chips.addWidget(src_chip)

        if is_active:
            act_chip = QLabel("Active")
            act_chip.setStyleSheet(
                "font-size: 9px; font-weight: 600; color: #15803d;"
                " background: #dcfce7; border-radius: 4px; padding: 1px 5px;"
            )
            chips.addWidget(act_chip)
        elif not info.config_ok:
            err_chip = QLabel("Needs review")
            err_chip.setStyleSheet(
                "font-size: 9px; font-weight: 600; color: #991b1b;"
                " background: #fee2e2; border-radius: 4px; padding: 1px 5px;"
            )
            chips.addWidget(err_chip)
        elif not info.has_manifest:
            nm_chip = QLabel("No manifest")
            nm_chip.setStyleSheet(
                "font-size: 9px; color: #94a3b8;"
                " background: #f1f5f9; border-radius: 4px; padding: 1px 5px;"
            )
            chips.addWidget(nm_chip)

        chips.addStretch(1)
        cl.addLayout(chips)

        cl.addStretch(1)

        # ── Bottom: layout names ──────────────────────────────────
        if info.layout_infos:
            names = ", ".join(lid for lid, _ in info.layout_infos[:4])
            if len(info.layout_infos) > 4:
                names += "…"
        elif info.deck_names:
            names = ", ".join(info.deck_names[:4])
            if len(info.deck_names) > 4:
                names += "…"
        else:
            names = ""
        if names:
            ll = QLabel(names)
            ll.setStyleSheet("font-size: 9px; color: #94a3b8;")
            cl.addWidget(ll)

        # ── Uninstall button (managed decks only) ──────────────────
        if managed and not is_active:
            del_btn = QPushButton("Uninstall")
            del_btn.setStyleSheet(
                "QPushButton { padding: 2px 8px; border-radius: 4px; font-size: 9px; min-height: 0;"
                " color: #b91c1c; border: 1px solid #fecaca; background: transparent; }"
                "QPushButton:hover { background: #fef2f2; }"
            )
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            path = info.path
            del_btn.clicked.connect(lambda _checked, p=path: self._uninstall_managed_deck(p))
            cl.addWidget(del_btn)

        return card

    def _populate_decks_list(self) -> None:
        if not hasattr(self, "_deck_grid_layout"):
            return
        # Clear existing grid widgets
        while self._deck_grid_layout.count():
            child = self._deck_grid_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        active_path = self._configured_launch_target()
        if not self._selected_deck_path:
            self._selected_deck_path = active_path

        matching = self._matching_launch_targets()

        grid_row = 0
        for col_idx, info in enumerate(matching):
            col = col_idx % 4
            if col == 0 and col_idx > 0:
                grid_row += 1
            widget = self._build_deck_item_widget(
                info,
                is_active=(info.path == active_path),
                is_selected=(info.path == self._selected_deck_path),
            )
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            path = info.path
            widget.mousePressEvent = lambda _ev, p=path: self._on_deck_card_clicked(p)
            self._deck_grid_layout.addWidget(widget, grid_row, col)
        grid_row += 1

        # Pushes cards to top when there are few entries
        self._deck_grid_layout.setRowStretch(grid_row, 1)

        total = len(self._launch_targets)
        shown = len(matching)
        self.decks_summary.setText(f"{shown} shown / {total} discovered")
        self._update_decks_actions()

    def _on_deck_card_clicked(self, path: str) -> None:
        self._selected_deck_path = path
        self._populate_decks_list()

    def _select_decks_item_by_path(self, path: str) -> None:
        self._selected_deck_path = path
        self._populate_decks_list()

    def _selected_decks_target_path(self) -> str:
        return self._selected_deck_path

    def _update_decks_actions(self) -> None:
        if not hasattr(self, "btn_decks_select"):
            return
        has_selected = bool(self._selected_deck_path)
        self.btn_decks_select.setEnabled(has_selected)
        self.btn_decks_reveal.setEnabled(has_selected)

    def _select_launch_target(self, path: str) -> None:
        save_desktop_settings(self._settings_with_updates(COCKPITDECKS_TARGET=path))
        self.settings_form.reload_from_disk()
        self._select_decks_item_by_path(path)
        self.refresh_info_panel()

    def _use_selected_decks_target(self) -> None:
        path = self._selected_decks_target_path()
        if not path:
            return
        self._select_launch_target(path)
        self._append(f"[decks] selected launch target: {path}")
        if self._launcher_is_running():
            base = f"http://127.0.0.1:{self._web_listen_port()}"
            ok, msg = api_set_target(path, base_url=base)
            if ok:
                self._append(f"[decks] {msg}")
            else:
                self._append(f"[decks] could not switch live: {msg}")

    def _use_auto_launch_target(self) -> None:
        self._select_launch_target("")
        self._append("[ok] launch target cleared; using auto / simulator-selected mode")

    def _reveal_selected_decks_target(self) -> None:
        path = self._selected_decks_target_path()
        if not path:
            return
        target = Path(path)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            elif sys.platform == "win32":
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(target)])
            self._append(f"[ok] revealed target folder: {target}")
        except OSError as exc:
            self._append(f"[error] could not reveal target folder {target}: {exc}")

    _SEG_ACTIVE = (
        "QPushButton { background: #3b82f6; color: #ffffff; font-size: 12px; font-weight: 600;"
        " border: none; border-radius: 6px; padding: 2px 18px; min-height: 0; }"
    )
    _SEG_INACTIVE = (
        "QPushButton { background: transparent; color: #64748b; font-size: 12px; font-weight: 500;"
        " border: none; border-radius: 6px; padding: 2px 18px; min-height: 0; }"
        "QPushButton:hover { color: #1e293b; background: #e2e8f0; }"
    )

    def _apply_seg_styles(self, active_idx: int) -> None:
        self._decks_seg_installed.setStyleSheet(self._SEG_ACTIVE if active_idx == 0 else self._SEG_INACTIVE)
        self._decks_seg_available.setStyleSheet(self._SEG_ACTIVE if active_idx == 1 else self._SEG_INACTIVE)
        self._decks_seg_archive.setStyleSheet(self._SEG_ACTIVE if active_idx == 2 else self._SEG_INACTIVE)

    def _on_decks_segment_changed(self, idx: int) -> None:
        self._decks_stack.setCurrentIndex(idx)
        self._apply_seg_styles(idx)

    def _resolve_launcher_binary(self) -> Path:
        """Resolve cockpitdecks executable: Config override → managed install → bundled (frozen) → dev dist."""
        override = launcher_binary_path(load_desktop_settings())
        if override is not None:
            return override

        # Managed install: downloaded via the Releases tab.
        from cockpitdecks_desktop.services.github_releases import installed_binary
        managed = installed_binary()
        if managed.exists():
            return managed

        # Frozen desktop app: bundled sidecar fallback.
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates = [
                exe_dir / "cockpitdecks",
                exe_dir / "resources" / "cockpitdecks",
                Path(getattr(sys, "_MEIPASS", exe_dir)) / "cockpitdecks",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return candidate
            return managed  # not found — return managed path for a clear "Missing" error message

        # Dev mode: run the local launcher built in cockpitdecks repo.
        return self._repo("cockpitdecks") / "dist" / "cockpitdecks"

    def _on_release_installed(self, tag: str) -> None:
        """Called after a successful install from the Releases tab."""
        self._append(f"[releases] cockpitdecks {tag} installed — ready to start")
        self._refresh_start_stop_buttons()

    def _uninstall_managed_deck(self, path: str) -> None:
        """Remove a managed deck from the library."""
        import shutil
        target = Path(path)
        if not target.is_dir():
            self._append(f"[error] deck not found: {path}")
            return
        if not self._is_managed_target(target):
            self._append(f"[error] cannot uninstall non-managed deck: {path}")
            return
        name = target.name
        shutil.rmtree(target)
        self._append(f"[packs] uninstalled {name}")
        self._refresh_pack_views()

    def _on_pack_installed(self, tag: str) -> None:
        """Called after a deck pack is installed from the Deck Packs tab."""
        self._append(f"[packs] deck pack {tag} installed")
        self._refresh_pack_views()

    def _on_pack_uninstalled(self, pack_id: str) -> None:
        """Called after a deck pack is uninstalled."""
        self._append(f"[packs] deck pack {pack_id} uninstalled")
        self._refresh_pack_views()

    def _refresh_pack_views(self) -> None:
        self._refresh_launch_targets()
        self.deck_packs_tab.refresh()
        self.deck_packs_archive.refresh()

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
            self.btn_start.setToolTip("Start cockpitdecks using paths and env from the Config tab.")
        elif cmd_busy:
            self.btn_start.setToolTip("Disabled while another task is running (e.g. Preflight). Wait for it to finish.")
        elif running:
            self.btn_start.setToolTip("Launcher was already started from this app. Use Stop, then you can Start again.")
        elif not launcher_ok:
            p = self._resolve_launcher_binary()
            self.btn_start.setToolTip(
                f"Launcher binary not found:\n{p}\n\n"
                f"Set “cockpitdecks path” on the Config tab, build the executable in the cockpitdecks repo, "
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
            self.btn_restart.setToolTip("Restart cockpitdecks.")
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
        self.btn_check.setEnabled(not busy)

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
        self._refresh_launch_targets()
        self.refresh_info_panel()

    def _desktop_app_version(self) -> str:
        from cockpitdecks_desktop import __version__

        return __version__

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

    def _set_diag_warning(self, text: str, level: str = "neutral") -> None:
        self.info_diag_warning.setText(text)
        styles = {
            "neutral": "font-size: 13px; color: #334155; border: none; padding: 0;",
            "ok": "font-size: 13px; color: #166534; border: none; padding: 0; font-weight: 500;",
            "warn": "font-size: 13px; color: #b45309; border: none; padding: 0; font-weight: 500;",
            "error": "font-size: 13px; color: #991b1b; border: none; padding: 0; font-weight: 500;",
        }
        self.info_diag_warning.setStyleSheet(styles.get(level, styles["neutral"]))
        self.diag_health_runtime.setText(text)
        self._style_status_value(self.diag_health_runtime, text)

    def _update_diagnostics_warning(self, metrics: dict | None) -> None:
        if not isinstance(metrics, dict):
            self._set_diag_warning("—", "neutral")
            return
        cockpit = metrics.get("cockpit") if isinstance(metrics.get("cockpit"), dict) else {}
        queue_depth = cockpit.get("event_queue_depth")
        dirty_marks = cockpit.get("dirty_marks")
        dirty_flushes = cockpit.get("dirty_flushes")
        dirty_rendered = cockpit.get("dirty_rendered")

        if isinstance(queue_depth, int) and queue_depth >= 100:
            self._set_diag_warning(f"Queue backlog high: {queue_depth}", "error")
            return
        if isinstance(queue_depth, int) and queue_depth >= 30:
            self._set_diag_warning(f"Queue backlog building: {queue_depth}", "warn")
            return
        if isinstance(dirty_marks, int) and isinstance(dirty_flushes, int) and dirty_marks > 0 and dirty_flushes == 0:
            self._set_diag_warning("Dirty buttons marked but no flushes yet", "warn")
            return
        if isinstance(dirty_rendered, int) and dirty_rendered == 0 and isinstance(dirty_marks, int) and dirty_marks > 0:
            self._set_diag_warning("Marks arriving without rendered output", "warn")
            return
        self._set_diag_warning("Queue and render pipeline look stable", "ok")

    def _refresh_diagnostics_panel(self) -> None:
        settings = load_desktop_settings()
        launcher = self._resolve_launcher_binary()
        launch_log = self._launch_log_path()
        crash_log = self._crash_log_path()
        target = self._selected_launch_target()
        launcher_running = self._launcher_is_running()
        listener = self._cockpit_web_port_listener()
        web_base = cockpit_web_base(settings)
        xp_base = xplane_rest_base(settings)

        # ── Health badges ──
        if not launcher.exists():
            launcher_health, launcher_level = "Missing", "error"
        elif launcher_running:
            launcher_health, launcher_level = "Running", "ok"
        elif self._last_launcher_exit_code not in (None, 0):
            launcher_health, launcher_level = f"Exited ({self._last_launcher_exit_code})", "error"
        elif listener is not None:
            launcher_health, launcher_level = f"Port {self._web_listen_port()} in use", "warn"
        else:
            launcher_health, launcher_level = "Ready", "neutral"

        cockpit_text = self.info_cockpit_web.text()
        if self._last_session_info is not None and self._last_session_info.ok:
            cockpit_text = f"OK | {self._last_session_info.aircraft}"
        cockpit_level = "ok" if "ok" in cockpit_text.lower() else ("error" if "unreachable" in cockpit_text.lower() else "neutral")

        xp_text = self.info_xplane.text()
        xp_level = "ok" if "v" in xp_text.lower() and "unreachable" not in xp_text.lower() else ("error" if "unreachable" in xp_text.lower() else "neutral")

        self.diag_tab.update_health(launcher_health, launcher_level, cockpit_text, cockpit_level, xp_text, xp_level)

        # ── Connectivity checks ──
        def _check_ok(text: str) -> bool | None:
            t = text.lower()
            if "unreachable" in t or "error" in t or "refused" in t:
                return False
            if "ok" in t or "v1" in t or "v2" in t or "v3" in t or text.strip() not in ("—", "…", ""):
                return True
            return None

        ckpt_text = f"{web_base} -> {self.info_cockpit_web.text()}"
        xplane_text = f"{xp_base} -> {self.info_xplane.text()}"

        hw_text = "\u2014"
        hw_ok: bool | None = None
        if self._last_session_info is not None and self._last_session_info.ok:
            detail = self._last_session_info.decks_detail
            if detail:
                connected = [d for d in detail if d.get("connected") and not d.get("virtual")]
                virtual = [d for d in detail if d.get("virtual")]
                physical = [d for d in detail if not d.get("virtual")]
                parts = []
                if physical:
                    conn_count = len(connected)
                    phys_count = len(physical)
                    status = "all connected" if conn_count == phys_count else f"{conn_count}/{phys_count} connected"
                    names = ", ".join(d.get("name", "?") for d in physical[:3]) + ("…" if len(physical) > 3 else "")
                    parts.append(f"{names} ({status})")
                if virtual:
                    parts.append(f"{len(virtual)} virtual")
                hw_text = "  ·  ".join(parts) if parts else "no decks"
                hw_ok = len(connected) > 0 if physical else None
            else:
                hw_text = self._last_session_info.decks
                hw_ok = "no decks" not in hw_text.lower()
        elif self._last_session_info is not None:
            hw_text = "unknown (no session)"

        self.diag_tab.update_checks(
            ckpt_text, _check_ok(self.info_cockpit_web.text()),
            xplane_text, _check_ok(self.info_xplane.text()),
            hw_text, hw_ok,
        )

        # ── Runtime pressure ──
        queue_text = self.metric_queue_depth.text()
        queue_depth = int(queue_text) if queue_text not in ("—", "") else None
        self.diag_tab.update_pressure(
            queue_depth=queue_depth,
            queue_status=self.diag_health_runtime.text(),
            ws_rate=self.metric_ws_rate.text(),
            dataref_rate=self.metric_dataref_rate.text(),
            render_rate=self.metric_dirty_rendered.text(),
            marks_per_flush=self.metric_marks_per_flush.text(),
            uptime=self.metric_uptime.text(),
        )

        # ── Startup details ──
        self.diag_tab.update_startup(
            launcher=f"{launcher_health} | {_shorten_filesystem_path(launcher, max_len=80)}",
            target=_shorten_filesystem_path(target, max_len=96) if target else "Auto / simulator-selected",
            log=_shorten_filesystem_path(launch_log, max_len=96) if launch_log else "Not configured",
            crash=(_shorten_filesystem_path(crash_log, max_len=80) + (" | present" if crash_log.exists() else " | none yet")),
            exit_code=str(self._last_launcher_exit_code) if self._last_launcher_exit_code is not None else "—",
        )

        # ── Topology diagram ──
        session = self._last_session_info
        self.topology_tab.update_topology(
            launcher_status=launcher_level,
            launcher_label=launcher_health,
            launcher_custom=settings.get("COCKPITDECKS_LAUNCHER_USE_CUSTOM", "0") == "1",
            launcher_pid=self._launcher_process.pid if self._launcher_is_running() else None,
            cockpit_status=cockpit_level,
            cockpit_label=cockpit_text,
            cockpit_version=session.version if session and session.ok else "",
            cockpit_uptime=self.metric_uptime.text(),
            cockpit_aircraft=session.aircraft if session and session.ok else "",
            xplane_status=xp_level,
            xplane_label=xp_text,
            desktop_label=f"v{self._desktop_app_version()}",
            cockpit_reachable=_check_ok(self.info_cockpit_web.text()),
            xplane_reachable=_check_ok(self.info_xplane.text()),
            launcher_running=launcher_running,
            decks=session.decks_detail if session and session.ok else [],
            dataref_rate=self.metric_dataref_rate.text(),
            ws_rate=self.metric_ws_rate.text(),
            cockpit_web_host=settings.get("COCKPIT_WEB_HOST", "127.0.0.1"),
            cockpit_web_port=settings.get("COCKPIT_WEB_PORT", "7777"),
        )

    def _build_diagnostics_bundle(self) -> dict:
        settings = load_desktop_settings()
        session = self._last_session_info or fetch_session_info(base_url=cockpit_web_base(settings))
        metrics = self._last_metrics_obj
        if metrics is None:
            metrics, _ = cockpitdecks_metrics_json(base_url=cockpit_web_base(settings))
        launch_target = self._selected_launch_target()
        log_path = self._launch_log_path()
        return {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "desktop_app_version": self._desktop_app_version(),
            "settings_path": str(settings_path()),
            "settings": settings,
            "launcher_binary": str(self._resolve_launcher_binary()),
            "launch_target": str(launch_target) if launch_target is not None else "",
            "launch_log_path": str(log_path) if log_path is not None else "",
            "last_preflight_at": self.info_last_check.text(),
            "diagnostics_summary": self.info_diag_warning.text(),
            "session": {
                "ok": session.ok,
                "version": session.version,
                "aircraft": session.aircraft,
                "decks": session.decks,
                "config_path": session.config_path,
                "error": session.error,
            },
            "metrics": metrics,
            "logs": self.log.toPlainText().splitlines()[-500:],
        }

    def export_diagnostics_bundle(self) -> None:
        default_name = f"cockpitdecks-desktop-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export diagnostics bundle",
            str(Path.home() / default_name),
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        out = Path(path).expanduser()
        bundle = self._build_diagnostics_bundle()
        out.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        self._append(f"[ok] diagnostics exported to {out}")

    def _apply_metrics_visuals(self, metrics: dict | None) -> None:
        if not isinstance(metrics, dict):
            self.metric_cpu_label.setText("CPU (process) —")
            self.metric_mem_label.setText("Memory (RSS) —")
            self.metric_cpu_spark.clear()
            self.metric_mem_spark.clear()
            self.metric_threads.setText("—")
            self.metric_variables.setText("—")
            self.metric_datarefs.setText("—")
            self.metric_dataref_rate.setText("—")
            self.metric_queue_depth.setText("—")
            self.metric_dirty_rendered.setText("—")
            self.metric_ws_rate.setText("—")
            self.metric_marks_per_flush.setText("—")
            self.metric_uptime.setText("—")
            self._prev_dataref_values_processed = None
            self._prev_dataref_poll_ts = None
            self._prev_dirty_rendered = None
            self._prev_dirty_poll_ts = None
            self._prev_ws_messages_received = None
            self._prev_dirty_marks = None
            self._prev_dirty_flushes = None
            self._prev_rate_poll_ts = None
            self._update_diagnostics_warning(None)
            self._update_latency_display(None)
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

        if isinstance(cpu, (int, float)):
            cpu_f = float(cpu)
            self.metric_cpu_label.setText(f"CPU (process) {cpu_f:.1f}%")
            cpu_color = (
                QColor("#ef4444") if cpu_f >= 85 else
                QColor("#f59e0b") if cpu_f >= 60 else
                QColor("#22c55e")
            )
            self.metric_cpu_spark.push(cpu_f, cpu_color)
        else:
            self.metric_cpu_label.setText("CPU (process) —")

        if isinstance(rss_mb, (int, float)):
            self.metric_mem_label.setText(f"Memory (RSS) {float(rss_mb):.1f} MB")
            self.metric_mem_spark.push(float(rss_mb))
        else:
            self.metric_mem_label.setText("Memory (RSS) —")

        self.metric_threads.setText(str(threads) if isinstance(threads, int) else "—")
        self.metric_variables.setText(str(vars_n) if isinstance(vars_n, int) else "—")
        self.metric_datarefs.setText(str(drefs) if isinstance(drefs, int) else "—")

        # Dataref/s rate from traffic counters
        traffic = metrics.get("dataref_traffic") if isinstance(metrics.get("dataref_traffic"), dict) else {}
        cur_vals = traffic.get("dataref_values_processed")
        now_ts = time.time()
        rate_dt = now_ts - self._prev_rate_poll_ts if self._prev_rate_poll_ts is not None else None
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

        ws_messages = traffic.get("ws_messages_received")
        if isinstance(ws_messages, (int, float)) and self._prev_ws_messages_received is not None and isinstance(rate_dt, (int, float)) and rate_dt > 0.5:
            ws_rate = (ws_messages - self._prev_ws_messages_received) / rate_dt
            self.metric_ws_rate.setText(f"{ws_rate:.1f}")
        else:
            self.metric_ws_rate.setText("—")
        if isinstance(ws_messages, (int, float)):
            self._prev_ws_messages_received = int(ws_messages)

        dirty_marks = c.get("dirty_marks")
        dirty_flushes = c.get("dirty_flushes")
        if isinstance(dirty_marks, (int, float)) and isinstance(dirty_flushes, (int, float)):
            if self._prev_dirty_marks is not None and self._prev_dirty_flushes is not None:
                marks_delta = int(dirty_marks) - self._prev_dirty_marks
                flush_delta = int(dirty_flushes) - self._prev_dirty_flushes
                if flush_delta > 0:
                    self.metric_marks_per_flush.setText(f"{marks_delta / flush_delta:.1f}")
                elif marks_delta > 0:
                    self.metric_marks_per_flush.setText("∞")
                else:
                    self.metric_marks_per_flush.setText("0")
            else:
                self.metric_marks_per_flush.setText("—")
            self._prev_dirty_marks = int(dirty_marks)
            self._prev_dirty_flushes = int(dirty_flushes)
        else:
            self.metric_marks_per_flush.setText("—")
            self._prev_dirty_marks = None
            self._prev_dirty_flushes = None

        self._prev_rate_poll_ts = now_ts

        if isinstance(uptime_s, (int, float)):
            uptime_i = int(uptime_s)
            h, rem = divmod(uptime_i, 3600)
            m, sec = divmod(rem, 60)
            self.metric_uptime.setText(f"{h:d}:{m:02d}:{sec:02d}")
        else:
            self.metric_uptime.setText("—")
        self._update_diagnostics_warning(metrics)
        self._update_latency_display(metrics)

    def _update_latency_display(self, metrics: dict | None) -> None:
        """Update latency gauges and thread breakdown on the diagnostics tab."""
        self.diag_tab.update_latency(metrics)

        # Thread breakdown
        if isinstance(metrics, dict):
            diag = metrics.get("diagnostics") if isinstance(metrics.get("diagnostics"), dict) else {}
            threads = diag.get("threads") if isinstance(diag.get("threads"), dict) else {}
            self.diag_tab.update_threads(threads)
        else:
            self.diag_tab.update_threads({})

    def refresh_info_panel(self) -> None:
        ver = self._desktop_app_version()
        self.info_desktop.setText(f"v{ver}")
        self._header_version.setText(f"v{ver}")

        launcher = self._resolve_launcher_binary()
        wport = self._web_listen_port()
        tip_lines: list[str] = [f"Full path:\n{launcher}"]
        path_disp = _shorten_filesystem_path(launcher)
        selected_target = self._selected_launch_target()
        if selected_target is not None:
            tip_lines.append(f"Launch target:\n{selected_target}")
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
        self._refresh_diagnostics_panel()
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
        self._last_session_info = session_info
        self._last_metrics_obj = metrics_obj
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
        self._refresh_diagnostics_panel()

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

    def start_cockpitdecks(self) -> None:
        # Reset log-analysis state for the new launch
        self._log_init_start_ts = None
        self._log_init_end_ts = None
        self._log_extensions = []
        self._log_missing_ext = []
        self._log_hardware = {}
        self._log_last_usb = ""

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
        command = [str(launcher)]
        target = self._selected_launch_target()
        if target is not None:
            if not target.exists():
                self._append(f"[launch] selected target not found: {target}")
                self.refresh_info_panel()
                return
            command.append(str(target))
        child_env = os.environ.copy()
        child_env.update(launch_env_overlay())
        self._launcher_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=child_env,
        )
        self._append(f"[launch] started cockpitdecks (pid={self._launcher_process.pid})")
        if target is not None:
            self._append(f"[launch] target: {target}")
        log_path = self._launch_log_path()
        if log_path is not None:
            self._append(f"[launch] appending launcher output to {log_path}")
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
        log_path = self._launch_log_path()
        launcher = self._resolve_launcher_binary()
        target = self._selected_launch_target()

        def _reader() -> None:
            assert proc.stdout is not None
            fp = None
            if log_path is not None:
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    fp = log_path.open("a", encoding="utf-8", buffering=1)
                    header = f"=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} pid={proc.pid} launcher={launcher}"
                    if target is not None:
                        header += f" target={target}"
                    fp.write(header + " ===\n")
                except OSError as exc:
                    self.log_line.emit(f"[launch] could not open log file {log_path}: {exc}")
                    fp = None
            try:
                for line in proc.stdout:
                    msg = line.rstrip("\n")
                    if not msg:
                        continue
                    self.log_line.emit(msg)
                    if fp is not None:
                        try:
                            fp.write(msg + "\n")
                        except OSError as exc:
                            self.log_line.emit(f"[launch] stopped writing log file {log_path}: {exc}")
                            fp.close()
                            fp = None
                rc = proc.poll()
                if rc is not None:
                    self._last_launcher_exit_code = rc
                    exit_msg = f"[launch] cockpitdecks exited (code={rc})"
                    self.log_line.emit(exit_msg)
                    if fp is not None:
                        fp.write(exit_msg + "\n")
                    self.log_line.emit("[launch] tip: open the Status tab and use Refresh status to update the panel")
            finally:
                if fp is not None:
                    fp.close()

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
