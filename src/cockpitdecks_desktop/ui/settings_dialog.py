from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
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


def _path_key(p: str) -> str:
    try:
        return str(Path(p).expanduser().resolve())
    except OSError:
        return str(Path(p).expanduser())


class SettingsFormWidget(QWidget):
    """Inline settings editor: paths and API endpoints (see desktop_settings). Auto-saves after edits."""

    settings_saved = Signal()

    def __init__(self, parent: QWidget | None = None, data: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        data = data if data is not None else desktop_settings.load()

        intro = QLabel(
            "<b>Launcher</b> — optional full path to <code>cockpitdecks-launcher</code>. "
            "Leave empty to use the bundled binary (frozen app) or <code>…/cockpitdecks/dist/cockpitdecks-launcher</code> in dev.<br><br>"
            "<b>Launch environment</b> (passed to cockpitdecks-launcher): "
            "<code>SIMULATOR_HOME</code>, <code>COCKPITDECKS_PATH</code> (extra roots where Cockpitdecks looks for aircraft "
            "folders containing <code>deckconfig</code>), optional <code>SIMULATOR_HOST</code>, "
            "<code>API_HOST</code>/<code>API_PORT</code> for the X-Plane Web API.<br><br>"
            "<b>Web host/port</b> at the bottom only affects this app’s live status polling — "
            "not the Cockpitdecks Flask bind (that comes from Cockpitdecks <code>environ.yaml</code> / defaults).<br><br>"
            "<i>Changes are saved automatically.</i>"
        )
        intro.setWordWrap(True)

        self.ed_launcher = QLineEdit(data.get("COCKPITDECKS_LAUNCHER_PATH", ""))
        self.ed_launcher.setPlaceholderText("Optional — auto if empty")
        btn_launcher = QPushButton("Browse…")
        btn_launcher.clicked.connect(self._browse_launcher)
        row_launcher = QHBoxLayout()
        row_launcher.addWidget(self.ed_launcher, 1)
        row_launcher.addWidget(btn_launcher)

        self.ed_xp_home = QLineEdit(data.get("SIMULATOR_HOME", ""))
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_xp)
        row_xp = QHBoxLayout()
        row_xp.addWidget(self.ed_xp_home, 1)
        row_xp.addWidget(btn_browse)

        self.list_cd_path = QListWidget()
        self.list_cd_path.setMinimumHeight(110)
        self.list_cd_path.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_cd_path.setAlternatingRowColors(True)
        self._load_cd_path_list(data.get("COCKPITDECKS_PATH", ""))

        btn_cd_add = QPushButton("Add folder…")
        btn_cd_add.clicked.connect(self._browse_cd_path_add)
        btn_cd_remove = QPushButton("Remove selected")
        btn_cd_remove.clicked.connect(self._remove_cd_path_selected)
        row_cd_btns = QHBoxLayout()
        row_cd_btns.addWidget(btn_cd_add)
        row_cd_btns.addWidget(btn_cd_remove)
        row_cd_btns.addStretch(1)

        cd_wrap = QWidget()
        cd_layout = QVBoxLayout(cd_wrap)
        cd_layout.setContentsMargins(0, 0, 0, 0)
        cd_layout.setSpacing(8)
        cd_layout.addWidget(self.list_cd_path)
        cd_layout.addLayout(row_cd_btns)

        cd_hint = QLabel(
            "Each folder is one search root (saved as a <code>:</code>-separated list, same as Cockpitdecks). "
            "Example: your <code>cockpitdecks-configs/decks</code> checkout or a parent of several aircraft trees."
        )
        cd_hint.setWordWrap(True)
        cd_hint.setStyleSheet("color: #666; font-size: 12px;")
        cd_layout.addWidget(cd_hint)

        self.ed_sim_host = QLineEdit(data.get("SIMULATOR_HOST", ""))
        self.ed_api_host = QLineEdit(data.get("API_HOST", "127.0.0.1"))
        self.ed_api_port = QLineEdit(data.get("API_PORT", "8086"))
        self.ed_web_host = QLineEdit(data.get("COCKPIT_WEB_HOST", "127.0.0.1"))
        self.ed_web_port = QLineEdit(data.get("COCKPIT_WEB_PORT", "7777"))

        form = QFormLayout()
        form.addRow("cockpitdecks-launcher path (optional)", row_launcher)
        form.addRow("SIMULATOR_HOME (X-Plane install)", row_xp)
        form.addRow("COCKPITDECKS_PATH (deck search roots)", cd_wrap)
        form.addRow("SIMULATOR_HOST (optional, remote)", self.ed_sim_host)
        form.addRow("API_HOST", self.ed_api_host)
        form.addRow("API_PORT", self.ed_api_port)
        form.addRow("Poll: Cockpitdecks web host", self.ed_web_host)
        form.addRow("Poll: Cockpitdecks web port", self.ed_web_port)

        root = QVBoxLayout(self)
        root.addWidget(intro)
        root.addLayout(form)
        root.addStretch(1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_save)

        for ed in (
            self.ed_launcher,
            self.ed_xp_home,
            self.ed_sim_host,
            self.ed_api_host,
            self.ed_api_port,
            self.ed_web_host,
            self.ed_web_port,
        ):
            ed.textChanged.connect(self._schedule_save)

    def _load_cd_path_list(self, raw: str) -> None:
        self.list_cd_path.clear()
        # Cockpitdecks core splits on ":" only; accept ";" on disk for hand-edited JSON.
        parts: list[str] = []
        for chunk in raw.replace(";", ":").split(":"):
            s = chunk.strip()
            if s:
                parts.append(s)
        for p in parts:
            self.list_cd_path.addItem(QListWidgetItem(p))

    def _existing_cd_path_keys(self) -> set[str]:
        return {_path_key(self.list_cd_path.item(i).text()) for i in range(self.list_cd_path.count())}

    def _schedule_save(self) -> None:
        self._save_timer.stop()
        self._save_timer.start(450)

    def _flush_save(self) -> None:
        desktop_settings.save(self.values())
        self.settings_saved.emit()

    def _browse_launcher(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select cockpitdecks-launcher",
            str(Path.home() / "GitHub" / "cockpitdecks" / "dist"),
            "cockpitdecks-launcher (cockpitdecks-launcher cockpitdecks-launcher.exe);;All files (*)",
        )
        if path:
            self.ed_launcher.setText(path)

    def _browse_xp(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select X-Plane installation folder", str(Path.home()))
        if d:
            self.ed_xp_home.setText(d)

    def _browse_cd_path_add(self) -> None:
        start = str(Path.home() / "GitHub" / "cockpitdecks-configs" / "decks")
        if not Path(start).is_dir():
            start = str(Path.home())
        d = QFileDialog.getExistingDirectory(
            self,
            "Add folder to COCKPITDECKS_PATH (aircraft roots / deckconfig parents)",
            start,
        )
        if not d:
            return
        key = _path_key(d)
        if key in self._existing_cd_path_keys():
            return
        self.list_cd_path.addItem(QListWidgetItem(d))
        self._schedule_save()

    def _remove_cd_path_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.list_cd_path.selectedIndexes()}, reverse=True)
        for row in rows:
            self.list_cd_path.takeItem(row)
        if rows:
            self._schedule_save()

    def reload_from_disk(self) -> None:
        """Load persisted values without emitting save (e.g. after external edit)."""
        self._save_timer.stop()
        data = desktop_settings.load()
        pairs: list[tuple[QLineEdit, str]] = [
            (self.ed_launcher, data.get("COCKPITDECKS_LAUNCHER_PATH", "")),
            (self.ed_xp_home, data.get("SIMULATOR_HOME", "")),
            (self.ed_sim_host, data.get("SIMULATOR_HOST", "")),
            (self.ed_api_host, data.get("API_HOST", "127.0.0.1")),
            (self.ed_api_port, data.get("API_PORT", "8086")),
            (self.ed_web_host, data.get("COCKPIT_WEB_HOST", "127.0.0.1")),
            (self.ed_web_port, data.get("COCKPIT_WEB_PORT", "7777")),
        ]
        for ed, text in pairs:
            ed.blockSignals(True)
            ed.setText(str(text))
            ed.blockSignals(False)
        self._load_cd_path_list(data.get("COCKPITDECKS_PATH", ""))

    def values(self) -> dict[str, str]:
        # Cockpitdecks joins/splits with ":" (see cockpitdecks.cockpit.Cockpit.get_aircraft_path).
        paths = [self.list_cd_path.item(i).text().strip() for i in range(self.list_cd_path.count())]
        stored_cd = ":".join(p for p in paths if p)
        return {
            "COCKPITDECKS_LAUNCHER_PATH": self.ed_launcher.text().strip(),
            "SIMULATOR_HOME": self.ed_xp_home.text().strip(),
            "COCKPITDECKS_PATH": stored_cd,
            "SIMULATOR_HOST": self.ed_sim_host.text().strip(),
            "API_HOST": self.ed_api_host.text().strip(),
            "API_PORT": self.ed_api_port.text().strip(),
            "COCKPIT_WEB_HOST": self.ed_web_host.text().strip(),
            "COCKPIT_WEB_PORT": self.ed_web_port.text().strip(),
        }
