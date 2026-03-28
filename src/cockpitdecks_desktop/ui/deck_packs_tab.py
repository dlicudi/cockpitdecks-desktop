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
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_desktop.services import deck_packs as dp
from cockpitdecks_desktop.services.desktop_settings import managed_decks_dir

_BTN_SS = (
    "QPushButton { padding: 4px 14px; border-radius: 5px; font-size: 12px; min-height: 14px; }"
    "QPushButton:disabled { color: #a1a6b0; background: #f4f5f7; border-color: #dde0e6; }"
)

_BADGE_SS = "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 500;"


def _pack_sort_key(release: dict) -> tuple:
    """Sort packs alphabetically by name, then newest version first."""
    tag = release.get("tag_name", "")
    name = release.get("name", tag)
    # Extract version from tag: "pack-cirrus-sr22-v1.0.1" → ("cirrus-sr22", (1, 0, 1))
    m = re.match(r"^pack-(.+)-v(.+)$", tag)
    if m:
        pack_name = m.group(1)
        ver_str = m.group(2)
    else:
        pack_name = name.lower()
        ver_str = "0"
    try:
        ver_tuple = tuple(int(x) for x in ver_str.split("."))
    except ValueError:
        ver_tuple = (0,)
    # Sort by name ascending, then version descending (negate)
    return (pack_name, tuple(-x for x in ver_tuple))


def _format_size(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _installed_pack_versions() -> dict[str, str]:
    """Return {pack_id: version} for packs in the managed decks library."""
    library = managed_decks_dir()
    if not library.is_dir():
        return {}
    result: dict[str, str] = {}
    for d in library.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        manifest = d / "manifest.yaml"
        version = ""
        if manifest.is_file():
            try:
                for line in manifest.read_text(encoding="utf-8").splitlines():
                    if line.startswith("version:"):
                        version = line.split(":", 1)[1].strip().strip("'\"")
                        break
            except OSError:
                pass
        result[d.name] = version
    return result


def _pack_id_from_tag(tag: str) -> str:
    """Guess the pack ID from a release tag like 'pack-cirrus-sr22-v1.0.1' → 'cirrus-sr22'."""
    m = re.match(r"^pack-(.+)-v[\d.]+", tag)
    return m.group(1) if m else ""


def _pack_version_from_tag(tag: str) -> str:
    """Extract version from tag like 'pack-cirrus-sr22-v1.0.1' → '1.0.1'."""
    m = re.match(r"^pack-.+-v([\d.]+)$", tag)
    return m.group(1) if m else ""


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
    """Download a zip asset and extract it into the managed decks library."""
    progress = Signal(int, int)
    log = Signal(str)
    succeeded = Signal(str)  # installed path
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
                    asset,
                    tmp,
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
            # Read manifest ID
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


class _PackRow(QFrame):
    install_requested = Signal(dict)

    def __init__(
        self,
        release: dict,
        installed_versions: dict[str, str],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._release = release
        self._worker: _DownloadInstallWorker | None = None

        tag = release["tag_name"]
        name = release.get("name", tag)
        published = release.get("published_at", "")
        has_asset = dp.find_zip_asset(release) is not None
        self._pack_id = _pack_id_from_tag(tag)
        release_version = _pack_version_from_tag(tag)
        installed_ver = installed_versions.get(self._pack_id, "")
        self._is_installed = bool(self._pack_id) and installed_ver == release_version and bool(release_version)
        self._has_update = bool(self._pack_id) and bool(installed_ver) and installed_ver != release_version

        self.setObjectName("PackRow")
        self._apply_frame_style()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(4)

        # ── Main row ─────────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(10)

        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: 600; font-size: 13px; border: none; background: transparent;")
        row.addWidget(name_label)

        tag_label = QLabel(tag)
        tag_label.setStyleSheet("color: #9ca3af; font-size: 11px; border: none; background: transparent;")
        row.addWidget(tag_label)

        if published:
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                date_str = dt.astimezone(timezone.utc).strftime("%d %b %Y")
            except ValueError:
                date_str = published[:10]
            date_label = QLabel(date_str)
            date_label.setStyleSheet("color: #9ca3af; font-size: 11px; border: none; background: transparent;")
            row.addWidget(date_label)

        # Asset size
        asset = dp.find_zip_asset(release)
        if asset and asset.get("size"):
            size_label = QLabel(_format_size(asset["size"]))
            size_label.setStyleSheet("color: #9ca3af; font-size: 11px; border: none; background: transparent;")
            row.addWidget(size_label)

        row.addStretch()

        # Right side: badge or button
        if self._is_installed:
            self._right_widget = QLabel("Installed")
            self._right_widget.setStyleSheet(f"color: #15803d; background: #dcfce7; border: 1px solid #bbf7d0; {_BADGE_SS}")
            row.addWidget(self._right_widget)
        elif has_asset:
            self._btn = QPushButton("Install")
            self._btn.setStyleSheet(_BTN_SS)
            self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn.clicked.connect(self._on_install)
            self._right_widget = self._btn
            row.addWidget(self._btn)
        else:
            no_asset = QLabel("No zip")
            no_asset.setStyleSheet("color: #9ca3af; background: transparent; border: none; font-size: 11px;")
            self._right_widget = no_asset
            row.addWidget(no_asset)

        layout.addLayout(row)

        # ── Progress area (hidden) ───────────────────────────────
        self._progress_row = QWidget()
        self._progress_row.hide()
        pl = QHBoxLayout(self._progress_row)
        pl.setContentsMargins(0, 2, 0, 0)
        pl.setSpacing(8)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { border-radius: 2px; background: #e5e7eb; border: none; }"
            "QProgressBar::chunk { background: #2563eb; border-radius: 2px; }"
        )
        pl.addWidget(self._progress, 1)

        self._progress_label = QLabel()
        self._progress_label.setStyleSheet("color: #6b7280; font-size: 11px; border: none; background: transparent;")
        self._progress_label.setMinimumWidth(80)
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pl.addWidget(self._progress_label)

        layout.addWidget(self._progress_row)

        # ── Error label (hidden) ─────────────────────────────────
        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #b91c1c; font-size: 11px; border: none; background: transparent;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        # ── Release notes (collapsed) ────────────────────────────
        body = release.get("body", "").strip()
        if body:
            self._notes_visible = False
            self._notes_toggle = QPushButton("▶ Release notes")
            self._notes_toggle.setStyleSheet(
                "QPushButton { background: transparent; border: none; color: #6b7280; "
                "font-size: 11px; text-align: left; padding: 0; min-height: 0; }"
                "QPushButton:hover { color: #374151; }"
            )
            self._notes_toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._notes_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            self._notes_toggle.clicked.connect(self._toggle_notes)

            # Strip trailing changelog URL
            display_body = re.sub(
                r"\n?\s*\*{0,2}Full Changelog\*{0,2}:\s*https?://\S+\s*$", "", body, flags=re.IGNORECASE
            ).strip()
            self._notes_label = QLabel(display_body[:2000])
            self._notes_label.setWordWrap(True)
            self._notes_label.setStyleSheet(
                "color: #6b7280; font-size: 11px; padding: 4px 0 0 12px; border: none; background: transparent;"
            )
            self._notes_label.hide()

            layout.addWidget(self._notes_toggle)
            layout.addWidget(self._notes_label)

    def _apply_frame_style(self) -> None:
        if self._is_installed:
            self.setStyleSheet(
                "#PackRow { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; }"
            )
        else:
            self.setStyleSheet(
                "#PackRow { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 6px; }"
            )

    def _toggle_notes(self) -> None:
        self._notes_visible = not self._notes_visible
        self._notes_label.setVisible(self._notes_visible)
        self._notes_toggle.setText("▼ Release notes" if self._notes_visible else "▶ Release notes")

    def _on_install(self) -> None:
        self._btn.setEnabled(False)
        self._btn.setText("Installing…")
        self._progress.setValue(0)
        self._progress_row.show()
        self._progress_label.setText("0%")
        self._error_label.hide()

        self._worker = _DownloadInstallWorker(self._release)
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

    def _on_success(self, installed_path: str) -> None:
        self._progress_row.hide()
        self._is_installed = True
        self._apply_frame_style()
        self._btn.hide()
        # Show/update badge
        if isinstance(self._right_widget, QLabel):
            self._right_widget.setText("Installed")
            self._right_widget.setStyleSheet(f"color: #15803d; background: #dcfce7; border: 1px solid #bbf7d0; {_BADGE_SS}")
        else:
            badge = QLabel("Installed")
            badge.setStyleSheet(f"color: #15803d; background: #dcfce7; border: 1px solid #bbf7d0; {_BADGE_SS}")
            header_layout = self.layout().itemAt(0).layout()
            header_layout.insertWidget(header_layout.count() - 1, badge)
            self._right_widget = badge
        self.install_requested.emit(self._release)

    def _on_failure(self, error: str) -> None:
        self._progress_row.hide()
        self._error_label.setText(error)
        self._error_label.show()
        self._btn.setText("Retry")
        self._btn.setEnabled(True)


class DeckPacksTab(QWidget):
    """Tab for browsing and installing deck configuration packs from GitHub."""
    installed = Signal(str)  # tag
    log_line = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._releases: list[dict] = []
        self._cards: list[_PackRow] = []
        self._fetched = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # Top bar
        top = QHBoxLayout()
        self._info_label = QLabel("Browse deck configuration packs from cockpitdecks-configs.")
        self._info_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        top.addWidget(self._info_label)
        top.addStretch()
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setStyleSheet(_BTN_SS)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        root.addLayout(top)

        # Status label
        self._fetch_status = QLabel("Loading deck packs…")
        self._fetch_status.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self._fetch_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._fetch_status)

        # Scrollable list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_widget)
        root.addWidget(scroll, 1)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._fetched:
            self.refresh()

    def refresh(self) -> None:
        self._fetched = True
        self._refresh_btn.setEnabled(False)
        self._fetch_status.setText("Fetching deck packs…")
        self._fetch_status.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self._fetch_status.show()
        self._clear_list()

        worker = _FetchWorker()
        worker.succeeded.connect(self._on_fetch_done)
        worker.failed.connect(self._on_fetch_error)
        worker.setParent(self)
        self._fetch_worker = worker
        worker.start()

    def _on_fetch_done(self, releases: list) -> None:
        self._refresh_btn.setEnabled(True)
        installed_versions = _installed_pack_versions()

        # Only show pack-* releases (ignore repo-level tags like v1.0.0)
        releases = [r for r in releases if r.get("tag_name", "").startswith("pack-")]
        releases.sort(key=_pack_sort_key)
        self._releases = releases

        self._clear_list()
        for release in releases:
            card = _PackRow(release, installed_versions)
            card.install_requested.connect(self._on_installed)
            card.install_requested.connect(lambda r: self.log_line.emit(f"[packs] installed {r['name']}"))
            self._cards.append(card)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

        if not releases:
            self._fetch_status.setText("No deck packs published yet.")
        else:
            n_installed = sum(
                1 for r in releases
                if _pack_id_from_tag(r["tag_name"]) in installed_versions
                and installed_versions[_pack_id_from_tag(r["tag_name"])] == _pack_version_from_tag(r["tag_name"])
            )
            unique_packs = len({_pack_id_from_tag(r["tag_name"]) for r in releases})
            self._fetch_status.setText(f"{unique_packs} pack(s), {len(releases)} release(s) · {n_installed} installed")

    def _on_fetch_error(self, error: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._fetch_status.setText(f"Failed to fetch packs: {error}")
        self._fetch_status.setStyleSheet("color: #b91c1c; font-size: 12px;")

    def _on_installed(self, release: dict) -> None:
        tag = release["tag_name"]
        self.installed.emit(tag)

    def _clear_list(self) -> None:
        self._cards.clear()
        while self._list_layout.count() > 1:  # keep trailing stretch
            item = self._list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
