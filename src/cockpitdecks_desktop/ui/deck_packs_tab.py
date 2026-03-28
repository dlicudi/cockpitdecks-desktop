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
    QGridLayout,
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
    "QPushButton { padding: 3px 10px; border-radius: 5px; font-size: 11px; min-height: 0; }"
    "QPushButton:disabled { color: #a1a6b0; background: #f4f5f7; border-color: #dde0e6; }"
)


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
    try:
        ver_tuple = tuple(int(x) for x in ver_str.split("."))
    except ValueError:
        ver_tuple = (0,)
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
    m = re.match(r"^pack-(.+)-v[\d.]+", tag)
    return m.group(1) if m else ""


def _pack_version_from_tag(tag: str) -> str:
    m = re.match(r"^pack-.+-v([\d.]+)$", tag)
    return m.group(1) if m else ""


def _pack_display_name(release: dict) -> str:
    """Extract a clean display name from the release name or tag."""
    name = release.get("name", "")
    if name:
        # Strip trailing version like "v1.0.1" or "Pack v1.0.1"
        name = re.sub(r"\s+v?[\d.]+\s*$", "", name).strip()
        # Strip leading "Pack" if redundant
        name = re.sub(r"\s+Pack$", "", name).strip()
        if name:
            return name
    pack_id = _pack_id_from_tag(release.get("tag_name", ""))
    return pack_id.replace("-", " ").title() if pack_id else release.get("tag_name", "Unknown")


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
    """Card widget for a single pack release — matches installed deck card style."""
    install_requested = Signal(dict)
    uninstall_requested = Signal(str)  # pack_id

    def __init__(self, release: dict, installed_versions: dict[str, str], *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._release = release
        self._worker: _DownloadInstallWorker | None = None

        tag = release["tag_name"]
        has_asset = dp.find_zip_asset(release) is not None
        self._pack_id = _pack_id_from_tag(tag)
        release_version = _pack_version_from_tag(tag)
        installed_ver = installed_versions.get(self._pack_id, "")
        self._is_installed = bool(self._pack_id) and installed_ver == release_version and bool(release_version)
        # Compare version tuples to distinguish update vs older version.
        self._is_newer = False
        self._is_older = False
        if bool(self._pack_id) and bool(installed_ver) and installed_ver != release_version:
            try:
                rel_tuple = tuple(int(x) for x in release_version.split("."))
                inst_tuple = tuple(int(x) for x in installed_ver.split("."))
                self._is_newer = rel_tuple > inst_tuple
                self._is_older = rel_tuple < inst_tuple
            except ValueError:
                self._is_newer = True  # fallback: assume newer

        # Card style
        bg = "#f0fdf4" if self._is_installed else "#f8fafc"
        border = "#bbf7d0" if self._is_installed else "#e2e8f0"
        self.setObjectName("packcard")
        self.setStyleSheet(f"QFrame#packcard {{ background: {bg}; border: 2px solid {border}; border-radius: 8px; }}")
        self.setFixedHeight(130)

        cl = QVBoxLayout(self)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(3)

        # ── Row 1: pack name ──
        name_lbl = QLabel(_pack_display_name(release))
        name_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #1e293b;")
        name_lbl.setWordWrap(True)
        cl.addWidget(name_lbl)

        # ── Row 2: version · date · size ──
        meta_parts: list[str] = []
        if release_version:
            meta_parts.append(f"v{release_version}")
        published = release.get("published_at", "")
        if published:
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                meta_parts.append(dt.astimezone(timezone.utc).strftime("%d %b %Y"))
            except ValueError:
                pass
        asset = dp.find_zip_asset(release)
        if asset and asset.get("size"):
            meta_parts.append(_format_size(asset["size"]))
        if meta_parts:
            meta_lbl = QLabel(" · ".join(meta_parts))
            meta_lbl.setStyleSheet("font-size: 10px; color: #64748b;")
            cl.addWidget(meta_lbl)

        # ── Row 3: chips ──
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 2, 0, 0)
        chips.setSpacing(4)

        if self._is_installed:
            chip = QLabel("Installed")
            chip.setStyleSheet(
                "font-size: 9px; font-weight: 600; color: #15803d;"
                " background: #dcfce7; border-radius: 4px; padding: 1px 5px;"
            )
            chips.addWidget(chip)
        elif self._is_newer:
            chip = QLabel("Update available")
            chip.setStyleSheet(
                "font-size: 9px; font-weight: 600; color: #1d4ed8;"
                " background: #dbeafe; border-radius: 4px; padding: 1px 5px;"
            )
            chips.addWidget(chip)
        elif self._is_older:
            chip = QLabel(f"Older · v{installed_ver} installed")
            chip.setStyleSheet(
                "font-size: 9px; font-weight: 500; color: #6b7280;"
                " background: #f1f5f9; border-radius: 4px; padding: 1px 5px;"
            )
            chips.addWidget(chip)

        chips.addStretch(1)
        cl.addLayout(chips)

        cl.addStretch(1)

        # ── Bottom: action button + progress ──
        bottom = QVBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(3)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        if has_asset and not self._is_installed:
            label = "Update" if self._is_newer else "Install"
            self._btn = QPushButton(label)
            self._btn.setStyleSheet(_BTN_SS)
            self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn.clicked.connect(self._on_install)
            btn_row.addWidget(self._btn)

        if self._is_installed:
            self._uninstall_btn = QPushButton("Uninstall")
            self._uninstall_btn.setStyleSheet(
                "QPushButton { padding: 3px 10px; border-radius: 5px; font-size: 11px; min-height: 0;"
                " color: #b91c1c; border: 1px solid #fecaca; background: #fff; }"
                "QPushButton:hover { background: #fef2f2; }"
            )
            self._uninstall_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._uninstall_btn.clicked.connect(self._on_uninstall)
            btn_row.addWidget(self._uninstall_btn)

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

    def _on_install(self) -> None:
        self._btn.setEnabled(False)
        self._btn.setText("Installing…")
        self._progress.setValue(0)
        self._progress_row.show()
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
        self.setStyleSheet("QFrame#packcard { background: #f0fdf4; border: 2px solid #bbf7d0; border-radius: 8px; }")
        if hasattr(self, "_btn"):
            self._btn.hide()
        self.install_requested.emit(self._release)

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
        self.uninstall_requested.emit(self._pack_id)


class DeckPacksTab(QWidget):
    """Tab for browsing and installing deck configuration packs from GitHub."""
    installed = Signal(str)  # tag
    uninstalled = Signal(str)  # pack_id
    log_line = Signal(str)

    _GRID_COLS = 4

    def __init__(self, show_all_versions: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show_all_versions = show_all_versions
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
        installed_versions = _installed_pack_versions()

        releases = [r for r in releases if r.get("tag_name", "").startswith("pack-")]
        releases.sort(key=_pack_sort_key)
        self._releases = releases

        # Filter: Available = latest per pack (skip installed), Archive = older versions only.
        if self._show_all_versions:
            # Skip the latest version of each pack — those belong in Available.
            seen_latest: set[str] = set()
            display_releases = []
            for r in releases:
                pid = _pack_id_from_tag(r["tag_name"])
                if pid and pid not in seen_latest:
                    seen_latest.add(pid)
                    continue  # skip latest
                display_releases.append(r)
        else:
            # Available: show latest per pack, skip packs where latest is already installed.
            seen_packs: set[str] = set()
            display_releases = []
            for r in releases:
                pid = _pack_id_from_tag(r["tag_name"])
                if pid and pid not in seen_packs:
                    seen_packs.add(pid)
                    ver = _pack_version_from_tag(r["tag_name"])
                    installed_ver = installed_versions.get(pid, "")
                    if installed_ver == ver and ver:
                        continue  # already installed, skip
                    display_releases.append(r)
                elif not pid:
                    display_releases.append(r)

        self._clear_grid()
        grid_row = 0
        for idx, release in enumerate(display_releases):
            col = idx % self._GRID_COLS
            if col == 0 and idx > 0:
                grid_row += 1
            card = _PackCard(release, installed_versions)
            card.install_requested.connect(self._on_installed)
            card.install_requested.connect(lambda r: self.log_line.emit(f"[packs] installed {r['name']}"))
            card.uninstall_requested.connect(self._on_uninstalled)
            self._cards.append(card)
            self._grid_layout.addWidget(card, grid_row, col)
        grid_row += 1
        self._grid_layout.setRowStretch(grid_row, 1)

        if not display_releases:
            if self._show_all_versions:
                self._fetch_status.setText("No older versions available.")
            else:
                self._fetch_status.setText("All packs are up to date.")
            self._summary.setText("")
        else:
            self._fetch_status.setText("")
            if self._show_all_versions:
                n_installed = sum(
                    1 for r in display_releases
                    if _pack_id_from_tag(r["tag_name"]) in installed_versions
                    and installed_versions[_pack_id_from_tag(r["tag_name"])] == _pack_version_from_tag(r["tag_name"])
                )
                self._summary.setText(f"{len(display_releases)} release(s) · {n_installed} installed")
            else:
                self._summary.setText(f"{len(display_releases)} pack(s) available")

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
