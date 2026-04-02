from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QColor, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
    QSizePolicy,
)

from cockpitdecks_desktop.services.live_apis import reload_deck


class DeviceCard(QFrame):
    """A glassmorphism-styled card representing a single deck."""

    reload_requested = Signal(str)

    def __init__(self, deck_data: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.deck_name = deck_data.get("name", "Unknown")
        self.setFixedWidth(280)
        self.setMinimumHeight(160)

        # ── Glassmorphism Style ──
        # Semi-transparent background with a thin light border
        self.setStyleSheet(f"""
            DeviceCard {{
                background-color: rgba(255, 255, 255, 140);
                border: 1px solid rgba(255, 255, 255, 180);
                border-radius: 16px;
            }}
        """)

        # Drop shadow for "lifted" look
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 30))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        # ── Header (Icon + Name) ──
        header = QHBoxLayout()
        header.setSpacing(10)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_icon(deck_data.get("type", ""))
        header.addWidget(self.icon_label)

        name_layout = QVBoxLayout()
        name_layout.setSpacing(0)
        self.name_label = QLabel(self.deck_name)
        nf = QFont()
        nf.setPointSize(14)
        nf.setWeight(QFont.Weight.Bold)
        self.name_label.setFont(nf)
        self.name_label.setStyleSheet("color: #1e293b; border: none; background: transparent;")
        
        self.type_label = QLabel(deck_data.get("type", "Generic Deck"))
        tf = QFont()
        tf.setPointSize(10)
        self.type_label.setFont(tf)
        self.type_label.setStyleSheet("color: #64748b; border: none; background: transparent;")
        
        name_layout.addWidget(self.name_label)
        name_layout.addWidget(self.type_label)
        header.addLayout(name_layout)
        header.addStretch(1)

        # Status dot
        self.status_dot = QFrame()
        self.status_dot.setFixedSize(10, 10)
        self.status_dot.setStyleSheet("border-radius: 5px;")
        self._update_status(deck_data.get("connected", False), deck_data.get("running", False))
        header.addWidget(self.status_dot)

        layout.addLayout(header)

        # ── Body (Details) ──
        details = QGridLayout()
        details.setSpacing(6)

        def _label(text: str):
            l = QLabel(text)
            l.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600; border: none; background: transparent;")
            return l

        def _value(text: str):
            v = QLabel(text)
            v.setStyleSheet("color: #334155; font-size: 11px; border: none; background: transparent;")
            v.setWordWrap(True)
            return v

        details.addWidget(_label("Serial:"), 0, 0)
        self.serial_val = _value(str(deck_data.get("serial", "—")))
        details.addWidget(self.serial_val, 0, 1)

        details.addWidget(_label("Page:"), 1, 0)
        self.page_val = _value(str(deck_data.get("current_page", "—")))
        details.addWidget(self.page_val, 1, 1)

        layout.addLayout(details)
        layout.addStretch(1)

        # ── Footer (Actions) ──
        actions = QHBoxLayout()
        self.btn_web = QPushButton("Open in Browser")
        self.btn_web.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_web.setStyleSheet("""
            QPushButton {
                background-color: rgba(16, 185, 129, 200);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(5, 150, 105, 255);
            }
            QPushButton:pressed {
                background-color: rgba(4, 120, 87, 255);
            }
        """)
        self.btn_web.clicked.connect(self._on_web_clicked)
        self.btn_web.setVisible(deck_data.get("virtual", False))

        self.btn_reload = QPushButton("Reset Deck")
        self.btn_reload.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reload.setStyleSheet("""
            QPushButton {
                background-color: rgba(37, 99, 235, 200);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(29, 78, 216, 255);
            }
            QPushButton:pressed {
                background-color: rgba(30, 64, 175, 255);
            }
        """)
        self.btn_reload.clicked.connect(lambda: self.reload_requested.emit(self.deck_name))
        
        actions.addWidget(self.btn_web)
        actions.addStretch(1)
        actions.addWidget(self.btn_reload)
        layout.addLayout(actions)

    def _on_web_clicked(self):
        if hasattr(self, "_base_url") and self._base_url:
            import urllib.parse
            encoded_name = urllib.parse.quote(self.deck_name)
            url = f"{self._base_url.rstrip('/')}/deck/{encoded_name}"
            QDesktopServices.openUrl(QUrl(url))

    def _update_status(self, connected: bool, running: bool):
        if running:
            color = "#22c55e"  # Green
        elif connected:
            color = "#3b82f6"  # Blue
        else:
            color = "#ef4444"  # Red
        self.status_dot.setStyleSheet(f"background-color: {color}; border-radius: 5px;")

    def _update_icon(self, deck_type: str):
        # Using simple text labels instead of emojis to avoid macOS rendering crashes
        dt = deck_type.lower()
        if "streamdeck" in dt or "stream deck" in dt:
            icon = "SD"
        elif "loupedeck" in dt:
            icon = "LD"
        elif "virtual" in dt or "web" in dt:
            icon = "WEB"
        else:
            icon = "DK"
        self.icon_label.setText(icon)
        self.icon_label.setStyleSheet("""
            font-size: 10px; 
            font-weight: 800; 
            color: #1e293b; 
            background: rgba(0, 0, 0, 0.05); 
            border-radius: 4px;
            border: none;
        """)

    def update_data(self, deck_data: dict, base_url: str | None = None):
        self._base_url = base_url
        self.btn_web.setVisible(deck_data.get("virtual", False))
        self._update_status(deck_data.get("connected", False), deck_data.get("running", False))
        self.serial_val.setText(str(deck_data.get("serial", "—")))
        self.page_val.setText(str(deck_data.get("current_page", "—")))
        self.type_label.setText(deck_data.get("type", "Generic Deck"))


class DevicesTab(QWidget):
    """The central tab showing all connected controllers."""

    log_line = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: dict[str, DeviceCard] = {}

        # Content Layout
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")

        self.container = QWidget()
        self.container.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                stop:0 #f1f5f9, stop:0.5 #e2e8f0, stop:1 #cbd5e1);
        """)
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(24, 24, 24, 24)
        self.grid.setSpacing(20)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.scroll.setWidget(self.container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)

    def update_decks(self, decks: list[dict], base_url: str | None = None):
        """Refresh the grid from live deck data."""
        self._base_url = base_url
        new_names = {d.get("name") for d in decks if d.get("name")}
        
        # Remove old cards
        to_remove = set(self._cards.keys()) - new_names
        for name in to_remove:
            card = self._cards.pop(name)
            self.grid.removeWidget(card)
            card.deleteLater()

        # Update or add new cards
        for i, deck in enumerate(sorted(decks, key=lambda x: x.get("name", ""))):
            name = deck.get("name")
            if not name:
                continue
            
            if name in self._cards:
                self._cards[name].update_data(deck, base_url=self._base_url)
            else:
                card = DeviceCard(deck)
                card._base_url = self._base_url
                card.reload_requested.connect(self._on_reload_requested)
                self._cards[name] = card
                
            # Re-flow in grid (4 columns)
            row, col = divmod(i, 4)
            self.grid.addWidget(self._cards[name], row, col)

    def _on_reload_requested(self, name: str):
        self.log_line.emit(f"Requesting reload for deck: {name}")

        def work() -> None:
            ok, msg = reload_deck(name)
            self.log_line.emit(f"Reload {name}: {msg}")

        threading.Thread(target=work, name=f"ReloadDeck-{name}", daemon=True).start()
