"""Deck Packs tab — browse and install deck configuration packs from cockpitdecks-configs releases."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_desktop.services import deck_packs as dp
from cockpitdecks_desktop.services.desktop_settings import managed_decks_dir

_BTN_SS = (
    "QPushButton { padding: 3px 10px; border-radius: 5px; font-size: 11px; min-height: 0; }"
    "QPushButton:disabled { color: #a1a6b0; background: #f4f5f7; border-color: #dde0e6; }"
)

_CHANGELOG_RE = re.compile(
    r"^\s*\*{0,2}Full Changelog\*{0,2}:\s*https?://\S+\s*$", re.IGNORECASE
)


def _has_meaningful_notes(body: str) -> bool:
    """Return True if the release body has more than just a changelog URL."""
    stripped = body.strip()
    if not stripped:
        return False
    if _CHANGELOG_RE.match(stripped):
        return False
    return True


def _version_sort_key(version: str) -> tuple:
    version = (version or "").removeprefix("v")
    parts = version.split("-", 1)
    base = parts[0]
    pre = parts[1] if len(parts) > 1 else ""
    try:
        base_tuple = tuple(int(x) for x in base.split("."))
    except ValueError:
        base_tuple = (0,)
    if pre:
        pre_parts = pre.split(".")
        pre_name = pre_parts[0]
        pre_num = int(pre_parts[1]) if len(pre_parts) > 1 and pre_parts[1].isdigit() else 0
        pre_order = {"alpha": 0, "beta": 1, "rc": 2}.get(pre_name, -1)
        return base_tuple + (0, pre_order, pre_num)
    return base_tuple + (1, 0, 0)


def _pack_sort_key(release: dict) -> tuple:
    """Sort packs alphabetically by name, then newest version first."""
    tag = release.get("tag_name", "")
    m = re.match(r"^pack-(.+)-v(.+)$", tag)
    if m:
        pack_name = m.group(1)
        ver_str = m.group(2)
    else:
        pack_name = release.get("name", tag).lower()
        ver_str = "0"
    ver_key = _version_sort_key(ver_str)
    return (pack_name, tuple(-x for x in ver_key))


def _format_size(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


_MANIFEST_KEYS = ("version", "summary", "aircraft", "icao")


def _installed_pack_info() -> dict[str, dict]:
    """Return {pack_id: {version, summary, aircraft, icao}} for installed packs."""
    library = managed_decks_dir()
    if not library.is_dir():
        return {}
    result: dict[str, dict] = {}
    for d in library.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        manifest = d / "manifest.yaml"
        info: dict = {k: "" for k in _MANIFEST_KEYS}
        if manifest.is_file():
            try:
                for line in manifest.read_text(encoding="utf-8").splitlines():
                    for key in _MANIFEST_KEYS:
                        if line.startswith(f"{key}:"):
                            info[key] = line.split(":", 1)[1].strip().strip("'\"")
                            break
            except OSError:
                pass
        result[d.name] = info
    return result


def _pack_id_from_tag(tag: str) -> str:
    m = re.match(r"^pack-(.+)-v(.+)$", tag)
    return m.group(1) if m else ""


def _pack_version_from_tag(tag: str) -> str:
    m = re.match(r"^pack-.+-v(.+)$", tag)
    return m.group(1) if m else ""


def _pack_display_name(release: dict) -> str:
    """Extract a clean display name from the release name or tag."""
    name = release.get("name", "")
    if name:
        # Strip trailing version like "v1.0.1" or "Pack v1.0.1"
        name = re.sub(r"\s+v?[\d.]+(?:-[A-Za-z]+(?:\.\d+)?)?\s*$", "", name).strip()
        # Strip leading "Pack" if redundant
        name = re.sub(r"\s+Pack$", "", name).strip()
        if name:
            return name
    pack_id = _pack_id_from_tag(release.get("tag_name", ""))
    return pack_id.replace("-", " ").title() if pack_id else release.get("tag_name", "Unknown")


def _is_prerelease(release: dict) -> bool:
    return "-" in _pack_version_from_tag(release.get("tag_name", ""))


def _release_display_label(release: dict, *, latest_stable: str = "", latest_prerelease: str = "") -> str:
    version = _pack_version_from_tag(release.get("tag_name", ""))
    if not version:
        return release.get("tag_name", "Unknown")
    label = f"v{version}"
    if version == latest_stable:
        return f"{label}  —  latest"
    if version == latest_prerelease:
        return f"{label}  —  beta"
    return label


class _ReadmeFetchWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, pack_id: str) -> None:
        super().__init__()
        self._pack_id = pack_id

    def run(self) -> None:
        try:
            content = dp.fetch_readme(self._pack_id)
            self.succeeded.emit(content)
        except Exception as exc:
            self.failed.emit(str(exc))


class _ReadmeDialog(QDialog):
    """Dialog that shows a pack README, reading from disk or fetching from GitHub."""

    def __init__(self, pack_id: str, pack_dir: Path | None = None, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"README — {pack_id}")
        self.resize(700, 540)
        self._worker: _ReadmeFetchWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet("font-size: 12px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px;")
        layout.addWidget(self._text, 1)

        self._status = QLabel()
        self._status.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(self._status)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        if pack_dir is not None:
            readme = pack_dir / "README.md"
            if readme.is_file():
                try:
                    self._text.setMarkdown(readme.read_text(encoding="utf-8"))
                    return
                except OSError:
                    pass

        self._status.setText("Fetching README from GitHub…")
        self._worker = _ReadmeFetchWorker(pack_id)
        self._worker.succeeded.connect(self._on_fetched)
        self._worker.failed.connect(self._on_fetch_failed)
        self._worker.start()

    def _on_fetched(self, content: str) -> None:
        self._text.setMarkdown(content)
        self._status.hide()

    def _on_fetch_failed(self, error: str) -> None:
        self._status.setText(f"Could not fetch README: {error}")


class _FetchWorker(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            releases = dp.fetch_pack_releases()
            self.succeeded.emit(releases)
        except Exception as exc:
            self.failed.emit(str(exc))


class _DownloadInstallWorker(QThread):
    progress = Signal(int, int)
    log = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, release: dict) -> None:
        super().__init__()
        self._release = release

    def run(self) -> None:
        try:
            asset = dp.find_zip_asset(self._release)
            if not asset:
                raise RuntimeError("No zip asset in this release")
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                zip_path = dp.download_zip(
                    asset, tmp,
                    on_progress=lambda done, total: self.progress.emit(done, total),
                    on_log=lambda msg: self.log.emit(msg),
                )
                installed = self._install_zip(zip_path)
                self.succeeded.emit(str(installed))
        except Exception as exc:
            self.failed.emit(str(exc))

    def _install_zip(self, zip_path: Path) -> Path:
        library = managed_decks_dir()
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
                raise RuntimeError("zip does not contain manifest.yaml")
            manifest_path = manifest_candidates[0]
            deck_root = manifest_path.parent
            if not (deck_root / "deckconfig").is_dir():
                raise RuntimeError("zip missing deckconfig/ folder next to manifest.yaml")
            try:
                import yaml
                with manifest_path.open("r", encoding="utf-8") as fp:
                    data = yaml.safe_load(fp) or {}
                deck_id = str(data.get("id") or "").strip()
            except Exception:
                deck_id = ""
            if not deck_id:
                raise RuntimeError("manifest.yaml missing required 'id'")
            target_dir = library / deck_id
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.move(str(deck_root), str(target_dir))
            self.log.emit(f"[packs] installed {deck_id} → {target_dir}")
            return target_dir
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)


class _PackCard(QFrame):
    """Card widget for a single pack with selectable versions."""
    install_requested = Signal(dict)
    uninstall_requested = Signal(str)  # pack_id

    def __init__(self, pack_id: str, releases: list[dict], installed_info: dict[str, dict], *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pack_id = pack_id
        self._releases = sorted(
            releases,
            key=lambda r: _version_sort_key(_pack_version_from_tag(r.get("tag_name", ""))),
            reverse=True,
        )
        self._release_by_version = {
            _pack_version_from_tag(release.get("tag_name", "")): release for release in self._releases
        }
        self._worker: _DownloadInstallWorker | None = None
        _info = installed_info.get(self._pack_id, {})
        self._installed_ver: str = _info.get("version", "")
        self._installed_summary: str = _info.get("summary", "")
        self._installed_aircraft: str = _info.get("aircraft", "")
        self._installed_icao: str = _info.get("icao", "")
        self._latest_stable_release = next((r for r in self._releases if not _is_prerelease(r)), None)
        self._latest_prerelease_release = next((r for r in self._releases if _is_prerelease(r)), None)
        _latest_stable_ver = _pack_version_from_tag(self._latest_stable_release.get("tag_name", "")) if self._latest_stable_release else ""
        _update_available = (
            bool(self._installed_ver)
            and bool(_latest_stable_ver)
            and _version_sort_key(_latest_stable_ver) > _version_sort_key(self._installed_ver)
        )
        self._selected_release = (
            self._latest_stable_release
            if _update_available
            else (self._release_by_version.get(self._installed_ver) or self._latest_stable_release or self._releases[0])
        )

        # Card style
        self.setObjectName("packcard")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        cl = QVBoxLayout(self)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(3)

        # ── Row 1: pack name ──
        self._name_lbl = QLabel(_pack_display_name(self._releases[0]))
        self._name_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #1e293b;")
        self._name_lbl.setWordWrap(True)
        cl.addWidget(self._name_lbl)

        # ── Row 1b: aircraft (shown when installed) ──
        self._aircraft_lbl = QLabel()
        self._aircraft_lbl.setStyleSheet("font-size: 10px; color: #475569;")
        self._aircraft_lbl.setWordWrap(True)
        self._aircraft_lbl.hide()
        cl.addWidget(self._aircraft_lbl)

        # ── Row 1c: summary (shown when installed) ──
        self._summary_lbl = QLabel()
        self._summary_lbl.setStyleSheet("font-size: 10px; color: #64748b;")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.hide()
        cl.addWidget(self._summary_lbl)

        # ── Row 2: version · date · size ──
        self._meta_lbl = QLabel()
        self._meta_lbl.setStyleSheet("font-size: 10px; color: #64748b;")
        cl.addWidget(self._meta_lbl)

        # ── Row 3: chips ──
        self._chips_row = QHBoxLayout()
        self._chips_row.setContentsMargins(0, 2, 0, 0)
        self._chips_row.setSpacing(4)
        cl.addLayout(self._chips_row)

        cl.addStretch(1)

        # ── Bottom: version picker + actions ──
        bottom = QVBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(3)

        version_row = QHBoxLayout()
        version_row.setSpacing(6)
        self._version_combo = QComboBox()
        self._version_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._version_combo.setStyleSheet(
            "QComboBox { padding: 3px 8px; border-radius: 5px; font-size: 11px; min-height: 0;"
            " background: #ffffff; color: #1e293b; border: 1px solid #cbd5e1; }"
            "QComboBox::drop-down { border: none; width: 18px; }"
            "QComboBox QAbstractItemView { background: #ffffff; color: #1e293b;"
            " selection-background-color: #dbeafe; selection-color: #1e3a8a;"
            " border: 1px solid #cbd5e1; outline: 0; }"
        )
        latest_stable = _pack_version_from_tag(self._latest_stable_release.get("tag_name", "")) if self._latest_stable_release else ""
        latest_prerelease = _pack_version_from_tag(self._latest_prerelease_release.get("tag_name", "")) if self._latest_prerelease_release else ""
        for release in self._releases:
            self._version_combo.addItem(
                _release_display_label(release, latest_stable=latest_stable, latest_prerelease=latest_prerelease),
                release,
            )
        initial_idx = next((idx for idx, release in enumerate(self._releases) if release is self._selected_release), 0)
        self._version_combo.setCurrentIndex(initial_idx)
        self._version_combo.currentIndexChanged.connect(self._on_version_changed)
        version_row.addWidget(self._version_combo, 1)
        bottom.addLayout(version_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self._btn = QPushButton("Install")
        self._btn.setStyleSheet(_BTN_SS)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._on_install)
        btn_row.addWidget(self._btn)

        self._uninstall_btn = QPushButton("Uninstall")
        self._uninstall_btn.setStyleSheet(
            "QPushButton { padding: 3px 10px; border-radius: 5px; font-size: 11px; min-height: 0;"
            " color: #b91c1c; border: 1px solid #fecaca; background: #fff; }"
            "QPushButton:hover { background: #fef2f2; }"
        )
        self._uninstall_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._uninstall_btn.clicked.connect(self._on_uninstall)
        btn_row.addWidget(self._uninstall_btn)

        self._readme_btn = QPushButton("README")
        self._readme_btn.setStyleSheet(
            "QPushButton { padding: 3px 10px; border-radius: 5px; font-size: 11px; min-height: 0;"
            " color: #0369a1; border: 1px solid #bae6fd; background: #fff; }"
            "QPushButton:hover { background: #f0f9ff; }"
        )
        self._readme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._readme_btn.clicked.connect(self._on_readme)
        btn_row.addWidget(self._readme_btn)

        btn_row.addStretch()
        bottom.addLayout(btn_row)

        # Progress bar (hidden)
        self._progress_row = QWidget()
        self._progress_row.hide()
        pl = QHBoxLayout(self._progress_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { border-radius: 2px; background: #e5e7eb; border: none; }"
            "QProgressBar::chunk { background: #2563eb; border-radius: 2px; }"
        )
        pl.addWidget(self._progress, 1)
        self._progress_label = QLabel()
        self._progress_label.setStyleSheet("color: #6b7280; font-size: 9px;")
        pl.addWidget(self._progress_label)
        bottom.addWidget(self._progress_row)

        # Error label (hidden)
        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #b91c1c; font-size: 9px;")
        self._error_label.hide()
        bottom.addWidget(self._error_label)

        cl.addLayout(bottom)

        # ── Release notes (collapsed, only if meaningful) ───────
        self._notes_visible = False
        self._notes_toggle = QPushButton("▶ Release notes")
        self._notes_toggle.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #6b7280; "
            "font-size: 10px; text-align: left; padding: 2px 0 0 0; min-height: 0; }"
            "QPushButton:hover { color: #374151; }"
        )
        self._notes_toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._notes_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._notes_toggle.clicked.connect(self._toggle_notes)
        self._notes_label = QLabel()
        self._notes_label.setWordWrap(True)
        self._notes_label.setStyleSheet(
            "color: #6b7280; font-size: 10px; padding: 2px 0 0 10px; border: none; background: transparent;"
        )
        self._notes_label.hide()
        cl.addWidget(self._notes_toggle)
        cl.addWidget(self._notes_label)

        self._refresh_card()

    def _set_card_style(self, *, selected_installed: bool) -> None:
        bg = "#f0fdf4" if selected_installed else "#f8fafc"
        border = "#bbf7d0" if selected_installed else "#e2e8f0"
        self.setStyleSheet(f"QFrame#packcard {{ background: {bg}; border: 2px solid {border}; border-radius: 8px; }}")

    def _selected_version(self) -> str:
        return _pack_version_from_tag(self._selected_release.get("tag_name", ""))

    def _current_release_has_asset(self) -> bool:
        return dp.find_zip_asset(self._selected_release) is not None

    def _set_notes_from_release(self) -> None:
        body = (self._selected_release.get("body") or "").strip()
        has_notes = _has_meaningful_notes(body)
        self._notes_toggle.setVisible(has_notes)
        self._notes_label.setVisible(has_notes and self._notes_visible)
        if has_notes:
            display_body = re.sub(
                r"\n?\s*\*{0,2}Full Changelog\*{0,2}:\s*https?://\S+\s*$", "", body, flags=re.IGNORECASE
            ).strip()
            self._notes_label.setText(display_body[:2000])
            self._notes_toggle.setText("▼ Release notes" if self._notes_visible else "▶ Release notes")
        else:
            self._notes_visible = False
            self._notes_label.setText("")

    def _refresh_card(self) -> None:
        selected_version = self._selected_version()
        selected_installed = bool(self._installed_ver) and self._installed_ver == selected_version
        latest_stable = _pack_version_from_tag(self._latest_stable_release.get("tag_name", "")) if self._latest_stable_release else ""
        latest_prerelease = _pack_version_from_tag(self._latest_prerelease_release.get("tag_name", "")) if self._latest_prerelease_release else ""

        if self._installed_aircraft:
            self._aircraft_lbl.setText(self._installed_aircraft)
            self._aircraft_lbl.show()
        else:
            self._aircraft_lbl.hide()

        if self._installed_summary:
            self._summary_lbl.setText(self._installed_summary)
            self._summary_lbl.show()
        else:
            self._summary_lbl.hide()

        meta_parts: list[str] = []
        published = self._selected_release.get("published_at", "")
        if published:
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                meta_parts.append(dt.astimezone(timezone.utc).strftime("%d %b %Y"))
            except ValueError:
                pass
        asset = dp.find_zip_asset(self._selected_release)
        if asset and asset.get("size"):
            meta_parts.append(_format_size(asset["size"]))
        self._meta_lbl.setText(" · ".join(meta_parts))
        self._meta_lbl.setVisible(bool(meta_parts))

        self._set_card_style(selected_installed=selected_installed)

        while self._chips_row.count():
            item = self._chips_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        def _add_chip(text: str, style: str) -> None:
            chip = QLabel(text)
            chip.setStyleSheet(style)
            self._chips_row.addWidget(chip)

        if self._installed_ver:
            if latest_stable and self._installed_ver == latest_stable:
                _add_chip(
                    f"v{self._installed_ver}  —  up to date",
                    "font-size: 9px; font-weight: 600; color: #15803d;"
                    " background: #dcfce7; border-radius: 4px; padding: 1px 5px;",
                )
            else:
                _add_chip(
                    f"v{self._installed_ver} installed",
                    "font-size: 9px; font-weight: 600; color: #15803d;"
                    " background: #dcfce7; border-radius: 4px; padding: 1px 5px;",
                )
                if latest_stable and _version_sort_key(latest_stable) > _version_sort_key(self._installed_ver):
                    _add_chip(
                        f"v{latest_stable} available",
                        "font-size: 9px; font-weight: 600; color: #1d4ed8;"
                        " background: #dbeafe; border-radius: 4px; padding: 1px 5px;",
                    )
        self._chips_row.addStretch(1)

        has_asset = self._current_release_has_asset()
        self._btn.setVisible(has_asset and not selected_installed)
        if has_asset and not selected_installed:
            if self._installed_ver:
                selected_key = _version_sort_key(selected_version)
                installed_key = _version_sort_key(self._installed_ver)
                if selected_key > installed_key:
                    self._btn.setText("Update")
                elif selected_key < installed_key:
                    self._btn.setText("Downgrade")
                else:
                    self._btn.setText("Reinstall")
            else:
                self._btn.setText("Install")
            self._btn.setEnabled(True)
        self._uninstall_btn.setVisible(bool(self._installed_ver))
        self._set_notes_from_release()

    def _on_version_changed(self, index: int) -> None:
        release = self._version_combo.itemData(index)
        if isinstance(release, dict):
            self._selected_release = release
            self._notes_visible = False
            self._refresh_card()

    def _toggle_notes(self) -> None:
        self._notes_visible = not self._notes_visible
        self._notes_label.setVisible(self._notes_visible)
        self._notes_toggle.setText("▼ Release notes" if self._notes_visible else "▶ Release notes")

    def _on_install(self) -> None:
        self._btn.setEnabled(False)
        self._btn.setText("Installing…")
        self._progress.setValue(0)
        self._progress_row.show()
        self._error_label.hide()

        self._worker = _DownloadInstallWorker(self._selected_release)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            pct = int(done * 100 / total)
            self._progress.setValue(pct)
            self._progress_label.setText(f"{_format_size(done)} / {_format_size(total)}")
        else:
            self._progress_label.setText(_format_size(done))

    def _on_readme(self) -> None:
        library = managed_decks_dir()
        pack_dir = library / self._pack_id if self._installed_ver else None
        dlg = _ReadmeDialog(self._pack_id, pack_dir, parent=self)
        dlg.exec()

    def _on_success(self, installed_path: str) -> None:
        self._progress_row.hide()
        self._installed_ver = self._selected_version()
        # Re-read manifest to pick up summary/aircraft/icao
        info = _installed_pack_info().get(self._pack_id, {})
        self._installed_summary = info.get("summary", "")
        self._installed_aircraft = info.get("aircraft", "")
        self._installed_icao = info.get("icao", "")
        self._refresh_card()
        self.install_requested.emit(self._selected_release)

    def _on_failure(self, error: str) -> None:
        self._progress_row.hide()
        self._error_label.setText(error)
        self._error_label.show()
        self._btn.setText("Retry")
        self._btn.setEnabled(True)

    def _on_uninstall(self) -> None:
        import shutil
        library = managed_decks_dir()
        target = library / self._pack_id
        if target.is_dir():
            shutil.rmtree(target)
        self._installed_ver = ""
        self._installed_summary = ""
        self._installed_aircraft = ""
        self._installed_icao = ""
        self._refresh_card()
        self.uninstall_requested.emit(self._pack_id)


class DeckPacksTab(QWidget):
    """Tab for browsing and installing deck configuration packs from GitHub."""
    installed = Signal(str)  # tag
    uninstalled = Signal(str)  # pack_id
    log_line = Signal(str)

    _GRID_COLS = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._releases: list[dict] = []
        self._cards: list[_PackCard] = []
        self._fetched = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(8)

        # Top bar
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 4, 0)
        self._fetch_status = QLabel("Loading deck packs…")
        self._fetch_status.setStyleSheet("color: #9ca3af; font-size: 12px;")
        top.addWidget(self._fetch_status)
        top.addStretch()
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setStyleSheet(_BTN_SS)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        root.addLayout(top)

        # Scrollable grid
        self._grid_container = QWidget()
        self._grid_container.setStyleSheet("background: transparent;")
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._grid_layout.setSpacing(10)
        for c in range(self._GRID_COLS):
            self._grid_layout.setColumnStretch(c, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; }"
        )
        scroll.setWidget(self._grid_container)
        root.addWidget(scroll, 1)

        self._summary = QLabel()
        self._summary.setStyleSheet("font-size: 12px; color: #64748b;")
        root.addWidget(self._summary)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._fetched:
            self.refresh()

    def refresh(self) -> None:
        self._fetched = True
        self._refresh_btn.setEnabled(False)
        self._fetch_status.setText("Fetching deck packs…")
        self._fetch_status.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self._clear_grid()

        worker = _FetchWorker()
        worker.succeeded.connect(self._on_fetch_done)
        worker.failed.connect(self._on_fetch_error)
        worker.setParent(self)
        self._fetch_worker = worker
        worker.start()

    def _on_fetch_done(self, releases: list) -> None:
        self._refresh_btn.setEnabled(True)
        installed_info = _installed_pack_info()

        releases = [r for r in releases if r.get("tag_name", "").startswith("pack-")]
        releases.sort(key=_pack_sort_key)
        self._releases = releases

        grouped_releases: dict[str, list[dict]] = {}
        for release in releases:
            pid = _pack_id_from_tag(release["tag_name"])
            if not pid:
                continue
            grouped_releases.setdefault(pid, []).append(release)

        self._clear_grid()
        grid_row = 0
        display_groups = sorted(
            grouped_releases.items(),
            key=lambda item: _pack_display_name(item[1][0]).lower(),
        )
        for idx, (pack_id, pack_releases) in enumerate(display_groups):
            col = idx % self._GRID_COLS
            if col == 0 and idx > 0:
                grid_row += 1
            card = _PackCard(pack_id, pack_releases, installed_info)
            card.install_requested.connect(self._on_installed)
            card.install_requested.connect(lambda r: self.log_line.emit(f"[packs] installed {r['tag_name']}"))
            card.uninstall_requested.connect(self._on_uninstalled)
            self._cards.append(card)
            self._grid_layout.addWidget(card, grid_row, col)
        grid_row += 1
        self._grid_layout.setRowStretch(grid_row, 1)

        if not display_groups:
            self._fetch_status.setText("No packs available.")
            self._summary.setText("")
        else:
            self._fetch_status.setText("")
            installed_count = sum(1 for pack_id in grouped_releases if installed_info.get(pack_id, {}).get("version"))
            self._summary.setText(f"{len(display_groups)} pack(s) · {installed_count} installed")

    def _on_fetch_error(self, error: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._fetch_status.setText(f"Failed to fetch packs: {error}")
        self._fetch_status.setStyleSheet("color: #b91c1c; font-size: 12px;")

    def _on_installed(self, release: dict) -> None:
        self.installed.emit(release["tag_name"])

    def _on_uninstalled(self, pack_id: str) -> None:
        self.log_line.emit(f"[packs] uninstalled {pack_id}")
        self.uninstalled.emit(pack_id)

    def _clear_grid(self) -> None:
        self._cards.clear()
        while self._grid_layout.count():
            child = self._grid_layout.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()
