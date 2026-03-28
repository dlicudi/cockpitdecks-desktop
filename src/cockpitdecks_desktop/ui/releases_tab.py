"""Releases tab — browse, download, and install cockpitdecks binary releases from GitHub."""

from __future__ import annotations

import re
from datetime import datetime, timezone

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

from cockpitdecks_desktop.services import github_releases as gh

# Compact button style — overrides global QSS padding/border for this context.
_BTN_SS = (
    "QPushButton { padding: 4px 14px; border-radius: 5px; font-size: 12px; min-height: 14px; }"
    "QPushButton:disabled { color: #a1a6b0; background: #f4f5f7; border-color: #dde0e6; }"
)

_BADGE_SS = "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 500;"

_CHANGELOG_RE = re.compile(
    r"^\s*\*{0,2}Full Changelog\*{0,2}:\s*https?://\S+\s*$", re.IGNORECASE
)


def _version_sort_key(release: dict) -> tuple:
    """Parse tag into a sortable tuple so e.g. beta.10 > beta.9 > beta.1."""
    tag = release.get("tag_name", "")
    tag = tag.removeprefix("v")
    parts = tag.split("-", 1)
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


def _has_meaningful_notes(body: str) -> bool:
    """Return True if the release body has more than just a changelog URL."""
    stripped = body.strip()
    if not stripped:
        return False
    if _CHANGELOG_RE.match(stripped):
        return False
    return True


def _format_size(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


class _DownloadWorker(QThread):
    progress = Signal(int, int)  # bytes_done, total
    log = Signal(str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, release: dict) -> None:
        super().__init__()
        self._release = release

    def run(self) -> None:
        try:
            gh.download_and_install(
                self._release,
                on_progress=lambda done, total: self.progress.emit(done, total),
                on_log=lambda msg: self.log.emit(msg),
            )
            self.succeeded.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _ReleaseRow(QFrame):
    install_requested = Signal(dict)  # emits the release dict

    def __init__(
        self,
        release: dict,
        installed_tag: str | None,
        *,
        is_latest: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._release = release
        self._worker: _DownloadWorker | None = None

        tag = release["tag_name"]
        published = release.get("published_at", "")
        self._is_installed = tag == installed_tag
        has_asset = gh.has_binary_asset(release)

        self.setObjectName("ReleaseRow")
        self._apply_frame_style()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(4)

        # ── Main row ────────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(10)

        tag_label = QLabel(tag)
        tag_label.setStyleSheet("font-weight: 600; font-size: 13px; border: none; background: transparent;")
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

        if is_latest and not self._is_installed:
            badge = QLabel("Latest")
            badge.setStyleSheet(f"color: #1d4ed8; background: #dbeafe; border: 1px solid #93c5fd; {_BADGE_SS}")
            row.addWidget(badge)

        row.addStretch()

        # Right side: badge or action button
        if self._is_installed:
            self._right_widget = QLabel("✓ Installed")
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
            no_bin = QLabel("No binary")
            no_bin.setStyleSheet(f"color: #9ca3af; background: transparent; border: none; font-size: 11px;")
            self._right_widget = no_bin
            row.addWidget(no_bin)

        layout.addLayout(row)

        # ── Progress area (hidden) ──────────────────────────────
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

        # ── Error label (hidden) ────────────────────────────────
        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #b91c1c; font-size: 11px; border: none; background: transparent;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        # ── Release notes (collapsed, only if meaningful) ───────
        body = release.get("body", "").strip()
        self._has_notes = _has_meaningful_notes(body)
        if self._has_notes:
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

            # Strip the trailing changelog link if the body also has other content.
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
                "#ReleaseRow { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; }"
            )
        else:
            self.setStyleSheet(
                "#ReleaseRow { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 6px; }"
            )

    def mark_not_installed(self) -> None:
        """Called by the parent tab when another release is installed."""
        if not self._is_installed:
            return
        self._is_installed = False
        self._apply_frame_style()
        if hasattr(self, "_right_widget") and isinstance(self._right_widget, QLabel):
            self._right_widget.setText("Previously installed")
            self._right_widget.setStyleSheet(f"color: #9ca3af; background: #f3f4f6; border: 1px solid #e5e7eb; {_BADGE_SS}")

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

        self._worker = _DownloadWorker(self._release)
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

    def _on_success(self) -> None:
        self._progress_row.hide()
        self._is_installed = True
        self._apply_frame_style()
        # Replace button with Installed badge
        self._btn.hide()
        badge = QLabel("✓ Installed")
        badge.setStyleSheet(f"color: #15803d; background: #dcfce7; border: 1px solid #bbf7d0; {_BADGE_SS}")
        header_layout = self.layout().itemAt(0).layout()
        header_layout.addWidget(badge)
        self._right_widget = badge
        self.install_requested.emit(self._release)

    def _on_failure(self, error: str) -> None:
        self._progress_row.hide()
        self._error_label.setText(error)
        self._error_label.show()
        self._btn.setText("Retry")
        self._btn.setEnabled(True)


class ReleasesTab(QWidget):
    # Emitted after a successful install so main_window can react
    installed = Signal(str)  # tag
    log_line = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._releases: list[dict] = []
        self._cards: list[_ReleaseRow] = []
        self._fetched = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # Top bar
        top = QHBoxLayout()
        self._installed_label = QLabel()
        self._installed_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        top.addWidget(self._installed_label)
        top.addStretch()
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setStyleSheet(_BTN_SS)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        root.addLayout(top)

        # Status / error label
        self._fetch_status = QLabel("Loading releases…")
        self._fetch_status.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self._fetch_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._fetch_status)

        # Scrollable release list
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

        self._update_installed_label()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._fetched:
            self.refresh()

    def refresh(self) -> None:
        self._fetched = True
        self._refresh_btn.setEnabled(False)
        self._fetch_status.setText("Fetching releases…")
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
        installed = gh.installed_version()
        self._update_installed_label()

        # Filter out old launcher-* releases and sort by version descending.
        releases = [r for r in releases if not r.get("tag_name", "").startswith("launcher-")]
        releases.sort(key=_version_sort_key, reverse=True)
        self._releases = releases

        self._clear_list()
        has_any_asset = False
        latest_tagged = False
        for release in releases:
            is_latest = gh.has_binary_asset(release) and not latest_tagged
            if is_latest:
                latest_tagged = True
                has_any_asset = True
            card = _ReleaseRow(release, installed, is_latest=is_latest)
            card.install_requested.connect(self._on_installed)
            card.install_requested.connect(lambda r: self.log_line.emit(f"[releases] installed {r['tag_name']}"))
            self._cards.append(card)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)
            if gh.has_binary_asset(release):
                has_any_asset = True

        if not releases:
            self._fetch_status.setText("No releases found.")
        elif not has_any_asset:
            self._fetch_status.setText(f"{len(releases)} releases — no macOS arm64 binaries published yet.")
        else:
            self._fetch_status.hide()

    def _on_fetch_error(self, error: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._fetch_status.setText(f"Failed to fetch releases: {error}")
        self._fetch_status.setStyleSheet("color: #b91c1c; font-size: 12px;")

    def _on_installed(self, release: dict) -> None:
        tag = release["tag_name"]
        # Clear "Installed" badge from all other cards
        for card in self._cards:
            if card._release["tag_name"] != tag:
                card.mark_not_installed()
        self._update_installed_label()
        self.installed.emit(tag)

    def _update_installed_label(self) -> None:
        ver = gh.installed_version()
        if ver:
            short_path = str(gh.installed_binary()).replace(str(gh.INSTALL_DIR.parent.parent), "~")
            self._installed_label.setText(f"Installed: {ver}  ·  {short_path}")
        else:
            self._installed_label.setText("No version installed — select a release below.")

    def _clear_list(self) -> None:
        self._cards.clear()
        while self._list_layout.count() > 1:  # keep the trailing stretch
            item = self._list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()


class _FetchWorker(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            releases = gh.fetch_releases()
            self.succeeded.emit(releases)
        except Exception as exc:
            self.failed.emit(str(exc))
