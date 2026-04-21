from __future__ import annotations

import threading
import urllib.parse

from PySide6.QtCore import QEvent, QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_desktop.services.live_apis import reload_deck


class DeviceCard(QFrame):
    reload_requested = Signal(str)

    def __init__(self, deck_data: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.deck_name = deck_data.get("name", "Unknown")
        self._base_url: str | None = None

        self.setObjectName("devicecard")
        self.setFixedHeight(122)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)

        self.name_label = QLabel(self.deck_name)
        self.name_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #1e293b;")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        self.meta_label = QLabel()
        self.meta_label.setStyleSheet("font-size: 10px; color: #64748b;")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        self.chips_row = QHBoxLayout()
        self.chips_row.setContentsMargins(0, 2, 0, 0)
        self.chips_row.setSpacing(4)
        layout.addLayout(self.chips_row)

        self.page_label = QLabel()
        self.page_label.setStyleSheet("font-size: 10px; color: #475569;")
        self.page_label.setWordWrap(True)
        layout.addWidget(self.page_label)

        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)

        self.btn_web = QPushButton("Open")
        self.btn_web.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_web.setStyleSheet(
            "QPushButton { padding: 2px 8px; border-radius: 4px; font-size: 9px; min-height: 0;"
            " color: #0369a1; border: 1px solid #bae6fd; background: #fff; }"
            "QPushButton:hover { background: #f0f9ff; }"
        )
        self.btn_web.clicked.connect(self._on_web_clicked)
        actions.addWidget(self.btn_web)

        self.btn_reload = QPushButton("Reset")
        self.btn_reload.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reload.setStyleSheet(
            "QPushButton { padding: 2px 8px; border-radius: 4px; font-size: 9px; min-height: 0;"
            " color: #1d4ed8; border: 1px solid #bfdbfe; background: #fff; }"
            "QPushButton:hover { background: #eff6ff; }"
        )
        self.btn_reload.clicked.connect(lambda: self.reload_requested.emit(self.deck_name))
        actions.addWidget(self.btn_reload)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.update_data(deck_data)

    def _clear_layout(self, layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_chip(self, text: str, style: str) -> None:
        chip = QLabel(text)
        chip.setStyleSheet(style)
        self.chips_row.addWidget(chip)

    def _on_web_clicked(self) -> None:
        if not self._base_url:
            return
        encoded_name = urllib.parse.quote(self.deck_name)
        QDesktopServices.openUrl(QUrl(f"{self._base_url.rstrip('/')}/deck/{encoded_name}"))

    def update_data(self, deck_data: dict, base_url: str | None = None) -> None:
        self._base_url = base_url
        self.deck_name = deck_data.get("name", self.deck_name)
        self.name_label.setText(self.deck_name)

        deck_type = str(deck_data.get("type", "Generic Deck"))
        serial = str(deck_data.get("serial", "")).strip()
        meta_parts = [deck_type]
        if serial and serial != "—":
            meta_parts.append(serial)
        self.meta_label.setText(" · ".join(meta_parts))

        current_page = str(deck_data.get("current_page", "")).strip()
        self.page_label.setText(f"Page: {current_page}" if current_page and current_page != "—" else "Page: —")

        self._clear_layout(self.chips_row)
        virtual = bool(deck_data.get("virtual", False))
        connected = bool(deck_data.get("connected", False))
        running = bool(deck_data.get("running", False))

        kind_text = "Virtual" if virtual else "Hardware"
        kind_style = (
            "font-size: 9px; font-weight: 600; color: #1d4ed8;"
            " background: #dbeafe; border-radius: 4px; padding: 1px 5px;"
            if virtual
            else
            "font-size: 9px; font-weight: 600; color: #92400e;"
            " background: #fef3c7; border-radius: 4px; padding: 1px 5px;"
        )
        self._add_chip(kind_text, kind_style)

        if running:
            self._add_chip(
                "Running",
                "font-size: 9px; font-weight: 600; color: #15803d;"
                " background: #dcfce7; border-radius: 4px; padding: 1px 5px;",
            )
        elif connected:
            self._add_chip(
                "Connected",
                "font-size: 9px; font-weight: 600; color: #1d4ed8;"
                " background: #dbeafe; border-radius: 4px; padding: 1px 5px;",
            )
        else:
            self._add_chip(
                "Offline",
                "font-size: 9px; font-weight: 600; color: #991b1b;"
                " background: #fee2e2; border-radius: 4px; padding: 1px 5px;",
            )
        self.chips_row.addStretch(1)

        selected_bg = "#f0fdf4" if running else "#f8fafc"
        selected_border = "#bbf7d0" if running else "#e2e8f0"
        self.setStyleSheet(
            f"QFrame#devicecard {{ background: {selected_bg}; border: 2px solid {selected_border}; border-radius: 8px; }}"
        )

        self.btn_web.setVisible(virtual)
        self.btn_reload.setVisible(True)


class DevicesTab(QWidget):
    log_line = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: dict[str, DeviceCard] = {}
        self._ordered_names: list[str] = []
        self._base_url: str | None = None

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")

        self.container = QWidget()
        self.container.setStyleSheet("background: #ffffff;")
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(16, 16, 16, 16)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.scroll.setWidget(self.container)
        self.scroll.viewport().installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._reflow_grid()
        return super().eventFilter(watched, event)

    def _grid_metrics(self) -> tuple[int, int]:
        max_columns = 4
        min_card_width = 260
        viewport_width = max(0, self.scroll.viewport().width())
        margins = self.grid.contentsMargins()
        usable_width = max(0, viewport_width - margins.left() - margins.right())
        spacing = self.grid.horizontalSpacing()
        if usable_width <= 0:
            return 1, min_card_width

        columns = min(max_columns, max(1, (usable_width + spacing) // (min_card_width + spacing)))
        card_width = max(min_card_width, (usable_width - spacing * (columns - 1)) // columns)
        return columns, card_width

    def _reflow_grid(self) -> None:
        while self.grid.count():
            child = self.grid.takeAt(0)
            if child.widget():
                child.widget().setParent(None)
        for col in range(4):
            self.grid.setColumnStretch(col, 0)
            self.grid.setColumnMinimumWidth(col, 0)
        for row in range(self.grid.rowCount() + 1):
            self.grid.setRowStretch(row, 0)

        columns, card_width = self._grid_metrics()
        row = 0
        for idx, name in enumerate(self._ordered_names):
            card = self._cards.get(name)
            if card is None:
                continue
            col = idx % columns
            if col == 0 and idx > 0:
                row += 1
            card.setFixedWidth(card_width)
            self.grid.addWidget(card, row, col, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.grid.setRowStretch(row + 1, 1)
        if columns < 4:
            self.grid.setColumnStretch(columns, 1)

    def update_decks(self, decks: list[dict], base_url: str | None = None) -> None:
        self._base_url = base_url
        ordered = [d for d in sorted(decks, key=lambda x: x.get("name", "")) if d.get("name")]
        new_names = {d["name"] for d in ordered}

        for name in list(self._cards.keys()):
            if name not in new_names:
                card = self._cards.pop(name)
                self.grid.removeWidget(card)
                card.deleteLater()

        for deck in ordered:
            name = deck["name"]
            if name in self._cards:
                self._cards[name].update_data(deck, base_url=self._base_url)
            else:
                card = DeviceCard(deck)
                card.reload_requested.connect(self._on_reload_requested)
                self._cards[name] = card
                self._cards[name].update_data(deck, base_url=self._base_url)

        self._ordered_names = [d["name"] for d in ordered]
        self._reflow_grid()

    def _on_reload_requested(self, name: str) -> None:
        self.log_line.emit(f"Requesting reload for deck: {name}")

        def work() -> None:
            ok, msg = reload_deck(name)
            self.log_line.emit(f"Reload {name}: {msg}")

        threading.Thread(target=work, name=f"ReloadDeck-{name}", daemon=True).start()
