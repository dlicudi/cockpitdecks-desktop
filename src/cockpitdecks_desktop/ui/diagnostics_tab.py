"""Visual diagnostics tab for Cockpitdecks Desktop.

Gauge bars, color-coded health badges, proportional thread bars,
and inline explanations for every metric section.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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

from cockpitdecks_desktop.ui.sparkline import SparklineWidget


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
    "event_loop": (200, 1000, 2000),
    "flush": (200, 1000, 2000),
    "render": (200, 1000, 2000),
    "usb": (50, 500, 1000),
    "page_change": (500, 1000, 2000),
}

_QUEUE_WARN = 30
_QUEUE_CRIT = 100
_QUEUE_MAX = 150

_MONO = "'Menlo', 'SF Mono', monospace"


def _parse_rate(text: str) -> float | None:
    """Parse a formatted rate string ('12.3', '—', '') to float or None."""
    try:
        return float(text) if text not in ("—", "", None) else None
    except (ValueError, TypeError):
        return None


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
    h = QLabel(text.upper())
    h.setStyleSheet(
        "font-size: 10px; font-weight: 700; color: #6b7280;"
        " border: none; padding: 0; margin: 0;"
    )
    return h


def _hint(text: str) -> QLabel:
    """Compact inline explanation — kept visible but subdued."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(
        "font-size: 10px; color: #94a3b8; border: none;"
        " padding: 2px 0 0 0; margin: 0;"
    )
    return lbl


def _status_bar(text: str = "") -> QLabel:
    """Mini status strip flush at the bottom of a card."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setContentsMargins(0, 0, 0, 0)
    lbl.setStyleSheet(
        f"font-size: 10px; color: {_MUTED}; border: none;"
        " background: #f1f5f9; border-radius: 0 0 9px 9px;"
        " padding: 5px 12px;"
    )
    return lbl


def _card_with_status(bg: str = _CARD_BG, border: str = _CARD_BORDER) -> tuple[QFrame, QVBoxLayout, QLabel]:
    """Card with a flush-bottom status bar. Returns (frame, content_layout, status_label)."""
    frame = QFrame()
    frame.setStyleSheet(
        f"QFrame {{ background-color: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
    )
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(12, 10, 12, 6)
    content_layout.setSpacing(2)
    outer.addWidget(content, 1)

    status = _status_bar()
    outer.addWidget(status)

    return frame, content_layout, status


def _badge(title: str, status: str = "\u2014", level: str = "neutral") -> QFrame:
    """Colored status badge card."""
    frame = QFrame()
    frame.setMinimumWidth(100)
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
    layout.setContentsMargins(10, 8, 10, 8)
    layout.setSpacing(3)

    title_lbl = QLabel(title.upper())
    title_lbl.setStyleSheet(
        "font-size: 9px; font-weight: 600; color: #6b7280; border: none;"
    )
    layout.addWidget(title_lbl)

    dot_color = {"ok": _GREEN, "warn": _AMBER, "error": _RED}.get(level, _GRAY)
    status_row = QHBoxLayout()
    status_row.setSpacing(5)
    dot = QLabel()
    dot.setFixedSize(8, 8)
    dot.setStyleSheet(f"background-color: {dot_color}; border-radius: 4px; border: none;")
    status_row.addWidget(dot)
    status_lbl = QLabel(status)
    status_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {_DARK}; border: none;")
    status_row.addWidget(status_lbl, 1)
    layout.addLayout(status_row)

    frame._dot = dot  # noqa: SLF001
    frame._status_lbl = status_lbl  # noqa: SLF001
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
    frame._dot.setStyleSheet(f"background-color: {dot_color}; border-radius: 4px; border: none;")  # noqa: SLF001
    frame._status_lbl.setText(status)  # noqa: SLF001
    frame._status_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {_DARK}; border: none;")  # noqa: SLF001


# ── Latency gauge row ─────────────────────────────────────────────


class _LatencyGauge(QWidget):
    """Horizontal gauge bar + sparkline trend with avg / max labels."""

    def __init__(self, label: str, metric_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metric_key = metric_key
        warn, crit, bar_max = _LATENCY_THRESHOLDS.get(metric_key, (20, 50, 100))
        self._warn = warn
        self._crit = crit
        self._bar_max = bar_max

        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 1, 0, 1)
        vl.setSpacing(2)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._name = QLabel(label)
        self._name.setMinimumWidth(70)
        self._name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._name.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {_MUTED}; border: none;")
        row.addWidget(self._name)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(10)
        self._bar.setMaximumWidth(200)
        self._bar.setStyleSheet(_bar_qss(_GREEN, 10))
        row.addWidget(self._bar)

        self._avg_lbl = QLabel("\u2014")
        self._avg_lbl.setMinimumWidth(55)
        self._avg_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._avg_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {_DARK}; border: none; font-family: {_MONO};")
        row.addWidget(self._avg_lbl)

        self._max_lbl = QLabel("")
        self._max_lbl.setMinimumWidth(55)
        self._max_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._max_lbl.setStyleSheet(f"font-size: 10px; color: {_MUTED}; border: none; font-family: {_MONO};")
        row.addWidget(self._max_lbl)

        self._detail_lbl = QLabel("")
        self._detail_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._detail_lbl.setStyleSheet(f"font-size: 10px; color: {_MUTED}; border: none;")
        row.addWidget(self._detail_lbl, 1)

        vl.addLayout(row)

        self._spark = SparklineWidget(max_points=60, color=QColor(_GREEN))
        self._spark.setMinimumHeight(26)
        self._spark.setMaximumHeight(30)
        vl.addWidget(self._spark)

    def set_values(self, avg_ms: float = 0, max_ms: float = 0, detail: str = "") -> None:
        color = _gauge_color(avg_ms, self._warn, self._crit)
        pct = min(1.0, avg_ms / self._bar_max) if self._bar_max > 0 else 0
        self._bar.setValue(int(pct * 1000))
        self._bar.setStyleSheet(_bar_qss(color, 10))
        self._avg_lbl.setText(f"{avg_ms:.1f} ms")
        self._avg_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {color}; border: none; font-family: {_MONO};")
        self._max_lbl.setText(f"max {max_ms:.1f}" if max_ms > 0 else "")
        self._detail_lbl.setText(detail)
        self._spark.push(avg_ms, QColor(color))

    def clear(self) -> None:
        self._bar.setValue(0)
        self._bar.setStyleSheet(_bar_qss(_GRAY, 10))
        self._avg_lbl.setText("\u2014")
        self._avg_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {_DARK}; border: none; font-family: {_MONO};")
        self._max_lbl.setText("")
        self._detail_lbl.setText("")
        self._spark.clear()


# ── Queue depth gauge ─────────────────────────────────────────────


class _QueueGauge(QWidget):
    """Horizontal gauge for event queue depth."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(8)

        name = QLabel("Queue Depth")
        name.setMinimumWidth(70)
        name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        name.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {_MUTED}; border: none;")
        row.addWidget(name)

        self._bar = QProgressBar()
        self._bar.setRange(0, _QUEUE_MAX)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(10)
        self._bar.setStyleSheet(_bar_qss(_GREEN, 10))
        row.addWidget(self._bar, 2)

        self._val = QLabel("\u2014")
        self._val.setMinimumWidth(30)
        self._val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {_DARK}; border: none; font-family: {_MONO};")
        row.addWidget(self._val)

    def set_value(self, depth: int) -> None:
        color = _gauge_color(depth, _QUEUE_WARN, _QUEUE_CRIT)
        self._bar.setValue(min(depth, _QUEUE_MAX))
        self._bar.setStyleSheet(_bar_qss(color, 10))
        self._val.setText(str(depth))
        self._val.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {color}; border: none; font-family: {_MONO};")

    def clear(self) -> None:
        self._bar.setValue(0)
        self._bar.setStyleSheet(_bar_qss(_GRAY, 10))
        self._val.setText("\u2014")
        self._val.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {_DARK}; border: none; font-family: {_MONO};")



# ── Rate metric row ───────────────────────────────────────────────


class _RateRow(QWidget):
    """Single rate metric: label + value + unit."""

    def __init__(self, label: str, unit: str = "/s", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        name = QLabel(label)
        name.setMinimumWidth(70)
        name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        name.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {_MUTED}; border: none;")
        row.addWidget(name)

        self._val = QLabel("\u2014")
        self._val.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {_DARK}; border: none; font-family: {_MONO};")
        row.addWidget(self._val)

        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet(f"font-size: 10px; color: {_MUTED}; border: none;")
        row.addWidget(unit_lbl)
        row.addStretch(1)

    def set_value(self, text: str) -> None:
        self._val.setText(text)

    def clear(self) -> None:
        self._val.setText("\u2014")


# ── Spark row ─────────────────────────────────────────────────────


class _SparkRow(QWidget):
    """Label + rolling sparkline + current value + session peak with timestamp."""

    def __init__(
        self,
        label: str,
        unit: str = "",
        fixed_max: float | None = None,
        warn: float | None = None,
        crit: float | None = None,
        color: str = _BLUE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._warn = warn
        self._crit = crit
        self._peak: float | None = None
        self._default_color = QColor(color)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        name_lbl = QLabel(label)
        name_lbl.setMinimumWidth(70)
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        name_lbl.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {_MUTED}; border: none;")
        row.addWidget(name_lbl)

        self._spark = SparklineWidget(max_points=60, fixed_max=fixed_max, color=QColor(color))
        self._spark.setMinimumHeight(34)
        self._spark.setMaximumHeight(40)
        row.addWidget(self._spark, 1)

        right = QVBoxLayout()
        right.setSpacing(0)
        right.setContentsMargins(0, 0, 0, 0)

        val_row = QHBoxLayout()
        val_row.setSpacing(2)
        self._val = QLabel("\u2014")
        self._val.setMinimumWidth(40)
        self._val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {_DARK}; border: none; font-family: {_MONO};"
        )
        val_row.addWidget(self._val)
        if unit:
            unit_lbl = QLabel(unit)
            unit_lbl.setStyleSheet(f"font-size: 10px; color: {_MUTED}; border: none;")
            val_row.addWidget(unit_lbl)
        right.addLayout(val_row)

        self._peak_lbl = QLabel("")
        self._peak_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._peak_lbl.setStyleSheet(f"font-size: 9px; color: {_MUTED}; border: none;")
        right.addWidget(self._peak_lbl)

        row.addLayout(right)

    def push(self, value: float) -> None:
        from datetime import datetime
        if self._crit is not None and value >= self._crit:
            color = QColor(_RED)
        elif self._warn is not None and value >= self._warn:
            color = QColor(_AMBER)
        else:
            color = self._default_color
        self._val.setText(f"{value:.0f}")
        self._val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {color.name()}; border: none; font-family: {_MONO};"
        )
        self._spark.push(value, color)
        if self._peak is None or value > self._peak:
            self._peak = value
            now = datetime.now().strftime("%H:%M")
            self._peak_lbl.setText(f"\u2191{value:.0f} @ {now}")

    def clear(self) -> None:
        self._peak = None
        self._peak_lbl.setText("")
        self._val.setText("\u2014")
        self._val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {_DARK}; border: none; font-family: {_MONO};"
        )
        self._spark.clear()


# ── Thread bar ────────────────────────────────────────────────────


class _ThreadBar(QWidget):
    """Single horizontal bar for a thread type."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(6)

        self._name = QLabel()
        self._name.setMinimumWidth(90)
        self._name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._name.setStyleSheet(f"font-size: 10px; color: {_MUTED}; border: none; font-family: {_MONO};")
        row.addWidget(self._name)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(7)
        self._bar.setStyleSheet(_bar_qss(_BLUE, 7))
        row.addWidget(self._bar, 1)

        self._count = QLabel()
        self._count.setMinimumWidth(24)
        self._count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._count.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {_DARK}; border: none;")
        row.addWidget(self._count)

    def set_data(self, name: str, count: int, max_count: int) -> None:
        self._name.setText(name)
        pct = int((count / max_count) * 100) if max_count > 0 else 0
        self._bar.setValue(pct)
        self._count.setText(str(count))


# ── Connectivity check row ────────────────────────────────────────


class _CheckRow(QWidget):
    """Endpoint check: dot + name + status."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        self._dot = QLabel()
        self._dot.setFixedSize(7, 7)
        self._dot.setStyleSheet(f"background-color: {_GRAY}; border-radius: 3px; border: none;")
        row.addWidget(self._dot)

        name_lbl = QLabel(name)
        name_lbl.setMinimumWidth(90)
        name_lbl.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {_DARK}; border: none;")
        row.addWidget(name_lbl)

        self._status = QLabel("\u2014")
        self._status.setStyleSheet(f"font-size: 11px; color: {_MUTED}; border: none; font-family: {_MONO};")
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sp = self._status.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self._status.setSizePolicy(sp)
        row.addWidget(self._status, 1)

    def set_status(self, text: str, ok: bool | None = None) -> None:
        self._status.setText(text)
        if ok is True:
            self._dot.setStyleSheet(f"background-color: {_GREEN}; border-radius: 3px; border: none;")
        elif ok is False:
            self._dot.setStyleSheet(f"background-color: {_RED}; border-radius: 3px; border: none;")
        else:
            self._dot.setStyleSheet(f"background-color: {_GRAY}; border-radius: 3px; border: none;")


# ── Startup detail row ────────────────────────────────────────────


def _detail_row(key: str) -> tuple[QWidget, QLabel]:
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 1, 0, 1)
    rl.setSpacing(6)
    kl = QLabel(key)
    kl.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {_MUTED}; border: none;")
    kl.setMinimumWidth(60)
    kl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    rl.addWidget(kl)
    vl = QLabel("\u2014")
    vl.setStyleSheet(f"font-size: 11px; color: {_DARK}; border: none; font-family: {_MONO};")
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
    """Complete visual diagnostics tab."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Scrollable content ───────────────────────────────────
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        # ── Section 1: Health Overview (hero — no card wrapper) ──
        badges_row = QHBoxLayout()
        badges_row.setSpacing(8)
        self._badge_launcher = _badge("Cockpitdecks", "\u2014")
        self._badge_cockpit = _badge("Cockpitdecks Web", "\u2014")
        self._badge_xplane = _badge("X-Plane", "\u2014")
        for b in (self._badge_launcher, self._badge_cockpit, self._badge_xplane):
            badges_row.addWidget(b, 1)
        layout.addLayout(badges_row)

        layout.addWidget(_hint(
            "Green = connected and responding. Amber = partial / degraded. Red = unreachable or failed."
        ))

        # ── Section 2: Connectivity Checks ────────────────────────
        checks_card, cc, self._status_connectivity = _card_with_status()
        cc.setSpacing(3)
        cc.addWidget(_heading("Connectivity"))

        self._check_cockpitdecks = _CheckRow("Cockpitdecks")
        self._check_xplane = _CheckRow("X-Plane")
        self._check_hardware = _CheckRow("Hardware")
        for cr in (self._check_cockpitdecks, self._check_xplane, self._check_hardware):
            cc.addWidget(cr)

        cc.addWidget(_hint(
            "All three should be green during normal operation."
        ))
        layout.addWidget(checks_card)

        # ── Section 3: Latency + Pressure (two-column) ───────────
        perf_row = QHBoxLayout()
        perf_row.setSpacing(10)

        # Left: latency gauges
        latency_card, lc, self._status_latency = _card_with_status()
        lc.addWidget(_heading("Latency"))

        self._gauge_event_loop = _LatencyGauge("Event Loop", "event_loop")
        self._gauge_flush = _LatencyGauge("Flush", "flush")
        self._gauge_render = _LatencyGauge("Render", "render")
        self._gauge_usb = _LatencyGauge("USB Batch", "usb")
        self._gauge_page = _LatencyGauge("Page Change", "page_change")
        for g in (self._gauge_event_loop, self._gauge_flush, self._gauge_render, self._gauge_usb, self._gauge_page):
            lc.addWidget(g)

        legend_row = QHBoxLayout()
        legend_row.setContentsMargins(78, 2, 0, 0)
        legend_row.setSpacing(10)
        for color, label in [(_GREEN, "OK"), (_AMBER, "Warn"), (_RED, "Crit")]:
            dot = QLabel()
            dot.setFixedSize(6, 6)
            dot.setStyleSheet(f"background-color: {color}; border-radius: 3px; border: none;")
            legend_row.addWidget(dot)
            ll = QLabel(label)
            ll.setStyleSheet(f"font-size: 9px; color: {_MUTED}; border: none;")
            legend_row.addWidget(ll)
        legend_row.addStretch(1)
        lc.addLayout(legend_row)

        lc.addWidget(_hint(
            "Event Loop = per-event processing time. Flush = pushing images to decks. "
            "Render = drawing buttons. USB = deck transfer. Page Change = full page switch."
        ))
        perf_row.addWidget(latency_card, 3)

        # Right: runtime pressure
        pressure_card, pc, self._status_pressure = _card_with_status()
        pc.addWidget(_heading("Runtime Pressure"))

        self._spark_queue = _SparkRow("Queue Depth", "", warn=float(_QUEUE_WARN), crit=float(_QUEUE_CRIT), color=_GREEN)
        pc.addWidget(self._spark_queue)

        self._spark_dataref = _SparkRow("Dataref", "/s", color=_BLUE)
        self._spark_ws = _SparkRow("WebSocket", "/s", color=_BLUE)
        self._spark_render = _SparkRow("Render", "/s", color=_BLUE)
        self._rate_marks = _RateRow("Marks/Flush", "")
        self._rate_uptime = _RateRow("Uptime", "")
        for r in (self._spark_dataref, self._spark_ws, self._spark_render, self._rate_marks, self._rate_uptime):
            pc.addWidget(r)

        pc.addWidget(_hint(
            "Queue Depth = event backlog. A growing queue means events arrive faster "
            "than they can be processed. Marks/Flush = dirty buttons per cycle."
        ))
        perf_row.addWidget(pressure_card, 2)

        layout.addLayout(perf_row)

        # ── Section 4: Threads + Startup (two-column) ─────────────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        # Left: threads
        threads_card, tc, self._status_threads = _card_with_status()
        tc.addWidget(_heading("Threads"))

        self._thread_container = QVBoxLayout()
        self._thread_container.setSpacing(1)
        self._thread_bars: list[_ThreadBar] = []
        tc.addLayout(self._thread_container)

        self._thread_total = QLabel("")
        self._thread_total.setStyleSheet(f"font-size: 10px; color: {_MUTED}; border: none; padding: 2px 0 0 0;")
        tc.addWidget(self._thread_total)

        tc.addWidget(_hint(
            "Active Python threads by type. A sudden increase may indicate leaked connections."
        ))
        bottom_row.addWidget(threads_card, 1)

        # Right: startup details
        startup_card, sc, self._status_startup = _card_with_status(bg=_ALT_CARD_BG, border="#e5e7eb")
        sc.addWidget(_heading("Startup Details"))

        def _group_sep() -> QFrame:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet(f"color: #e5e7eb; max-height: 1px; border: none; margin: 4px 0;")
            return sep

        def _group_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {_MUTED}; border: none; "
                              f"letter-spacing: 0.05em; text-transform: uppercase; padding-top: 2px;")
            return lbl

        # ── Group 1: Status ──
        sc.addWidget(_group_label("Status"))
        row_launcher, self._detail_launcher = _detail_row("Cockpitdecks")
        row_exit, self._detail_exit = _detail_row("Exit code")
        row_init, self._detail_init = _detail_row("Init time")
        for r in (row_launcher, row_exit, row_init):
            sc.addWidget(r)

        # ── Group 2: Last run ──
        sc.addWidget(_group_sep())
        sc.addWidget(_group_label("Last run"))
        row_extensions, self._detail_extensions = _detail_row("Extensions")
        row_hardware, self._detail_hardware = _detail_row("Hardware")
        for r in (row_extensions, row_hardware):
            sc.addWidget(r)

        # ── Group 3: Paths ──
        sc.addWidget(_group_sep())
        sc.addWidget(_group_label("Paths"))
        row_target, self._detail_target = _detail_row("Target")
        row_log, self._detail_log = _detail_row("Cockpitdecks log")
        row_crash, self._detail_crash = _detail_row("Crash log")
        for r in (row_target, row_log, row_crash):
            sc.addWidget(r)

        # ── Group 4: Packages ──
        sc.addWidget(_group_sep())
        sc.addWidget(_group_label("Packages"))
        self._detail_packages = QLabel("\u2014")
        self._detail_packages.setWordWrap(True)
        self._detail_packages.setStyleSheet(
            f"font-size: 11px; color: {_DARK}; border: none; font-family: {_MONO}; padding: 2px 0;"
        )
        self._detail_packages.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sc.addWidget(self._detail_packages)

        self._populate_packages()
        bottom_row.addWidget(startup_card, 1)

        layout.addLayout(bottom_row)

        layout.addStretch(1)

        # ── Scroll area ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

    # ── Public API ────────────────────────────────────────────────

    def update_health(self, launcher: str, launcher_level: str,
                      cockpit: str, cockpit_level: str,
                      xplane: str, xplane_level: str) -> None:
        _update_badge(self._badge_launcher, launcher, launcher_level)
        _update_badge(self._badge_cockpit, cockpit, cockpit_level)
        _update_badge(self._badge_xplane, xplane, xplane_level)

    def update_checks(self, cockpitdecks: str, cockpitdecks_ok: bool | None,
                      xplane: str, xplane_ok: bool | None,
                      hardware: str = "", hardware_ok: bool | None = None) -> None:
        self._check_cockpitdecks.set_status(cockpitdecks, cockpitdecks_ok)
        self._check_xplane.set_status(xplane, xplane_ok)
        self._check_hardware.set_status(hardware or "\u2014", hardware_ok)

        checks = [cockpitdecks_ok, xplane_ok, hardware_ok]
        ok_count = sum(1 for c in checks if c is True)
        fail_count = sum(1 for c in checks if c is False)
        if fail_count:
            self._status_connectivity.setText(f"{fail_count} endpoint(s) unreachable")
        elif ok_count == len(checks):
            self._status_connectivity.setText("All endpoints responding")
        else:
            self._status_connectivity.setText("Waiting for data\u2026")

    def update_latency(self, metrics: dict | None) -> None:
        """Update all latency gauges from the full metrics dict."""
        if not isinstance(metrics, dict):
            for g in (self._gauge_event_loop, self._gauge_flush, self._gauge_render,
                      self._gauge_usb, self._gauge_page):
                g.clear()
            self._status_latency.setText("")
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
        pgc = diag.get("page_change") if isinstance(diag.get("page_change"), dict) else {}
        if pgc and pgc.get("count", 0) > 0:
            self._gauge_page.set_values(pgc.get("last_ms", 0), pgc.get("max_ms", 0), pgc.get("last_page", ""))
        else:
            self._gauge_page.clear()

        # Latency status bar summary
        worst_level = "ok"
        for key, (warn, crit, _) in _LATENCY_THRESHOLDS.items():
            sub = diag.get(key) if isinstance(diag.get(key), dict) else {}
            avg = sub.get("avg_ms", 0) if key == "event_loop" else sub.get("last_ms", sub.get("avg_ms", 0))
            if avg >= crit:
                worst_level = "crit"
                break
            if avg >= warn and worst_level != "crit":
                worst_level = "warn"

        if worst_level == "crit":
            self._status_latency.setText("One or more metrics in critical range")
        elif worst_level == "warn":
            self._status_latency.setText("Elevated latency detected")
        else:
            self._status_latency.setText("All latencies within normal range")

    def update_pressure(self, queue_depth: int | None, queue_status: str,
                        ws_rate: str, dataref_rate: str, render_rate: str,
                        marks_per_flush: str, uptime: str) -> None:
        if queue_depth is not None:
            self._spark_queue.push(float(queue_depth))
        else:
            self._spark_queue.clear()

        ws = _parse_rate(ws_rate)
        dr = _parse_rate(dataref_rate)
        rr = _parse_rate(render_rate)

        if ws is not None:
            self._spark_ws.push(ws)
        else:
            self._spark_ws.clear()

        if dr is not None:
            self._spark_dataref.push(dr)
        else:
            self._spark_dataref.clear()

        if rr is not None:
            self._spark_render.push(rr)
        else:
            self._spark_render.clear()

        self._rate_marks.set_value(marks_per_flush)
        self._rate_uptime.set_value(uptime)
        self._status_pressure.setText(queue_status if queue_status else "")

    def update_threads(self, threads: dict[str, int]) -> None:
        """Update thread breakdown bars."""
        while len(self._thread_bars) > len(threads):
            bar = self._thread_bars.pop()
            self._thread_container.removeWidget(bar)
            bar.deleteLater()

        while len(self._thread_bars) < len(threads):
            bar = _ThreadBar()
            self._thread_bars.append(bar)
            self._thread_container.addWidget(bar)

        if not threads:
            self._thread_total.setText("")
            self._status_threads.setText("")
            return

        sorted_threads = sorted(threads.items(), key=lambda x: -x[1])
        max_count = max(threads.values()) if threads else 1
        total = sum(threads.values())

        for i, (name, count) in enumerate(sorted_threads):
            self._thread_bars[i].set_data(name, count, max_count)

        self._thread_total.setText(f"{len(threads)} types, {total} total")
        self._status_threads.setText(f"{total} active threads")

    def _populate_packages(self) -> None:
        """Query installed cockpitdecks_* packages via importlib.metadata and fill the Packages row."""
        import importlib.metadata
        parts: list[str] = []
        for dist in sorted(importlib.metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
            name = dist.metadata["Name"] or ""
            if name.lower().startswith("cockpitdecks"):
                version = dist.metadata["Version"] or "?"
                parts.append(f"{name} {version}")
        self._detail_packages.setText("   ·   ".join(parts) if parts else "\u2014")

    def update_log_analysis(self, init_s: float | None, extensions: list[str],
                            missing: list[str], hardware: dict[str, int],
                            last_usb: str) -> None:
        """Update startup card rows derived from parsing launcher log output."""
        if init_s is not None:
            self._detail_init.setText(f"{init_s:.1f} s")
        else:
            self._detail_init.setText("\u2014")

        if extensions:
            ext_text = ", ".join(extensions)
            if missing:
                ext_text += f"  \u26a0\ufe0f missing: {', '.join(missing)}"
            self._detail_extensions.setText(ext_text)
        elif missing:
            self._detail_extensions.setText(f"\u26a0\ufe0f missing: {', '.join(missing)}")
        else:
            self._detail_extensions.setText("\u2014")

        if hardware:
            parts = [f"{count} {name}" for name, count in sorted(hardware.items())]
            hw_text = ", ".join(parts)
            if last_usb:
                hw_text += f"  \u00b7  {last_usb}"
            self._detail_hardware.setText(hw_text)
        else:
            self._detail_hardware.setText(last_usb or "\u2014")

    def update_startup(self, launcher: str, target: str, log: str, crash: str, exit_code: str) -> None:
        self._detail_launcher.setText(launcher)
        self._detail_target.setText(target)
        self._detail_log.setText(log)
        self._detail_crash.setText(crash)
        self._detail_exit.setText(exit_code)

        if "running" in launcher.lower():
            self._status_startup.setText("Cockpitdecks running")
        elif "exited" in launcher.lower():
            self._status_startup.setText(f"Last exit: {exit_code}")
        else:
            self._status_startup.setText("Cockpitdecks idle")

    def clear_all(self) -> None:
        """Reset all visuals to empty/neutral state."""
        _update_badge(self._badge_launcher, "\u2014", "neutral")
        _update_badge(self._badge_cockpit, "\u2014", "neutral")
        _update_badge(self._badge_xplane, "\u2014", "neutral")
        for g in (self._gauge_event_loop, self._gauge_flush, self._gauge_render,
                  self._gauge_usb, self._gauge_page):
            g.clear()
        for r in (self._spark_queue, self._spark_dataref, self._spark_ws, self._spark_render,
                  self._rate_marks, self._rate_uptime):
            r.clear()
        self.update_threads({})
        self.update_startup("\u2014", "\u2014", "\u2014", "\u2014", "\u2014")
        self.update_log_analysis(None, [], [], {}, "")
        for sb in (self._status_connectivity, self._status_latency, self._status_pressure,
                   self._status_threads, self._status_startup):
            sb.setText("")
