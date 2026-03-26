"""Visual diagnostics tab for Cockpitdecks Desktop.

Replaces the old text-only diagnostic cards with gauge bars,
color-coded health badges, proportional thread bars, and
inline explanations for every metric section.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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


# ── Color palette ──────────────────────────────────────────────────
_GREEN = "#22c55e"
_AMBER = "#f59e0b"
_RED = "#ef4444"
_BLUE = "#3b82f6"
_GRAY = "#94a3b8"
_DARK = "#1e293b"
_MUTED = "#64748b"
_CARD_BG = "#ffffff"
_CARD_BORDER = "#e2e5eb"
_ALT_CARD_BG = "#f8fafc"
_BADGE_OK_BG = "#f0fdf4"
_BADGE_OK_BORDER = "#bbf7d0"
_BADGE_WARN_BG = "#fffbeb"
_BADGE_WARN_BORDER = "#fde68a"
_BADGE_ERR_BG = "#fef2f2"
_BADGE_ERR_BORDER = "#fecaca"
_BADGE_NEUTRAL_BG = "#f1f5f9"
_BADGE_NEUTRAL_BORDER = "#e2e8f0"


# ── Thresholds for latency gauge coloring (ms) ────────────────────
_LATENCY_THRESHOLDS: dict[str, tuple[float, float, float]] = {
    # (warn_ms, critical_ms, bar_max_ms)
    "event_loop": (20, 50, 100),
    "flush": (10, 25, 50),
    "render": (8, 15, 30),
    "usb": (5, 10, 20),
    "page_change": (50, 100, 200),
}

_QUEUE_WARN = 30
_QUEUE_CRIT = 100
_QUEUE_MAX = 150


def _gauge_color(value: float, warn: float, crit: float) -> str:
    if value >= crit:
        return _RED
    if value >= warn:
        return _AMBER
    return _GREEN


def _bar_qss(color: str, height: int = 10) -> str:
    return (
        f"QProgressBar {{ max-height: {height}px; min-height: {height}px; "
        f"border-radius: {height // 2}px; background: #e5e7eb; border: none; }} "
        f"QProgressBar::chunk {{ background-color: {color}; "
        f"border-radius: {height // 2}px; }}"
    )


# ── Reusable building blocks ──────────────────────────────────────


def _card(bg: str = _CARD_BG, border: str = _CARD_BORDER) -> QFrame:
    f = QFrame()
    f.setStyleSheet(
        f"QFrame {{ background-color: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
    )
    return f


def _heading(text: str) -> QLabel:
    h = QLabel(text)
    h.setStyleSheet(
        "font-size: 11px; font-weight: 700; color: #6b7280; letter-spacing: 0.05em; "
        "text-transform: uppercase; border: none; padding: 0; margin: 0;"
    )
    return h


def _explanation(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(
        "font-size: 11px; color: #94a3b8; border: none; padding: 4px 0 0 0; margin: 0; "
        "line-height: 1.4;"
    )
    return lbl


def _badge(title: str, status: str = "—", level: str = "neutral") -> QFrame:
    """Colored status badge card."""
    frame = QFrame()
    frame.setMinimumWidth(140)
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    bg, border = {
        "ok": (_BADGE_OK_BG, _BADGE_OK_BORDER),
        "warn": (_BADGE_WARN_BG, _BADGE_WARN_BORDER),
        "error": (_BADGE_ERR_BG, _BADGE_ERR_BORDER),
    }.get(level, (_BADGE_NEUTRAL_BG, _BADGE_NEUTRAL_BORDER))

    frame.setStyleSheet(
        f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(4)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet("font-size: 10px; font-weight: 600; color: #6b7280; border: none; text-transform: uppercase; letter-spacing: 0.04em;")
    layout.addWidget(title_lbl)

    dot_color = {"ok": _GREEN, "warn": _AMBER, "error": _RED}.get(level, _GRAY)
    status_row = QHBoxLayout()
    status_row.setSpacing(6)
    dot = QLabel()
    dot.setFixedSize(8, 8)
    dot.setStyleSheet(f"background-color: {dot_color}; border-radius: 4px; border: none;")
    status_row.addWidget(dot)
    status_lbl = QLabel(status)
    status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {_DARK}; border: none;")
    status_row.addWidget(status_lbl, 1)
    layout.addLayout(status_row)

    frame._dot = dot
    frame._status_lbl = status_lbl
    return frame


def _update_badge(frame: QFrame, status: str, level: str) -> None:
    bg, border = {
        "ok": (_BADGE_OK_BG, _BADGE_OK_BORDER),
        "warn": (_BADGE_WARN_BG, _BADGE_WARN_BORDER),
        "error": (_BADGE_ERR_BG, _BADGE_ERR_BORDER),
    }.get(level, (_BADGE_NEUTRAL_BG, _BADGE_NEUTRAL_BORDER))
    frame.setStyleSheet(
        f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
    )
    dot_color = {"ok": _GREEN, "warn": _AMBER, "error": _RED}.get(level, _GRAY)
    frame._dot.setStyleSheet(f"background-color: {dot_color}; border-radius: 4px; border: none;")
    frame._status_lbl.setText(status)
    frame._status_lbl.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {_DARK}; border: none;")


# ── Latency gauge row ─────────────────────────────────────────────


class _LatencyGauge(QWidget):
    """Horizontal gauge bar with avg / max labels."""

    def __init__(self, label: str, metric_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metric_key = metric_key
        warn, crit, bar_max = _LATENCY_THRESHOLDS.get(metric_key, (20, 50, 100))
        self._warn = warn
        self._crit = crit
        self._bar_max = bar_max

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)

        self._name = QLabel(label)
        self._name.setFixedWidth(90)
        self._name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._name.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {_MUTED}; border: none;")
        row.addWidget(self._name)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(12)
        self._bar.setStyleSheet(_bar_qss(_GREEN, 12))
        row.addWidget(self._bar, 1)

        self._avg_lbl = QLabel("—")
        self._avg_lbl.setFixedWidth(80)
        self._avg_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._avg_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {_DARK}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        row.addWidget(self._avg_lbl)

        self._max_lbl = QLabel("")
        self._max_lbl.setFixedWidth(80)
        self._max_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._max_lbl.setStyleSheet(f"font-size: 11px; color: {_MUTED}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        row.addWidget(self._max_lbl)

        self._detail_lbl = QLabel("")
        self._detail_lbl.setFixedWidth(120)
        self._detail_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._detail_lbl.setStyleSheet(f"font-size: 11px; color: {_MUTED}; border: none;")
        row.addWidget(self._detail_lbl)

    def set_values(self, avg_ms: float = 0, max_ms: float = 0, detail: str = "") -> None:
        color = _gauge_color(avg_ms, self._warn, self._crit)
        pct = min(1.0, avg_ms / self._bar_max) if self._bar_max > 0 else 0
        self._bar.setValue(int(pct * 1000))
        self._bar.setStyleSheet(_bar_qss(color, 12))
        self._avg_lbl.setText(f"{avg_ms:.1f} ms")
        self._avg_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {color}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        self._max_lbl.setText(f"max {max_ms:.1f}" if max_ms > 0 else "")
        self._detail_lbl.setText(detail)

    def clear(self) -> None:
        self._bar.setValue(0)
        self._bar.setStyleSheet(_bar_qss(_GRAY, 12))
        self._avg_lbl.setText("—")
        self._avg_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {_DARK}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        self._max_lbl.setText("")
        self._detail_lbl.setText("")


# ── Queue depth gauge ─────────────────────────────────────────────


class _QueueGauge(QWidget):
    """Horizontal gauge for event queue depth."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)

        name = QLabel("Queue Depth")
        name.setFixedWidth(90)
        name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        name.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {_MUTED}; border: none;")
        row.addWidget(name)

        self._bar = QProgressBar()
        self._bar.setRange(0, _QUEUE_MAX)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(12)
        self._bar.setStyleSheet(_bar_qss(_GREEN, 12))
        row.addWidget(self._bar, 1)

        self._val = QLabel("—")
        self._val.setFixedWidth(80)
        self._val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {_DARK}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        row.addWidget(self._val)

        self._status = QLabel("")
        self._status.setFixedWidth(200)
        self._status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status.setStyleSheet(f"font-size: 11px; color: {_MUTED}; border: none;")
        row.addWidget(self._status)

    def set_value(self, depth: int, status_text: str = "") -> None:
        color = _gauge_color(depth, _QUEUE_WARN, _QUEUE_CRIT)
        self._bar.setValue(min(depth, _QUEUE_MAX))
        self._bar.setStyleSheet(_bar_qss(color, 12))
        self._val.setText(str(depth))
        self._val.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {color}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        self._status.setText(status_text)

    def clear(self) -> None:
        self._bar.setValue(0)
        self._bar.setStyleSheet(_bar_qss(_GRAY, 12))
        self._val.setText("—")
        self._val.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {_DARK}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        self._status.setText("")


# ── Rate metric row ───────────────────────────────────────────────


class _RateRow(QWidget):
    """Single rate metric: label + large value + unit."""

    def __init__(self, label: str, unit: str = "/s", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(8)

        name = QLabel(label)
        name.setFixedWidth(90)
        name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        name.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {_MUTED}; border: none;")
        row.addWidget(name)

        self._val = QLabel("—")
        self._val.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {_DARK}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        row.addWidget(self._val)

        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet(f"font-size: 11px; color: {_MUTED}; border: none;")
        row.addWidget(unit_lbl)
        row.addStretch(1)

    def set_value(self, text: str) -> None:
        self._val.setText(text)

    def clear(self) -> None:
        self._val.setText("—")


# ── Thread bar ────────────────────────────────────────────────────


class _ThreadBar(QWidget):
    """Single horizontal bar for a thread type."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(8)

        self._name = QLabel()
        self._name.setFixedWidth(120)
        self._name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._name.setStyleSheet(f"font-size: 11px; color: {_MUTED}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        row.addWidget(self._name)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet(_bar_qss(_BLUE, 8))
        row.addWidget(self._bar, 1)

        self._count = QLabel()
        self._count.setFixedWidth(30)
        self._count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._count.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {_DARK}; border: none;")
        row.addWidget(self._count)

    def set_data(self, name: str, count: int, max_count: int) -> None:
        self._name.setText(name)
        pct = int((count / max_count) * 100) if max_count > 0 else 0
        self._bar.setValue(pct)
        self._count.setText(str(count))


# ── Connectivity check row ────────────────────────────────────────


class _CheckRow(QWidget):
    """Endpoint check: dot + name + url + status."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 3, 0, 3)
        row.setSpacing(8)

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._dot.setStyleSheet(f"background-color: {_GRAY}; border-radius: 4px; border: none;")
        row.addWidget(self._dot)

        name_lbl = QLabel(name)
        name_lbl.setFixedWidth(110)
        name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {_DARK}; border: none;")
        row.addWidget(name_lbl)

        self._status = QLabel("—")
        self._status.setStyleSheet(f"font-size: 12px; color: {_MUTED}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sp = self._status.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self._status.setSizePolicy(sp)
        row.addWidget(self._status, 1)

    def set_status(self, text: str, ok: bool | None = None) -> None:
        self._status.setText(text)
        if ok is True:
            self._dot.setStyleSheet(f"background-color: {_GREEN}; border-radius: 4px; border: none;")
        elif ok is False:
            self._dot.setStyleSheet(f"background-color: {_RED}; border-radius: 4px; border: none;")
        else:
            self._dot.setStyleSheet(f"background-color: {_GRAY}; border-radius: 4px; border: none;")


# ── Startup detail row ────────────────────────────────────────────


def _detail_row(key: str) -> tuple[QWidget, QLabel]:
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 2, 0, 2)
    rl.setSpacing(8)
    kl = QLabel(key)
    kl.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {_MUTED}; border: none;")
    kl.setFixedWidth(80)
    kl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    rl.addWidget(kl)
    vl = QLabel("—")
    vl.setStyleSheet(f"font-size: 12px; color: {_DARK}; border: none; font-family: 'Menlo', 'SF Mono', monospace;")
    vl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    sp = vl.sizePolicy()
    sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    vl.setSizePolicy(sp)
    rl.addWidget(vl, 1)
    return row, vl


# ══════════════════════════════════════════════════════════════════
#  DIAGNOSTICS TAB WIDGET
# ══════════════════════════════════════════════════════════════════


class DiagnosticsTab(QWidget):
    """Complete visual diagnostics tab.

    Signals
    -------
    refresh_clicked : emitted when the user clicks Refresh Status
    check_clicked   : emitted when the user clicks Run Checks
    export_clicked  : emitted when the user clicks Export Bundle
    """

    refresh_clicked = Signal()
    check_clicked = Signal()
    export_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        # ── Section 1: Health Overview ────────────────────────────
        health_card = _card()
        hc = QVBoxLayout(health_card)
        hc.setContentsMargins(16, 14, 16, 14)
        hc.setSpacing(8)
        hc.addWidget(_heading("System Health"))

        badges_row = QHBoxLayout()
        badges_row.setSpacing(10)
        self._badge_launcher = _badge("Launcher", "—")
        self._badge_cockpit = _badge("Cockpitdecks", "—")
        self._badge_xplane = _badge("X-Plane", "—")
        for b in (self._badge_launcher, self._badge_cockpit, self._badge_xplane):
            badges_row.addWidget(b, 1)
        hc.addLayout(badges_row)

        hc.addWidget(_explanation(
            "Shows the live status of each subsystem. Green means connected and responding, "
            "amber indicates partial connectivity or degraded performance, and red means "
            "unreachable or failed. Launcher must be running for Cockpitdecks to start, "
            "and X-Plane must be running for simulator data to flow."
        ))
        layout.addWidget(health_card)

        # ── Section 2: Connectivity Checks ────────────────────────
        checks_card = _card()
        cc = QVBoxLayout(checks_card)
        cc.setContentsMargins(16, 14, 16, 14)
        cc.setSpacing(6)
        cc.addWidget(_heading("Connectivity Checks"))

        self._check_web = _CheckRow("Cockpit Web")
        self._check_status = _CheckRow("/desktop-status")
        self._check_metrics = _CheckRow("/desktop-metrics")
        self._check_xplane = _CheckRow("X-Plane API")
        for cr in (self._check_web, self._check_status, self._check_metrics, self._check_xplane):
            cc.addWidget(cr)

        cc.addWidget(_explanation(
            "Each row probes an HTTP endpoint. Cockpit Web is the main Flask server UI. "
            "/desktop-status returns session info (aircraft, decks). /desktop-metrics returns "
            "performance counters. X-Plane API is the simulator's REST interface for datarefs "
            "and commands. All four should be green during normal operation."
        ))
        layout.addWidget(checks_card)

        # ── Section 3: Latency Performance ────────────────────────
        latency_card = _card()
        lc = QVBoxLayout(latency_card)
        lc.setContentsMargins(16, 14, 16, 14)
        lc.setSpacing(4)
        lc.addWidget(_heading("Latency Performance"))

        self._gauge_event_loop = _LatencyGauge("Event Loop", "event_loop")
        self._gauge_flush = _LatencyGauge("Flush", "flush")
        self._gauge_render = _LatencyGauge("Render", "render")
        self._gauge_usb = _LatencyGauge("USB Batch", "usb")
        self._gauge_page = _LatencyGauge("Page Change", "page_change")
        for g in (self._gauge_event_loop, self._gauge_flush, self._gauge_render, self._gauge_usb, self._gauge_page):
            lc.addWidget(g)

        # Legend
        legend_row = QHBoxLayout()
        legend_row.setContentsMargins(100, 4, 0, 0)
        legend_row.setSpacing(16)
        for color, label in [(_GREEN, "Normal"), (_AMBER, "Warning"), (_RED, "Critical")]:
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background-color: {color}; border-radius: 4px; border: none;")
            legend_row.addWidget(dot)
            ll = QLabel(label)
            ll.setStyleSheet(f"font-size: 10px; color: {_MUTED}; border: none;")
            legend_row.addWidget(ll)
        legend_row.addStretch(1)
        lc.addLayout(legend_row)

        lc.addWidget(_explanation(
            "Event Loop measures how long each cockpit event takes to process. "
            "Flush is the total time to push rendered images to physical decks. "
            "Render is the time spent drawing button images (PIL/Cairo). "
            "USB Batch is the deck USB transfer time. "
            "Page Change is how long it takes to switch all buttons when changing pages. "
            "Values under the green threshold are healthy; amber means the system is under load; "
            "red indicates potential frame drops or input lag."
        ))
        layout.addWidget(latency_card)

        # ── Section 4: Runtime Pressure ───────────────────────────
        pressure_card = _card()
        pc = QVBoxLayout(pressure_card)
        pc.setContentsMargins(16, 14, 16, 14)
        pc.setSpacing(4)
        pc.addWidget(_heading("Runtime Pressure"))

        self._queue_gauge = _QueueGauge()
        pc.addWidget(self._queue_gauge)

        rates_grid = QGridLayout()
        rates_grid.setContentsMargins(0, 6, 0, 0)
        rates_grid.setHorizontalSpacing(24)
        rates_grid.setVerticalSpacing(2)

        self._rate_ws = _RateRow("WebSocket", "/s")
        self._rate_dataref = _RateRow("Dataref", "/s")
        self._rate_render = _RateRow("Render", "/s")
        self._rate_marks = _RateRow("Marks/Flush", "")
        self._rate_uptime = _RateRow("Uptime", "")

        left_col = QVBoxLayout()
        left_col.setSpacing(0)
        left_col.addWidget(self._rate_ws)
        left_col.addWidget(self._rate_dataref)
        left_col.addWidget(self._rate_render)

        right_col = QVBoxLayout()
        right_col.setSpacing(0)
        right_col.addWidget(self._rate_marks)
        right_col.addWidget(self._rate_uptime)
        right_col.addStretch(1)

        rates_row = QHBoxLayout()
        rates_row.setSpacing(20)
        rates_row.addLayout(left_col, 1)
        rates_row.addLayout(right_col, 1)
        pc.addLayout(rates_row)

        pc.addWidget(_explanation(
            "Queue Depth shows the event backlog waiting to be processed. A growing queue "
            "means events arrive faster than the cockpit can handle them. "
            "WebSocket rate is how many messages arrive from X-Plane per second. "
            "Dataref rate counts simulator variable updates. "
            "Render rate is how many button images are drawn per second. "
            "Marks/Flush shows how many buttons are marked dirty per flush cycle — "
            "a high ratio may indicate redundant redraws."
        ))
        layout.addWidget(pressure_card)

        # ── Section 5: Threads ────────────────────────────────────
        threads_card = _card()
        tc = QVBoxLayout(threads_card)
        tc.setContentsMargins(16, 14, 16, 14)
        tc.setSpacing(4)
        tc.addWidget(_heading("Thread Breakdown"))

        self._thread_container = QVBoxLayout()
        self._thread_container.setSpacing(2)
        self._thread_bars: list[_ThreadBar] = []
        tc.addLayout(self._thread_container)

        self._thread_total = QLabel("")
        self._thread_total.setStyleSheet(f"font-size: 11px; color: {_MUTED}; border: none; padding: 4px 0 0 0;")
        tc.addWidget(self._thread_total)

        tc.addWidget(_explanation(
            "Shows all active Python threads grouped by type. The main thread runs the cockpit "
            "event loop. WebSocket threads handle simulator communication. Timer threads manage "
            "periodic tasks like polling. A sudden increase in thread count may indicate leaked "
            "connections or stuck operations."
        ))
        layout.addWidget(threads_card)

        # ── Section 6: Startup Details ────────────────────────────
        startup_card = _card(bg=_ALT_CARD_BG, border="#e5e7eb")
        sc = QVBoxLayout(startup_card)
        sc.setContentsMargins(16, 14, 16, 14)
        sc.setSpacing(6)
        sc.addWidget(_heading("Startup Details"))

        row_launcher, self._detail_launcher = _detail_row("Launcher")
        row_target, self._detail_target = _detail_row("Target")
        row_log, self._detail_log = _detail_row("Launch Log")
        row_crash, self._detail_crash = _detail_row("Crash Log")
        row_exit, self._detail_exit = _detail_row("Last Exit")
        for r in (row_launcher, row_target, row_log, row_crash, row_exit):
            sc.addWidget(r)

        sc.addWidget(_explanation(
            "Launcher is the cockpitdecks-launcher binary path and its current state. "
            "Target is the aircraft configuration directory being used. "
            "Launch Log captures stdout/stderr from the launcher process. "
            "Crash Log is written on unhandled exceptions. "
            "Last Exit shows the process exit code (0 = normal, non-zero = error)."
        ))
        layout.addWidget(startup_card)

        # ── Section 7: Actions ────────────────────────────────────
        actions_card = _card(bg=_ALT_CARD_BG, border="#e5e7eb")
        ac = QVBoxLayout(actions_card)
        ac.setContentsMargins(16, 14, 16, 14)
        ac.setSpacing(10)
        ac.addWidget(_heading("Actions"))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_refresh = QPushButton("Refresh Status")
        self.btn_check = QPushButton("Run Checks")
        self.btn_export = QPushButton("Export Bundle")
        for b in (self.btn_refresh, self.btn_check, self.btn_export):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        ac.addLayout(btn_row)

        ac.addWidget(_explanation(
            "Refresh Status re-polls all endpoints and updates every metric above. "
            "Run Checks performs a full preflight connectivity test. "
            "Export Bundle saves all current diagnostics, settings, and recent logs "
            "to a JSON file for sharing with developers when reporting issues."
        ))
        layout.addWidget(actions_card)

        layout.addStretch(1)

        # ── Scroll area ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # ── Wire signals ──
        self.btn_refresh.clicked.connect(self.refresh_clicked)
        self.btn_check.clicked.connect(self.check_clicked)
        self.btn_export.clicked.connect(self.export_clicked)

    # ── Public API ────────────────────────────────────────────────

    def update_health(self, launcher: str, launcher_level: str,
                      cockpit: str, cockpit_level: str,
                      xplane: str, xplane_level: str) -> None:
        _update_badge(self._badge_launcher, launcher, launcher_level)
        _update_badge(self._badge_cockpit, cockpit, cockpit_level)
        _update_badge(self._badge_xplane, xplane, xplane_level)

    def update_checks(self, web: str, web_ok: bool | None,
                      status: str, status_ok: bool | None,
                      metrics: str, metrics_ok: bool | None,
                      xplane: str, xplane_ok: bool | None) -> None:
        self._check_web.set_status(web, web_ok)
        self._check_status.set_status(status, status_ok)
        self._check_metrics.set_status(metrics, metrics_ok)
        self._check_xplane.set_status(xplane, xplane_ok)

    def update_latency(self, metrics: dict | None) -> None:
        """Update all latency gauges from the full metrics dict."""
        if not isinstance(metrics, dict):
            for g in (self._gauge_event_loop, self._gauge_flush, self._gauge_render,
                      self._gauge_usb, self._gauge_page):
                g.clear()
            return

        diag = metrics.get("diagnostics") if isinstance(metrics.get("diagnostics"), dict) else {}
        if not diag:
            return

        # Event loop
        evt = diag.get("event_loop") if isinstance(diag.get("event_loop"), dict) else {}
        if evt:
            slow = evt.get("slow_count", 0)
            total = evt.get("events_processed", 0)
            last_type = evt.get("last_type", "")
            detail = f"{slow} slow" if slow else f"{total} events"
            if last_type:
                detail = f"last: {last_type}"
            self._gauge_event_loop.set_values(evt.get("avg_ms", 0), evt.get("max_ms", 0), detail)
        else:
            self._gauge_event_loop.clear()

        # Flush
        fl = diag.get("flush") if isinstance(diag.get("flush"), dict) else {}
        if fl and fl.get("count", 0) > 0:
            self._gauge_flush.set_values(fl["avg_ms"], fl["max_ms"], f"{fl['count']} flushes")
            self._gauge_render.set_values(fl.get("render_avg_ms", 0), fl.get("render_max_ms", 0), "")
            self._gauge_usb.set_values(fl.get("usb_avg_ms", 0), detail="")
        else:
            self._gauge_flush.clear()
            self._gauge_render.clear()
            self._gauge_usb.clear()

        # Page change
        pc = diag.get("page_change") if isinstance(diag.get("page_change"), dict) else {}
        if pc and pc.get("count", 0) > 0:
            self._gauge_page.set_values(pc.get("last_ms", 0), pc.get("max_ms", 0), pc.get("last_page", ""))
        else:
            self._gauge_page.clear()

    def update_pressure(self, queue_depth: int | None, queue_status: str,
                        ws_rate: str, dataref_rate: str, render_rate: str,
                        marks_per_flush: str, uptime: str) -> None:
        if queue_depth is not None:
            self._queue_gauge.set_value(queue_depth, queue_status)
        else:
            self._queue_gauge.clear()
        self._rate_ws.set_value(ws_rate)
        self._rate_dataref.set_value(dataref_rate)
        self._rate_render.set_value(render_rate)
        self._rate_marks.set_value(marks_per_flush)
        self._rate_uptime.set_value(uptime)

    def update_threads(self, threads: dict[str, int]) -> None:
        """Update thread breakdown bars."""
        # Remove excess bars
        while len(self._thread_bars) > len(threads):
            bar = self._thread_bars.pop()
            self._thread_container.removeWidget(bar)
            bar.deleteLater()

        # Add bars if needed
        while len(self._thread_bars) < len(threads):
            bar = _ThreadBar()
            self._thread_bars.append(bar)
            self._thread_container.addWidget(bar)

        if not threads:
            self._thread_total.setText("")
            return

        sorted_threads = sorted(threads.items(), key=lambda x: -x[1])
        max_count = max(threads.values()) if threads else 1
        total = sum(threads.values())

        for i, (name, count) in enumerate(sorted_threads):
            self._thread_bars[i].set_data(name, count, max_count)

        self._thread_total.setText(f"{len(threads)} thread types, {total} total")

    def update_startup(self, launcher: str, target: str, log: str, crash: str, exit_code: str) -> None:
        self._detail_launcher.setText(launcher)
        self._detail_target.setText(target)
        self._detail_log.setText(log)
        self._detail_crash.setText(crash)
        self._detail_exit.setText(exit_code)

    def clear_all(self) -> None:
        """Reset all visuals to empty/neutral state."""
        _update_badge(self._badge_launcher, "—", "neutral")
        _update_badge(self._badge_cockpit, "—", "neutral")
        _update_badge(self._badge_xplane, "—", "neutral")
        for g in (self._gauge_event_loop, self._gauge_flush, self._gauge_render,
                  self._gauge_usb, self._gauge_page):
            g.clear()
        self._queue_gauge.clear()
        for r in (self._rate_ws, self._rate_dataref, self._rate_render, self._rate_marks, self._rate_uptime):
            r.clear()
        self.update_threads({})
        self.update_startup("—", "—", "—", "—", "—")
