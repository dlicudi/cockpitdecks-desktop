"""Minimal sparkline (rolling line chart) widget for PySide6.

Used in the Runtime Metrics card to visualise CPU % and memory (MB) over time.
"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget


class SparklineWidget(QWidget):
    """Rolling line-chart widget.

    Parameters
    ----------
    max_points:
        Number of samples kept in the ring-buffer.
        At a 4-second poll cadence ``max_points=60`` gives ~4 minutes of history.
    fixed_max:
        When set the y-axis ceiling is fixed (e.g. 100.0 for CPU %).
        When ``None`` the ceiling auto-scales to 1.15× the observed maximum.
    color:
        Default line / fill colour.  Pass a ``color`` argument to :meth:`push`
        to change it dynamically (e.g. red when CPU is high).
    """

    _BG   = QColor("#f1f5f9")
    _GRID = QColor("#e2e8f0")

    def __init__(
        self,
        max_points: int = 60,
        fixed_max: float | None = None,
        color: QColor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._points: deque[float] = deque(maxlen=max_points)
        self._fixed_max = fixed_max
        self._color = color or QColor("#3b82f6")
        self.setMinimumHeight(44)
        self.setMaximumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ── Public API ────────────────────────────────────────────────────────

    def push(self, value: float, color: QColor | None = None) -> None:
        """Append a new sample and optionally update the line colour."""
        self._points.append(value)
        if color is not None:
            self._color = color
        self.update()

    def clear(self) -> None:
        """Remove all samples and repaint (shows an empty graph placeholder)."""
        self._points.clear()
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = float(self.width()), float(self.height())
        pad = 4.0  # inner padding so the endpoint dot isn't clipped

        # Background with rounded corners
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._BG))
        painter.drawRoundedRect(QRectF(0, 0, W, H), 5, 5)

        pts = list(self._points)
        if not pts:
            painter.end()
            return

        # y-axis bounds
        if self._fixed_max is not None:
            ymax = float(self._fixed_max)
        else:
            ymax = max(max(pts) * 1.15, 1.0)
        y_span = ymax or 1.0

        n = len(pts)
        x_span = W - 2 * pad

        def _x(i: int) -> float:
            return pad + x_span * i / max(n - 1, 1)

        def _y(v: float) -> float:
            return H - pad - (H - 2 * pad) * v / y_span

        # Subtle grid at 25 / 50 / 75 %
        painter.setPen(QPen(self._GRID, 0.5))
        for frac in (0.25, 0.5, 0.75):
            gy = _y(ymax * frac)
            painter.drawLine(QPointF(0, gy), QPointF(W, gy))

        # Fill polygon (area under the line)
        fill = QColor(self._color)
        fill.setAlpha(35)
        poly = QPolygonF()
        poly.append(QPointF(_x(0), H))
        for i, v in enumerate(pts):
            poly.append(QPointF(_x(i), _y(v)))
        poly.append(QPointF(_x(n - 1), H))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(poly)

        # Line
        path = QPainterPath()
        path.moveTo(_x(0), _y(pts[0]))
        for i in range(1, n):
            path.lineTo(_x(i), _y(pts[i]))
        line_pen = QPen(self._color, 1.5)
        line_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        line_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(line_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # Dot at the current (rightmost) value
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._color))
        painter.drawEllipse(QPointF(_x(n - 1), _y(pts[-1])), 3.0, 3.0)

        painter.end()
