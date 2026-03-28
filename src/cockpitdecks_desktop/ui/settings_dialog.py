from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_desktop.services import desktop_settings


# ── Helpers ────────────────────────────────────────────────────────────────


def _path_key(p: str) -> str:
    try:
        return str(Path(p).expanduser().resolve())
    except OSError:
        return str(Path(p).expanduser())


def _section_heading(title: str) -> QWidget:
    """Bold section title with a separator line underneath."""
    w = QWidget()
    vl = QVBoxLayout(w)
    vl.setContentsMargins(0, 10, 0, 2)
    vl.setSpacing(4)
    lbl = QLabel(title)
    lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #1e293b; border: none;")
    vl.addWidget(lbl)
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("color: #e2e5eb; max-height: 1px; border: none;")
    vl.addWidget(sep)
    return w


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #374151; border: none; margin-top: 6px;")
    return lbl


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("font-size: 10px; color: #6b7280; border: none; margin-bottom: 2px;")
    return lbl


def _browse_row(line_edit: QLineEdit, btn_label: str = "Browse…") -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(line_edit, 1)
    btn = QPushButton(btn_label)
    btn.setFixedWidth(80)
    row.addWidget(btn)
    return row, btn  # type: ignore[return-value]


# ── Main form widget ───────────────────────────────────────────────────────


class SettingsFormWidget(QWidget):
    """Grouped settings editor. Auto-saves 450 ms after the last change."""

    settings_saved = Signal()

    def __init__(self, parent: QWidget | None = None, data: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        data = data if data is not None else desktop_settings.load()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 20)
        root.setSpacing(0)

        # ── Section: Launcher ──────────────────────────────────────────
        root.addWidget(_section_heading("Launcher"))

        root.addWidget(_field_label("Release launcher"))
        self.ed_launcher = QLineEdit(data.get("COCKPITDECKS_LAUNCHER_PATH", ""))
        self.ed_launcher.setPlaceholderText("Leave empty to use managed install or bundled binary")
        row_launcher, btn_launcher = _browse_row(self.ed_launcher)
        btn_launcher.clicked.connect(self._browse_launcher)
        root.addLayout(row_launcher)
        root.addWidget(_hint(
            "Full path to the cockpitdecks binary for normal use. "
            "Leave empty to use the version installed via the Releases tab."
        ))

        root.addWidget(_field_label("Dev launcher"))
        self.ed_launcher_dev = QLineEdit(data.get("COCKPITDECKS_LAUNCHER_PATH_DEV", ""))
        self.ed_launcher_dev.setPlaceholderText("e.g. ~/GitHub/cockpitdecks/scripts/cockpitdecks.sh")
        row_launcher_dev, btn_launcher_dev = _browse_row(self.ed_launcher_dev)
        btn_launcher_dev.clicked.connect(self._browse_launcher_dev)
        root.addLayout(row_launcher_dev)
        root.addWidget(_hint(
            "Path to your local development script or binary. "
            "Switch between Release and Dev using the toggle in the action bar."
        ))

        # ── Section: Aircraft search paths ────────────────────────────
        root.addWidget(_section_heading("Aircraft search paths"))

        root.addWidget(_field_label("Deck config roots"))
        self.list_cd_path = QListWidget()
        self.list_cd_path.setMinimumHeight(100)
        self.list_cd_path.setMaximumHeight(160)
        self.list_cd_path.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_cd_path.setAlternatingRowColors(True)
        self._load_cd_path_list(data.get("COCKPITDECKS_PATH", ""))
        root.addWidget(self.list_cd_path)

        btn_cd_add = QPushButton("Add folder…")
        btn_cd_add.clicked.connect(self._browse_cd_path_add)
        btn_cd_remove = QPushButton("Remove selected")
        btn_cd_remove.clicked.connect(self._remove_cd_path_selected)
        cd_btn_row = QHBoxLayout()
        cd_btn_row.setContentsMargins(0, 4, 0, 0)
        cd_btn_row.addWidget(btn_cd_add)
        cd_btn_row.addWidget(btn_cd_remove)
        cd_btn_row.addStretch(1)
        root.addLayout(cd_btn_row)
        root.addWidget(_hint(
            "Folders where Cockpitdecks looks for aircraft configs (directories containing "
            "a deckconfig/ subfolder). Add your cockpitdecks-configs/decks checkout here."
        ))

        # ── Section: Simulator connection ─────────────────────────────
        root.addWidget(_section_heading("Simulator connection"))

        root.addWidget(_field_label("X-Plane Web API host"))
        self.ed_api_host = QLineEdit(data.get("API_HOST", "127.0.0.1"))
        self.ed_api_host.setPlaceholderText("127.0.0.1")
        root.addWidget(self.ed_api_host)

        root.addWidget(_field_label("X-Plane Web API port"))
        self.ed_api_port = QLineEdit(data.get("API_PORT", "8086"))
        self.ed_api_port.setPlaceholderText("8086")
        root.addWidget(self.ed_api_port)

        root.addWidget(_field_label("Remote simulator host"))
        self.ed_sim_host = QLineEdit(data.get("SIMULATOR_HOST", ""))
        self.ed_sim_host.setPlaceholderText("Leave empty for local X-Plane (127.0.0.1)")
        root.addWidget(self.ed_sim_host)
        root.addWidget(_hint(
            "Only set Remote simulator host if X-Plane runs on a different machine. "
            "The API host/port default to 127.0.0.1:8086 for a local install."
        ))

        # ── Section: Advanced ─────────────────────────────────────────
        root.addWidget(_section_heading("Advanced"))

        root.addWidget(_field_label("Desktop poll host"))
        self.ed_web_host = QLineEdit(data.get("COCKPIT_WEB_HOST", "127.0.0.1"))
        self.ed_web_host.setPlaceholderText("127.0.0.1")
        root.addWidget(self.ed_web_host)

        root.addWidget(_field_label("Desktop poll port"))
        self.ed_web_port = QLineEdit(data.get("COCKPIT_WEB_PORT", "7777"))
        self.ed_web_port.setPlaceholderText("7777")
        root.addWidget(self.ed_web_port)
        root.addWidget(_hint(
            "This app polls Cockpitdecks at this host:port for live status and metrics. "
            "Must match the Flask port Cockpitdecks binds to (set in its environ.yaml)."
        ))

        root.addWidget(_field_label("Launch log file"))
        self.ed_launch_log = QLineEdit(data.get("COCKPITDECKS_LAUNCH_LOG_PATH", ""))
        self.ed_launch_log.setPlaceholderText("Optional — append all launcher output to this file")
        row_launch_log, btn_launch_log = _browse_row(self.ed_launch_log)
        btn_launch_log.clicked.connect(self._browse_launch_log)
        root.addLayout(row_launch_log)
        root.addWidget(_hint("In addition to the Logs tab, stdout is also written to this file."))

        root.addStretch(1)

        # ── Auto-save timer ───────────────────────────────────────────
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_save)

        for ed in (
            self.ed_launcher,
            self.ed_launcher_dev,
            self.ed_launch_log,
            self.ed_sim_host,
            self.ed_api_host,
            self.ed_api_port,
            self.ed_web_host,
            self.ed_web_port,
        ):
            ed.textChanged.connect(self._schedule_save)

    # ── Path list helpers ─────────────────────────────────────────────────

    def _load_cd_path_list(self, raw: str) -> None:
        self.list_cd_path.clear()
        for chunk in raw.replace(";", ":").split(":"):
            s = chunk.strip()
            if s:
                self.list_cd_path.addItem(QListWidgetItem(s))

    def _existing_cd_path_keys(self) -> set[str]:
        return {_path_key(self.list_cd_path.item(i).text()) for i in range(self.list_cd_path.count())}

    # ── Save ──────────────────────────────────────────────────────────────

    def _schedule_save(self) -> None:
        self._save_timer.stop()
        self._save_timer.start(450)

    def _flush_save(self) -> None:
        desktop_settings.save(self.values())
        self.settings_saved.emit()

    # ── Browse dialogs ────────────────────────────────────────────────────

    def _browse_launcher(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select release cockpitdecks binary",
            str(Path.home() / "GitHub" / "cockpitdecks" / "dist"),
            "cockpitdecks (cockpitdecks cockpitdecks.exe);;All files (*)",
        )
        if path:
            self.ed_launcher.setText(path)

    def _browse_launcher_dev(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select dev cockpitdecks script",
            str(Path.home() / "GitHub" / "cockpitdecks" / "scripts"),
            "Shell scripts (*.sh);;All files (*)",
        )
        if path:
            self.ed_launcher_dev.setText(path)

    def _browse_launch_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Select launch log file",
            str(Path.home() / "cockpitdecks-launch.log"),
            "Log files (*.log *.txt);;All files (*)",
        )
        if path:
            self.ed_launch_log.setText(path)

    def _browse_cd_path_add(self) -> None:
        start = str(Path.home() / "GitHub" / "cockpitdecks-configs" / "decks")
        if not Path(start).is_dir():
            start = str(Path.home())
        d = QFileDialog.getExistingDirectory(
            self, "Add aircraft search root", start,
        )
        if not d:
            return
        if _path_key(d) in self._existing_cd_path_keys():
            return
        self.list_cd_path.addItem(QListWidgetItem(d))
        self._schedule_save()

    def _remove_cd_path_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.list_cd_path.selectedIndexes()}, reverse=True)
        for row in rows:
            self.list_cd_path.takeItem(row)
        if rows:
            self._schedule_save()

    # ── Reload / values ───────────────────────────────────────────────────

    def reload_from_disk(self) -> None:
        """Reload persisted values without triggering a save (e.g. after an external write)."""
        self._save_timer.stop()
        data = desktop_settings.load()
        pairs: list[tuple[QLineEdit, str]] = [
            (self.ed_launcher,     data.get("COCKPITDECKS_LAUNCHER_PATH", "")),
            (self.ed_launcher_dev, data.get("COCKPITDECKS_LAUNCHER_PATH_DEV", "")),
            (self.ed_launch_log,   data.get("COCKPITDECKS_LAUNCH_LOG_PATH", "")),
            (self.ed_sim_host,     data.get("SIMULATOR_HOST", "")),
            (self.ed_api_host,     data.get("API_HOST", "127.0.0.1")),
            (self.ed_api_port,     data.get("API_PORT", "8086")),
            (self.ed_web_host,     data.get("COCKPIT_WEB_HOST", "127.0.0.1")),
            (self.ed_web_port,     data.get("COCKPIT_WEB_PORT", "7777")),
        ]
        for ed, text in pairs:
            ed.blockSignals(True)
            ed.setText(str(text))
            ed.blockSignals(False)
        self._load_cd_path_list(data.get("COCKPITDECKS_PATH", ""))

    def values(self) -> dict[str, str]:
        paths = [self.list_cd_path.item(i).text().strip() for i in range(self.list_cd_path.count())]
        return {
            "COCKPITDECKS_LAUNCHER_PATH":     self.ed_launcher.text().strip(),
            "COCKPITDECKS_LAUNCHER_PATH_DEV": self.ed_launcher_dev.text().strip(),
            "COCKPITDECKS_LAUNCH_LOG_PATH":   self.ed_launch_log.text().strip(),
            "COCKPITDECKS_PATH":              ":".join(p for p in paths if p),
            "SIMULATOR_HOST":                 self.ed_sim_host.text().strip(),
            "API_HOST":                       self.ed_api_host.text().strip(),
            "API_PORT":                       self.ed_api_port.text().strip(),
            "COCKPIT_WEB_HOST":               self.ed_web_host.text().strip(),
            "COCKPIT_WEB_PORT":               self.ed_web_port.text().strip(),
        }
