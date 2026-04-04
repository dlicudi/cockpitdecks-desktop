"""Releases tab — browse, download, and install cockpitdecks binary releases from GitHub."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
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


class _TextDialog(QDialog):
    """Simple dialog showing plain or markdown text."""

    def __init__(self, title: str, content: str, *, markdown: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        self.setStyleSheet(
            "QDialog { background: #ffffff; color: #1e293b; }"
            "QPushButton { padding: 4px 12px; border-radius: 6px; font-size: 12px;"
            " color: #1e293b; background: #ffffff; border: 1px solid #cbd5e1; }"
            "QPushButton:hover { background: #f8fafc; }"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet(
            "font-size: 12px; color: #1e293b; background: #ffffff;"
            " border: 1px solid #e2e8f0; border-radius: 6px;"
        )
        if markdown:
            self._text.setMarkdown(content)
        else:
            self._text.setPlainText(content)
        layout.addWidget(self._text, 1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)


class _DownloadWorker(QThread):
    progress = Signal(int, int)  # bytes_done, total
    log = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, release: dict) -> None:
        super().__init__()
        self._release = release

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            gh.download_and_install(
                self._release,
                on_progress=lambda done, total: self.progress.emit(done, total),
                on_log=lambda msg: self.log.emit(msg),
                should_cancel=self.isInterruptionRequested,
            )
            self.succeeded.emit(self._release["tag_name"])
        except gh.DownloadCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _ReleaseRow(QFrame):
    install_requested = Signal(dict)  # emits the release dict
    activated_requested = Signal(str)  # tag
    uninstall_requested = Signal(str)  # tag

    def __init__(
        self,
        release: dict,
        active_tag: str | None,
        installed_tags: set[str],
        *,
        is_latest: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._release = release
        self._worker: _DownloadWorker | None = None
        self._installing = False

        tag = release["tag_name"]
        published = release.get("published_at", "")
        self._is_active = tag == active_tag
        self._is_installed = tag in installed_tags
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

        if is_latest:
            badge = QLabel("Latest")
            badge.setStyleSheet(f"color: #1d4ed8; background: #dbeafe; border: 1px solid #93c5fd; {_BADGE_SS}")
            row.addWidget(badge)

        row.addStretch()

        self._actions = QHBoxLayout()
        self._actions.setSpacing(6)
        self._btn = QPushButton()
        self._btn.setStyleSheet(_BTN_SS)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._on_primary_action)
        self._actions.addWidget(self._btn)
        self._notes_btn = QPushButton("Release notes")
        self._notes_btn.setStyleSheet(
            "QPushButton { padding: 3px 10px; border-radius: 5px; font-size: 11px; min-height: 0;"
            " color: #0369a1; border: 1px solid #bae6fd; background: #fff; }"
            "QPushButton:hover { background: #f0f9ff; }"
        )
        self._notes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._notes_btn.clicked.connect(self._on_notes)
        self._actions.addWidget(self._notes_btn)
        self._uninstall_btn = QPushButton("Uninstall")
        self._uninstall_btn.setStyleSheet(
            "QPushButton { padding: 3px 10px; border-radius: 5px; font-size: 11px; min-height: 0;"
            " color: #b91c1c; border: 1px solid #fecaca; background: #fff; }"
            "QPushButton:hover { background: #fef2f2; }"
            "QPushButton:disabled { color: #a1a6b0; background: #f8fafc; border-color: #e5e7eb; }"
        )
        self._uninstall_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._uninstall_btn.clicked.connect(self._on_uninstall)
        self._actions.addWidget(self._uninstall_btn)
        row.addLayout(self._actions)
        self._has_asset = has_asset

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

        body = release.get("body", "").strip()
        self._has_notes = _has_meaningful_notes(body)
        self._notes_btn.setVisible(self._has_notes)

        self._refresh_button()

    def _apply_frame_style(self) -> None:
        if self._is_active:
            self.setStyleSheet(
                "#ReleaseRow { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; }"
            )
        elif self._is_installed:
            self.setStyleSheet(
                "#ReleaseRow { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 6px; }"
            )
        else:
            self.setStyleSheet(
                "#ReleaseRow { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 6px; }"
            )

    def _refresh_button(self) -> None:
        if self._installing:
            self._btn.setText("Cancel")
            self._btn.setEnabled(True)
            self._uninstall_btn.setVisible(False)
            return
        if self._is_active:
            self._btn.setText("✓ Active")
            self._btn.setEnabled(False)
            self._uninstall_btn.setVisible(False)
            self._uninstall_btn.setEnabled(False)
            return
        if self._is_installed:
            self._btn.setText("Use Installed")
            self._btn.setEnabled(True)
            self._uninstall_btn.setVisible(True)
            self._uninstall_btn.setEnabled(True)
            return
        if self._has_asset:
            self._btn.setText("Install")
            self._btn.setEnabled(True)
            self._uninstall_btn.setVisible(False)
            return
        self._btn.setText("No binary")
        self._btn.setEnabled(False)
        self._uninstall_btn.setVisible(False)

    def sync_install_state(self, *, active_tag: str | None, installed_tags: set[str]) -> None:
        tag = self._release["tag_name"]
        self._is_active = tag == active_tag
        self._is_installed = tag in installed_tags
        self._apply_frame_style()
        self._refresh_button()

    def _release_notes_body(self) -> str:
        body = (self._release.get("body") or "").strip()
        return re.sub(
            r"\n?\s*\*{0,2}Full Changelog\*{0,2}:\s*https?://\S+\s*$", "", body, flags=re.IGNORECASE
        ).strip()

    def _on_notes(self) -> None:
        title = f"Release notes — {self._release['tag_name']}"
        dlg = _TextDialog(title, self._release_notes_body(), markdown=True, parent=self)
        dlg.exec()

    def _on_primary_action(self) -> None:
        if self._installing:
            self._on_cancel()
        elif self._is_installed and not self._is_active:
            self.activated_requested.emit(self._release["tag_name"])
        else:
            self._on_install()

    def _on_install(self) -> None:
        self._installing = True
        self._refresh_button()
        self._progress.setValue(0)
        self._progress_row.show()
        self._progress_label.setText("0%")
        self._error_label.hide()

        self._worker = _DownloadWorker(self._release)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _on_cancel(self) -> None:
        self._btn.setText("Cancelling…")
        self._btn.setEnabled(False)
        if self._worker:
            self._worker.cancel()

    def _on_uninstall(self) -> None:
        self.uninstall_requested.emit(self._release["tag_name"])

    def _on_cancelled(self) -> None:
        self._installing = False
        self._progress_row.hide()
        self._refresh_button()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            pct = int(done * 100 / total)
            self._progress.setValue(pct)
            self._progress_label.setText(f"{_format_size(done)} / {_format_size(total)}")
        else:
            self._progress_label.setText(_format_size(done))

    def _on_success(self, tag: str) -> None:
        self._installing = False
        self._progress_row.hide()
        self._is_installed = True
        self._is_active = True
        self._apply_frame_style()
        self._refresh_button()
        self.install_requested.emit(self._release)

    def _on_failure(self, error: str) -> None:
        self._installing = False
        self._progress_row.hide()
        self._error_label.setText(error)
        self._error_label.show()
        self._refresh_button()


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
            self.refresh(initial=True)

    def refresh(self, *, initial: bool = False) -> None:
        self._fetched = True
        force_refresh = not initial
        self._refresh_btn.setEnabled(False)
        self._fetch_status.setText("Fetching releases…")
        self._fetch_status.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self._fetch_status.show()
        self._clear_list()

        worker = _FetchWorker(force_refresh=force_refresh, min_interval=gh.MANUAL_REFRESH_MIN_INTERVAL_SECS if force_refresh else gh.AUTO_REFRESH_INTERVAL_SECS)
        worker.succeeded.connect(self._on_fetch_done)
        worker.failed.connect(self._on_fetch_error)
        worker.setParent(self)
        self._fetch_worker = worker
        worker.start()

    def _on_fetch_done(self, releases: list, meta: dict) -> None:
        self._refresh_btn.setEnabled(True)
        active_tag = gh.installed_version()
        installed_tags = set(gh.installed_versions().keys())
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
            card = _ReleaseRow(release, active_tag, installed_tags, is_latest=is_latest)
            card.install_requested.connect(self._on_installed)
            card.install_requested.connect(lambda r: self.log_line.emit(f"[releases] installed {r['tag_name']}"))
            card.activated_requested.connect(self._on_activated)
            card.uninstall_requested.connect(self._on_uninstalled)
            self._cards.append(card)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)
            if gh.has_binary_asset(release):
                has_any_asset = True

        if not releases:
            self._fetch_status.setText("No releases found.")
        elif not has_any_asset:
            self._fetch_status.setText(f"{len(releases)} releases — no {gh.ASSET_PLATFORM} binaries published yet.")
        else:
            source = meta.get("source", "")
            stale = bool(meta.get("stale"))
            err = str(meta.get("error") or "").strip()
            cached_at = gh._format_cached_at(meta.get("cached_at"))
            if stale and err:
                self._fetch_status.setText(f"Using cached release data from {cached_at} — refresh failed: {err}")
                self._fetch_status.setStyleSheet("color: #b45309; font-size: 12px;")
                self._fetch_status.show()
            elif source == "cache":
                if err:
                    self._fetch_status.setText(f"Using cached release data from {cached_at} — {err}")
                else:
                    self._fetch_status.setText(f"Using cached release data from {cached_at}")
                self._fetch_status.setStyleSheet("color: #6b7280; font-size: 12px;")
                self._fetch_status.show()
            else:
                self._fetch_status.hide()

    def _on_fetch_error(self, error: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._fetch_status.setText(f"Failed to fetch releases: {error}")
        self._fetch_status.setStyleSheet("color: #b91c1c; font-size: 12px;")

    def _on_installed(self, release: dict) -> None:
        active_tag = gh.installed_version()
        installed_tags = set(gh.installed_versions().keys())
        for card in self._cards:
            card.sync_install_state(active_tag=active_tag, installed_tags=installed_tags)
        self._update_installed_label()
        self.installed.emit(release["tag_name"])

    def _on_activated(self, tag: str) -> None:
        try:
            gh.activate_installed_version(tag)
        except Exception as exc:
            self._fetch_status.setText(f"Failed to activate installed release: {exc}")
            self._fetch_status.setStyleSheet("color: #b91c1c; font-size: 12px;")
            self.log_line.emit(f"[releases] activate failed for {tag}: {exc}")
            return
        active_tag = gh.installed_version()
        installed_tags = set(gh.installed_versions().keys())
        for card in self._cards:
            card.sync_install_state(active_tag=active_tag, installed_tags=installed_tags)
        self._update_installed_label()
        self.log_line.emit(f"[releases] activated installed {tag}")
        self.installed.emit(tag)

    def _on_uninstalled(self, tag: str) -> None:
        try:
            gh.remove_installed_version(tag)
        except Exception as exc:
            self._fetch_status.setText(f"Failed to remove installed release: {exc}")
            self._fetch_status.setStyleSheet("color: #b91c1c; font-size: 12px;")
            self.log_line.emit(f"[releases] remove failed for {tag}: {exc}")
            return
        active_tag = gh.installed_version()
        installed_tags = set(gh.installed_versions().keys())
        for card in self._cards:
            card.sync_install_state(active_tag=active_tag, installed_tags=installed_tags)
        self._update_installed_label()
        self.log_line.emit(f"[releases] removed installed {tag}")

    def _update_installed_label(self) -> None:
        ver = gh.installed_version()
        if ver:
            short_path = str(gh.installed_binary()).replace(str(gh.INSTALL_DIR.parent.parent), "~")
            installed_count = len(gh.installed_versions())
            suffix = f"  ·  {installed_count} version(s) cached" if installed_count > 1 else ""
            self._installed_label.setText(f"Active: {ver}  ·  {short_path}{suffix}")
        else:
            self._installed_label.setText("No version installed — select a release below.")

    def _clear_list(self) -> None:
        self._cards.clear()
        while self._list_layout.count() > 1:  # keep the trailing stretch
            item = self._list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()


class _FetchWorker(QThread):
    succeeded = Signal(list, dict)
    failed = Signal(str)

    def __init__(self, *, force_refresh: bool = False, min_interval: int | None = None) -> None:
        super().__init__()
        self._force_refresh = force_refresh
        self._min_interval = min_interval

    def run(self) -> None:
        try:
            releases, meta = gh.fetch_releases_cached(force_refresh=self._force_refresh, min_interval=self._min_interval)
            self.succeeded.emit(releases, meta)
        except Exception as exc:
            self.failed.emit(str(exc))
