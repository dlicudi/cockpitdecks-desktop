"""Live component topology diagram for Cockpitdecks Desktop.

Paints a QPainter-based node/edge graph showing how every component
(Desktop App, Launcher, Cockpitdecks, X-Plane, physical and web decks)
connects, with live status colour-coding on each node and edge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget


# ── Palette (matches app-wide theme) ──────────────────────────────────────
_OK    = QColor("#22c55e")
_WARN  = QColor("#f59e0b")
_ERROR = QColor("#ef4444")
_GRAY  = QColor("#94a3b8")
_BLUE  = QColor("#3b82f6")
_DARK  = QColor("#1e293b")
_MUTED = QColor("#64748b")

_NODE_BG      = QColor("#ffffff")
_HUB_BG       = QColor("#eff6ff")   # light blue tint for the central hub
_COMPONENT_BG = QColor("#ecfeff")   # light cyan for embedded library nodes
_CANVAS_BG    = QColor("#f1f5f9")
_EDGE_NEUTRAL = QColor("#cbd5e1")


def _status_color(status: str) -> QColor:
    return {"ok": _OK, "warn": _WARN, "error": _ERROR}.get(status, _GRAY)


# ── Data model ────────────────────────────────────────────────────────────


@dataclass
class _Node:
    key: str
    title: str
    subtitle: str = ""
    detail: str = ""           # Optional third line (smaller font)
    status: str = "neutral"   # ok | warn | error | neutral
    hub: bool = False          # True → blue accent border + tinted fill
    component: bool = False    # True → cyan tint; marks an in-process library node
    W: float = 150
    H: float = 58
    # Assigned by _layout()
    cx: float = 0.0
    cy: float = 0.0

    def rect(self) -> QRectF:
        return QRectF(self.cx - self.W / 2, self.cy - self.H / 2, self.W, self.H)


@dataclass
class _Edge:
    src: str
    dst: str
    label: str = ""
    metric: str = ""
    status: str = "neutral"
    dashed: bool = False
    bidirectional: bool = False


# ── Tab widget ────────────────────────────────────────────────────────────


class TopologyTab(QWidget):
    """Custom-painted live topology diagram."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nodes: list[_Node] = self._default_nodes()
        self._edges: list[_Edge] = self._default_edges()
        self.setMinimumHeight(300)

    # ── Default / empty state ─────────────────────────────────────────────

    def _default_nodes(self) -> list[_Node]:
        return [
            _Node("desktop",       "Desktop App",   status="ok",  W=150, H=58),
            _Node("cockpitdecks",  "Cockpitdecks",  hub=True,     W=170, H=76),
            _Node("xplane_webapi", "xplane-webapi", component=True, W=140, H=52),
            _Node("xplane",        "X-Plane",                      W=150, H=58),
        ]

    def _default_edges(self) -> list[_Edge]:
        return [
            _Edge("desktop",       "cockpitdecks",  "starts",              dashed=True),
            _Edge("desktop",       "cockpitdecks",  "HTTP"),
            _Edge("cockpitdecks",  "xplane_webapi", ""),
            _Edge("xplane_webapi", "xplane",        "WebSocket",           bidirectional=True),
        ]

    # ── Public update API ─────────────────────────────────────────────────

    def update_topology(
        self,
        *,
        launcher_status: str,
        launcher_label: str,
        launcher_custom: bool = False,
        launcher_pid: int | None = None,
        cockpit_status: str,
        cockpit_label: str,
        cockpit_version: str = "",
        cockpit_uptime: str = "",
        cockpit_aircraft: str = "",
        xplane_status: str,
        xplane_label: str,
        desktop_label: str,
        cockpit_reachable: bool | None,
        xplane_reachable: bool | None,
        launcher_running: bool,
        decks: list[dict],
        dataref_rate: str = "",
        ws_rate: str = "",
        ws_stall_count: int = 0,
        cockpit_web_host: str = "127.0.0.1",
        cockpit_web_port: str = "7777",
    ) -> None:
        """Rebuild node/edge state from live poll data and schedule a repaint."""

        # ── Fixed nodes ───────────────────────────────────────────────────
        node_map = {n.key: n for n in self._nodes
                    if not n.key.startswith("deck_") and n.key != "webdecks" and n.key != "xplane_webapi"}

        # Ensure xplane_webapi is always present as a fixed node
        existing = {n.key: n for n in self._nodes}
        if "xplane_webapi" in existing:
            node_map["xplane_webapi"] = existing["xplane_webapi"]
        else:
            node_map["xplane_webapi"] = _Node(
                "xplane_webapi", "xplane-webapi",
                subtitle="WS + REST client", component=True, W=140, H=52,
            )

        node_map["desktop"].subtitle = desktop_label

        # Cockpitdecks hub: version + uptime on subtitle, aircraft + PID on detail
        if cockpit_version or (cockpit_uptime and cockpit_uptime not in ("—", "")):
            sub_parts = []
            if cockpit_version:
                v = cockpit_version if cockpit_version.startswith("v") else f"v{cockpit_version}"
                sub_parts.append(v)
            if cockpit_uptime and cockpit_uptime not in ("—", ""):
                sub_parts.append(cockpit_uptime)
            node_map["cockpitdecks"].subtitle = " · ".join(sub_parts)
            detail_parts = []
            if cockpit_aircraft:
                detail_parts.append(cockpit_aircraft)
            elif cockpit_label:
                detail_parts.append(cockpit_label)
            if launcher_running and launcher_pid is not None:
                detail_parts.append(f"PID {launcher_pid}")
            node_map["cockpitdecks"].detail = " · ".join(detail_parts)
        else:
            sub_parts = [cockpit_label] if cockpit_label else []
            if launcher_running and launcher_pid is not None:
                sub_parts.append(f"PID {launcher_pid}")
            node_map["cockpitdecks"].subtitle = " · ".join(sub_parts)
            node_map["cockpitdecks"].detail = ""
        node_map["cockpitdecks"].status = cockpit_status if cockpit_status != "neutral" else launcher_status

        node_map["xplane"].subtitle = xplane_label
        node_map["xplane"].status = xplane_status

        # xplane-webapi: always present; status mirrors cockpitdecks reachability
        if ws_stall_count > 0:
            node_map["xplane_webapi"].subtitle = f"WS stalled ({ws_stall_count}x)"
            node_map["xplane_webapi"].status = "error"
        else:
            node_map["xplane_webapi"].subtitle = "WS + REST client"
            node_map["xplane_webapi"].status = cockpit_status

        # ── Deck nodes (rebuilt each time) ────────────────────────────────
        deck_nodes: list[_Node] = []
        web_count = 0
        for d in decks:
            if d.get("virtual"):
                web_count += 1
                continue
            connected = d.get("connected", False)
            running   = d.get("running", False)
            deck_type = d.get("type", "") or ""
            sub = deck_type
            if connected and running:
                sub += " · running" if sub else "running"
            elif connected:
                sub += " · connected" if sub else "connected"
            else:
                sub += " · disconnected" if sub else "disconnected"
            deck_nodes.append(_Node(
                key=f"deck_{d.get('name', '')}",
                title=d.get("name", "Deck"),
                subtitle=sub,
                status="ok" if connected else "error",
                W=145, H=52,
            ))

        if web_count:
            ws_sub = f"{web_count} client{'s' if web_count != 1 else ''}"
            if ws_rate and ws_rate not in ("—", ""):
                ws_sub += f" · {ws_rate} msg/s"
            deck_nodes.append(_Node(
                key="webdecks",
                title="Web Decks",
                subtitle=ws_sub,
                status="ok" if web_count else "neutral",
                W=145, H=52,
            ))

        # ── Assemble node list (xplane_webapi kept with fixed nodes) ─────────
        self._nodes = list(node_map.values()) + deck_nodes

        # ── Edges ─────────────────────────────────────────────────────────
        def _es(reachable: bool | None) -> str:
            if reachable is True:   return "ok"
            if reachable is False:  return "error"
            return "neutral"

        xp_metric = f"{dataref_rate} ref/s" if dataref_rate not in ("—", "", None) else ""
        if ws_stall_count > 0:
            xp_metric = "NO DATA"
        cockpit_addr = f"{cockpit_web_host or '127.0.0.1'}:{cockpit_web_port or '7777'}"

        ws_status = "error" if ws_stall_count > 0 else _es(cockpit_reachable if xplane_reachable else None)

        mode_str = "custom" if launcher_custom else "managed"
        self._edges = [
            _Edge("desktop", "cockpitdecks", f"starts · {mode_str}" if not launcher_running else "HTTP",
                  metric=cockpit_addr if launcher_running else "",
                  status="ok" if launcher_running else "neutral",
                  dashed=not launcher_running),
            _Edge("cockpitdecks", "xplane_webapi", "",
                  status=_es(cockpit_reachable)),
            _Edge("xplane_webapi", "xplane", "WebSocket",
                  metric=xp_metric,
                  status=ws_status,
                  bidirectional=True),
        ]
        for dn in deck_nodes:
            proto = "WebSocket" if dn.key == "webdecks" else "USB"
            self._edges.append(_Edge(
                dn.key, "cockpitdecks", proto,
                status=dn.status, bidirectional=proto == "USB",
            ))

        self.update()

    def clear_all(self) -> None:
        self._nodes = self._default_nodes()
        self._edges = self._default_edges()
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = float(self.width()), float(self.height())

        painter.fillRect(self.rect(), _CANVAS_BG)

        self._layout(W, H)
        node_map = {n.key: n for n in self._nodes}

        # Edges underneath nodes
        for edge in self._edges:
            src = node_map.get(edge.src)
            dst = node_map.get(edge.dst)
            if src and dst:
                self._draw_edge(painter, src, dst, edge)

        # Nodes on top
        for node in self._nodes:
            self._draw_node(painter, node)

        painter.end()

    # ── Layout ────────────────────────────────────────────────────────────

    def _layout(self, W: float, H: float) -> None:
        """Two-row grid layout.

        Top row (left → right): [Decks] | Cockpitdecks | xplane-webapi | X-Plane
        Bottom row:             Launcher | Desktop App (directly below Cockpitdecks)

        Anchored from bottom-left so nothing goes off-screen.
        """
        node_map = {n.key: n for n in self._nodes}
        PAD      = 32.0
        NODE_GAP = 64.0   # visible gap between node edges (not centres)
        ROW_T = H * 0.30
        ROW_B = H * 0.74

        deck_nodes = [n for n in self._nodes
                      if n.key.startswith("deck_") or n.key == "webdecks"]
        has_decks = bool(deck_nodes)

        desktop_w = node_map["desktop"].W  if "desktop"      in node_map else 150
        cockpit_w = node_map["cockpitdecks"].W if "cockpitdecks" in node_map else 170

        # ── Desktop sits below Cockpitdecks; anchor from left edge ────────
        cockpit_cx = PAD + cockpit_w / 2
        desktop_cx = cockpit_cx

        # ── If decks present, push right so the deck column fits ──────────
        if has_decks:
            deck_col_w = max(n.W for n in deck_nodes)
            min_cockpit_cx = PAD + deck_col_w + NODE_GAP + cockpit_w / 2
            if min_cockpit_cx > cockpit_cx:
                shift = min_cockpit_cx - cockpit_cx
                cockpit_cx += shift
                desktop_cx += shift

        # ── Top chain: Cockpitdecks → xplane-webapi → X-Plane ────────────
        chain_keys = ["cockpitdecks", "xplane_webapi", "xplane"]
        chain_nodes = [node_map[k] for k in chain_keys if k in node_map]
        chain_end_x = W - PAD - (chain_nodes[-1].W / 2 if chain_nodes else 0)

        if "cockpitdecks" in node_map:
            node_map["cockpitdecks"].cx = cockpit_cx
            node_map["cockpitdecks"].cy = ROW_T

        remaining = [node_map[k] for k in ["xplane_webapi", "xplane"] if k in node_map]
        if remaining:
            step = (chain_end_x - cockpit_cx) / len(remaining)
            for i, n in enumerate(remaining):
                n.cx = cockpit_cx + (i + 1) * step
                n.cy = ROW_T

        # ── Bottom row: Desktop centred under Cockpitdecks ────────────────
        if "desktop" in node_map:
            node_map["desktop"].cx = desktop_cx
            node_map["desktop"].cy = ROW_B

        # ── Left column: deck nodes, vertically centred beside top row ───
        if has_decks:
            deck_x = PAD + max(n.W for n in deck_nodes) / 2
            spacing = min(70.0, (H - 40) / max(len(deck_nodes), 1))
            total_h = len(deck_nodes) * spacing
            start_y = ROW_T - total_h / 2 + spacing / 2
            for i, dn in enumerate(deck_nodes):
                dn.cx = deck_x
                dn.cy = start_y + i * spacing

    # ── Node drawing ──────────────────────────────────────────────────────

    def _draw_node(self, painter: QPainter, node: _Node) -> None:
        rect = node.rect()
        status_color = _status_color(node.status)

        # Drop shadow
        shadow = rect.translated(2, 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 20))
        painter.drawRoundedRect(shadow, 10, 10)

        # Fill
        fill = _HUB_BG if node.hub else (_COMPONENT_BG if node.component else _NODE_BG)
        painter.setBrush(QBrush(fill))

        # Border
        if node.hub:
            border_color = _BLUE
            border_w = 2.5
        elif node.component:
            border_color = QColor("#22d3ee") if node.status == "ok" else QColor("#a5f3fc")
            border_w = 1.5
        elif node.status != "neutral":
            border_color = status_color
            border_w = 2.0
        else:
            border_color = QColor("#cbd5e1")
            border_w = 1.5

        painter.setPen(QPen(border_color, border_w))
        painter.drawRoundedRect(rect, 10, 10)

        # Status dot (top-right corner)
        dot_r = 4.5
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(status_color))
        painter.drawEllipse(
            QPointF(rect.right() - dot_r - 6, rect.top() + dot_r + 5),
            dot_r, dot_r,
        )

        # Title / subtitle / detail — layout adapts to whether detail is present
        has_detail = bool(node.detail)
        title_top = rect.top() + (6 if has_detail else 7)
        sub_top   = rect.top() + (25 if has_detail else 30)
        det_top   = rect.top() + 46

        tf = QFont()
        tf.setPointSize(10)
        tf.setWeight(QFont.Weight.DemiBold)
        painter.setFont(tf)
        painter.setPen(QPen(_BLUE if node.hub else _DARK))
        title_rect = QRectF(rect.left() + 10, title_top, rect.width() - 26, 20)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            node.title,
        )

        # Subtitle (elided)
        if node.subtitle:
            sf = QFont()
            sf.setPointSize(8)
            painter.setFont(sf)
            painter.setPen(QPen(_MUTED))
            sub_rect = QRectF(rect.left() + 10, sub_top, rect.width() - 20, 18)
            fm = QFontMetrics(sf)
            elided = fm.elidedText(
                node.subtitle, Qt.TextElideMode.ElideRight, int(sub_rect.width())
            )
            painter.drawText(
                sub_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided,
            )

        # Detail — third line (smaller, only when present)
        if has_detail:
            df = QFont()
            df.setPointSize(7)
            painter.setFont(df)
            painter.setPen(QPen(_MUTED))
            det_rect = QRectF(rect.left() + 10, det_top, rect.width() - 20, 16)
            fm2 = QFontMetrics(df)
            elided2 = fm2.elidedText(
                node.detail, Qt.TextElideMode.ElideRight, int(det_rect.width())
            )
            painter.drawText(
                det_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided2,
            )

    # ── Edge drawing ──────────────────────────────────────────────────────

    def _draw_edge(self, painter: QPainter, src: _Node, dst: _Node, edge: _Edge) -> None:
        color = _status_color(edge.status)

        sp = self._border_point(src, dst.cx, dst.cy)
        dp = self._border_point(dst, src.cx, src.cy)

        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if edge.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(*sp), QPointF(*dp))

        # Arrowhead(s)
        self._draw_arrowhead(painter, sp, dp, color)
        if edge.bidirectional:
            self._draw_arrowhead(painter, dp, sp, color)

        # Edge label (pill background so it's readable over lines)
        parts = [p for p in (edge.label, edge.metric) if p]
        if parts:
            text = "  ·  ".join(parts)
            lf = QFont()
            lf.setPointSize(8)
            painter.setFont(lf)
            fm = QFontMetrics(lf)
            mid_x = (sp[0] + dp[0]) / 2
            mid_y = (sp[1] + dp[1]) / 2
            lw = fm.horizontalAdvance(text) + 12
            lh = fm.height() + 4
            pill = QRectF(mid_x - lw / 2, mid_y - lh / 2, lw, lh)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_CANVAS_BG))
            painter.drawRoundedRect(pill, lh / 2, lh / 2)
            painter.setPen(QPen(color if edge.status != "neutral" else _MUTED))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    def _border_point(self, node: _Node, tx: float, ty: float) -> tuple[float, float]:
        """Intersection of the ray from node centre toward (tx, ty) and the node border."""
        cx, cy = node.cx, node.cy
        hw, hh = node.W / 2, node.H / 2
        dx, dy = tx - cx, ty - cy
        if dx == 0 and dy == 0:
            return cx, cy
        t = min(
            hw / abs(dx) if dx != 0 else math.inf,
            hh / abs(dy) if dy != 0 else math.inf,
        )
        return cx + dx * t, cy + dy * t

    def _draw_arrowhead(
        self,
        painter: QPainter,
        sp: tuple[float, float],
        dp: tuple[float, float],
        color: QColor,
    ) -> None:
        dx, dy = dp[0] - sp[0], dp[1] - sp[1]
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        size = 7.0
        tip = QPointF(dp[0], dp[1])
        base_x = dp[0] - ux * size
        base_y = dp[1] - uy * size
        px, py = -uy * size * 0.38, ux * size * 0.38
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawPolygon(QPolygonF([
            tip,
            QPointF(base_x + px, base_y + py),
            QPointF(base_x - px, base_y - py),
        ]))

