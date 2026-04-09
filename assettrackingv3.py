#!/usr/bin/env python3
"""
assettrackingv3.py
Hospital Asset Tracker — multi-asset RFID demo for ESP32 + RC522 readers.

Supported serial messages from Arduino:

Recommended format:
    SCAN:LEFT:AA:BB:CC:DD
    SCAN:RIGHT:11:22:33:44
    SCAN:CENTER:22:33:44:55   (optional)

Behavior:
- New RFID UIDs prompt for a name and symbol.
- You can choose not to track a tag during first scan.
- Ignored tags are remembered for the rest of the session.
- Assets stay at their last known location until scanned somewhere else.
- No default wheelchair asset is created.

Requires:
    python -m pip install PyQt6 pyserial
"""

import sys
import math
from typing import Dict, Optional, Set

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QFrame, QGraphicsView, QGraphicsScene, QGroupBox,
    QGraphicsObject, QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QListWidget, QListWidgetItem, QCheckBox
)
from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve,
    pyqtProperty
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont, QRadialGradient, QPolygonF
)

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False


PALETTE = {
    "bg": "#0D1117",
    "panel": "#161B22",
    "border": "#30363D",
    "accent": "#58A6FF",
    "accent2": "#3FB950",
    "warn": "#F78166",
    "text": "#E6EDF3",
    "subtext": "#8B949E",
    "hall_floor": "#1C2128",
    "hall_stripe": "#2D333B",
    "idle": "#D2A8FF",
}

HALLWAY = QRectF(60, 140, 780, 140)
HALL_CY = HALLWAY.top() + HALLWAY.height() / 2

POS_X = {
    "LEFT": HALLWAY.left() + 80,
    "CENTER": HALLWAY.left() + HALLWAY.width() / 2,
    "RIGHT": HALLWAY.right() - 80,
    "UNKNOWN": HALLWAY.left() + HALLWAY.width() / 2,
}

POSITION_SLOTS = {
    "LEFT": [-28, 0, 28, 56, -56],
    "CENTER": [-48, -16, 16, 48, 80, -80],
    "RIGHT": [-28, 0, 28, 56, -56],
}

DEFAULT_SYMBOLS = ["♿", "🛏", "💉", "🩺", "🦽", "🧪", "🛒", "🫀", "🩻", "📟"]


class AssetRegistrationDialog(QDialog):
    def __init__(self, uid: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Register Asset")
        self.setModal(True)
        self.setStyleSheet(f"""
            QDialog {{ background: {PALETTE['panel']}; color: {PALETTE['text']}; }}
            QLabel, QLineEdit, QComboBox, QCheckBox {{ font-family: Consolas; font-size: 11px; }}
            QLineEdit, QComboBox {{
                background: {PALETTE['bg']};
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border']};
                border-radius: 4px;
                padding: 6px;
            }}
            QCheckBox {{
                spacing: 8px;
                color: {PALETTE['text']};
            }}
            QPushButton {{
                background: {PALETTE['accent']};
                color: #0D1117;
                border: none;
                border-radius: 4px;
                padding: 7px 12px;
                font-family: Consolas;
                font-weight: bold;
            }}
        """)

        layout = QVBoxLayout(self)
        title = QLabel(f"New RFID tag detected\nUID: {uid}")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(title)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Wheelchair 1")
        self.symbol_combo = QComboBox()
        self.symbol_combo.addItems(DEFAULT_SYMBOLS)
        form.addRow("Asset name:", self.name_edit)
        form.addRow("Symbol:", self.symbol_combo)
        layout.addLayout(form)

        self.track_checkbox = QCheckBox("Track this asset")
        self.track_checkbox.setChecked(True)
        self.track_checkbox.toggled.connect(self._on_track_toggled)
        layout.addWidget(self.track_checkbox)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        trimmed = uid.replace(":", "")
        self.name_edit.setText(f"Asset {trimmed[-4:] if len(trimmed) >= 4 else trimmed}")

    def _on_track_toggled(self, checked: bool):
        self.name_edit.setEnabled(checked)
        self.symbol_combo.setEnabled(checked)

    def _accept_if_valid(self):
        if self.track_checkbox.isChecked() and not self.name_edit.text().strip():
            return
        self.accept()

    def get_data(self):
        return (
            self.name_edit.text().strip(),
            self.symbol_combo.currentText(),
            self.track_checkbox.isChecked()
        )


class AssetNode(QGraphicsObject):
    RADIUS = 24

    def __init__(self, uid: str, name: str, symbol: str):
        super().__init__()
        self.uid = uid
        self.name = name
        self.symbol = symbol
        self.position_key = "CENTER"
        self.slot_offset = 0
        self.setPos(POS_X["CENTER"], HALL_CY)
        self.setZValue(10)
        self.setToolTip(f"{self.name}\nUID: {self.uid}")

        self._anim = QPropertyAnimation(self, b"xpos", self)
        self._anim.setDuration(500)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def get_xpos(self):
        return self.x()

    def set_xpos(self, v):
        self.setX(v)

    xpos = pyqtProperty(float, fget=get_xpos, fset=set_xpos)

    def boundingRect(self) -> QRectF:
        r = self.RADIUS + 10
        return QRectF(-r, -r - 8, r * 2, r * 2 + 38)

    def paint(self, painter: QPainter, option, widget=None):
        r = self.RADIUS
        color = QColor(PALETTE["idle"])

        glow = QRadialGradient(0, 0, r + 12)
        glow.setColorAt(0, QColor(210, 168, 255, 75))
        glow.setColorAt(1, Qt.GlobalColor.transparent)
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(-r - 12, -r - 12, (r + 12) * 2, (r + 12) * 2)

        painter.setPen(QPen(color.darker(130), 2))
        grad = QRadialGradient(0, -4, r)
        grad.setColorAt(0, color.lighter(160))
        grad.setColorAt(1, color.darker(130))
        painter.setBrush(QBrush(grad))
        pts = QPolygonF([
            QPointF(0, -r), QPointF(r, 0), QPointF(0, r), QPointF(-r, 0)
        ])
        painter.drawPolygon(pts)

        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI Emoji", 15))
        painter.drawText(QRectF(-r, -r, r * 2, r * 2),
                         Qt.AlignmentFlag.AlignCenter, self.symbol)

        painter.setPen(QColor(PALETTE["text"]))
        font = QFont("Consolas", 7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(-70, r + 4, 140, 16),
                         Qt.AlignmentFlag.AlignCenter, self.name)

        painter.setPen(QColor(PALETTE["subtext"]))
        painter.setFont(QFont("Consolas", 6))
        painter.drawText(QRectF(-70, r + 18, 140, 14),
                         Qt.AlignmentFlag.AlignCenter, self.position_key)

    def update_identity(self, name: str, symbol: str):
        self.name = name
        self.symbol = symbol
        self.setToolTip(f"{self.name}\nUID: {self.uid}")
        self.update()

    def move_to(self, position_key: str, slot_offset: int = 0):
        self.position_key = position_key
        self.slot_offset = slot_offset
        target_x = POS_X.get(position_key, POS_X["CENTER"]) + slot_offset
        self._anim.stop()
        self._anim.setStartValue(float(self.x()))
        self._anim.setEndValue(float(target_x))
        self._anim.start()
        self.update()


class HallwayScene(QGraphicsScene):
    def __init__(self):
        super().__init__(0, 0, 900, 420)
        self.assets: Dict[str, AssetNode] = {}
        self._draw_hallway()
        self._draw_sensor_markers()

    def _draw_hallway(self):
        self.addRect(self.sceneRect(), QPen(Qt.PenStyle.NoPen),
                     QBrush(QColor(PALETTE["bg"]))).setZValue(-20)

        self.addRect(HALLWAY, QPen(Qt.PenStyle.NoPen),
                     QBrush(QColor(PALETTE["hall_floor"]))).setZValue(-10)

        pen_grid = QPen(QColor(PALETTE["hall_stripe"]), 0.5, Qt.PenStyle.DotLine)
        for x in range(int(HALLWAY.left()), int(HALLWAY.right()) + 1, 40):
            self.addLine(x, HALLWAY.top(), x, HALLWAY.bottom(), pen_grid).setZValue(-9)
        for y in range(int(HALLWAY.top()), int(HALLWAY.bottom()) + 1, 40):
            self.addLine(HALLWAY.left(), y, HALLWAY.right(), y, pen_grid).setZValue(-9)

        self.addRect(HALLWAY, QPen(QColor(PALETTE["border"]), 3),
                     QBrush(Qt.BrushStyle.NoBrush)).setZValue(-8)

        dash_pen = QPen(QColor("#2D333B"), 1.5, Qt.PenStyle.DashLine)
        self.addLine(HALLWAY.left() + 5, HALL_CY, HALLWAY.right() - 5, HALL_CY, dash_pen).setZValue(-7)

        for x1, x2 in [(HALLWAY.left() + 30, HALLWAY.left() + 70),
                       (HALLWAY.right() - 70, HALLWAY.right() - 30)]:
            self._arrow(x1, HALL_CY, x2, HALL_CY)

        def lbl(text, x, y, size=8, color="#58A6FF"):
            t = self.addText(text)
            t.setDefaultTextColor(QColor(color))
            t.setFont(QFont("Consolas", size))
            t.setPos(x, y)
            t.setZValue(-5)

        lbl("MAIN CORRIDOR — WARD 3", 310, HALLWAY.top() + 6)
        lbl("← Nurses' Station", 65, HALLWAY.bottom() + 8, 7, PALETTE["subtext"])
        lbl("Operating Theatre →", 618, HALLWAY.bottom() + 8, 7, PALETTE["subtext"])

    def _draw_sensor_markers(self):
        for x, tag in [(POS_X["LEFT"], "RFID\nLEFT"), (POS_X["RIGHT"], "RFID\nRIGHT")]:
            y_top = HALLWAY.top() - 56
            pen = QPen(QColor("#30363D"), 2)
            self.addLine(x, y_top + 8, x, HALLWAY.top(), pen).setZValue(-4)
            for radius in (10, 18, 26):
                arc_pen = QPen(QColor(58, 90, 160, max(20, 120 - radius * 3)), 1.5)
                self.addEllipse(x - radius, y_top - radius + 8, radius * 2, radius * 2,
                                arc_pen, QBrush(Qt.BrushStyle.NoBrush)).setZValue(-4)
            t = self.addText(tag)
            t.setDefaultTextColor(QColor(PALETTE["subtext"]))
            t.setFont(QFont("Consolas", 7))
            t.setPos(x - 22, y_top - 28)
            t.setZValue(-3)

    def _arrow(self, x1, y1, x2, y2):
        pen = QPen(QColor("#30363D"), 1.5)
        self.addLine(x1, y1, x2, y2, pen).setZValue(-6)
        angle = math.atan2(y2 - y1, x2 - x1)
        for da in (0.4, -0.4):
            self.addLine(x2, y2,
                         x2 - 8 * math.cos(angle - da),
                         y2 - 8 * math.sin(angle - da), pen).setZValue(-6)

    def ensure_asset(self, uid: str, name: str, symbol: str) -> AssetNode:
        if uid not in self.assets:
            node = AssetNode(uid, name, symbol)
            self.assets[uid] = node
            self.addItem(node)
        else:
            self.assets[uid].update_identity(name, symbol)
        return self.assets[uid]

    def asset_exists(self, uid: str) -> bool:
        return uid in self.assets

    def get_asset(self, uid: str) -> Optional[AssetNode]:
        return self.assets.get(uid)

    def move_asset(self, uid: str, position_key: str):
        node = self.assets.get(uid)
        if not node:
            return

        same_pos_count = 0
        for other_uid, other in self.assets.items():
            if other_uid == uid:
                continue
            if other.position_key == position_key:
                same_pos_count += 1

        slots = POSITION_SLOTS.get(position_key, [0])
        slot_offset = slots[min(same_pos_count, len(slots) - 1)]
        node.move_to(position_key, slot_offset)


class MapView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QBrush(QColor(PALETTE["bg"])))
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setMinimumSize(900, 440)

    def wheelEvent(self, event):
        self.scale(1.15 if event.angleDelta().y() > 0 else 1 / 1.15, 1)


class SerialWorker(QTimer):
    scan_received = pyqtSignal(str, str)  # reader, uid
    message_received = pyqtSignal(str)

    def __init__(self, port: str, baud: int = 115200):
        super().__init__()
        self._ser = None
        self._buf = ""
        try:
            self._ser = serial.Serial(port, baud, timeout=0)
            self.timeout.connect(self._read)
            self.start(100)
        except Exception as e:
            self.message_received.emit(f"Serial error: {e}")

    def _read(self):
        if not self._ser or not self._ser.is_open:
            return
        try:
            raw = self._ser.read(256).decode("utf-8", errors="ignore")
        except Exception:
            return
        self._buf += raw
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            self.message_received.emit(line)

            if line.startswith("SCAN:"):
                parts = line.split(":")
                if len(parts) >= 3:
                    reader = parts[1].strip().upper()
                    uid = ":".join(parts[2:]).strip().upper()
                    if reader in ("LEFT", "RIGHT", "CENTER") and uid:
                        self.scan_received.emit(reader, uid)

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()


class SidePanel(QWidget):
    port_connect = pyqtSignal(str)
    demo_scan = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(320)
        self.asset_meta: Dict[str, Dict[str, str]] = {}
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget   {{ background: {PALETTE['panel']}; color: {PALETTE['text']}; }}
            QLabel    {{ font-family: Consolas; color: {PALETTE['text']}; }}
            QGroupBox {{
                border: 1px solid {PALETTE['border']}; border-radius: 6px;
                margin-top: 10px; font-family: Consolas; font-size: 10px;
                color: {PALETTE['subtext']};
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QPushButton {{
                background: {PALETTE['accent']}; color: #0D1117;
                border: none; border-radius: 4px; padding: 7px;
                font-family: Consolas; font-weight: bold; font-size: 11px;
            }}
            QPushButton:hover {{ background: #79BFFF; }}
            QComboBox, QListWidget {{
                background: {PALETTE['bg']}; color: {PALETTE['text']};
                border: 1px solid {PALETTE['border']}; border-radius: 4px;
                padding: 5px 8px; font-family: Consolas;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        title = QLabel("🏥 Multi-Asset\nTracker")
        title.setStyleSheet(
            f"font-size:18px; font-weight:bold; color:{PALETTE['accent']}; font-family:Consolas;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{PALETTE['border']};")
        root.addWidget(sep)

        grp_serial = QGroupBox("ARDUINO SERIAL PORT")
        sl = QVBoxLayout(grp_serial)
        self.port_combo = QComboBox()
        self.refresh_ports()
        sl.addWidget(self.port_combo)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(
            lambda: self.port_connect.emit(self.port_combo.currentText())
        )
        sl.addWidget(self.connect_btn)
        self.serial_status = QLabel("Not connected")
        self.serial_status.setStyleSheet(f"font-size:9px; color:{PALETTE['subtext']};")
        sl.addWidget(self.serial_status)
        root.addWidget(grp_serial)

        grp_assets = QGroupBox("TRACKED ASSETS")
        al = QVBoxLayout(grp_assets)
        self.asset_list = QListWidget()
        al.addWidget(self.asset_list)
        root.addWidget(grp_assets)

        grp_demo = QGroupBox("DEMO SCANS")
        dl = QVBoxLayout(grp_demo)
        for reader, label, uid in [
            ("LEFT", "Scan demo wheelchair at LEFT", "AA:AA:AA:AA"),
            ("RIGHT", "Scan demo wheelchair at RIGHT", "AA:AA:AA:AA"),
            ("LEFT", "Scan demo IV pump at LEFT", "BB:BB:BB:BB"),
            ("RIGHT", "Scan demo bed at RIGHT", "CC:CC:CC:CC"),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, r=reader, u=uid: self.demo_scan.emit(r, u))
            dl.addWidget(btn)
        root.addWidget(grp_demo)

        self.last_event_label = QLabel("Waiting for scans...")
        self.last_event_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.last_event_label.setStyleSheet(
            f"font-size:10px; color:{PALETTE['subtext']}; font-family:Consolas;"
        )
        root.addWidget(self.last_event_label)

        hint = QLabel("Serial format:\nSCAN:LEFT:AA:BB:CC:DD")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            f"font-size:8px; color:{PALETTE['subtext']}; font-family:Consolas;"
        )
        root.addStretch()
        root.addWidget(hint)

    def refresh_ports(self):
        self.port_combo.clear()
        if SERIAL_AVAILABLE:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            self.port_combo.addItems(ports if ports else ["(no ports found)"])
        else:
            self.port_combo.addItem("install pyserial first")

    def set_connected(self, port: str):
        self.serial_status.setText(f"Connected: {port}")
        self.serial_status.setStyleSheet(f"font-size:9px; color:{PALETTE['accent2']};")
        self.connect_btn.setEnabled(False)

    def update_asset(self, uid: str, name: str, symbol: str, pos: str):
        self.asset_meta[uid] = {"name": name, "symbol": symbol, "pos": pos}
        self.asset_list.clear()
        for asset_uid, meta in sorted(self.asset_meta.items(), key=lambda kv: kv[1]["name"].lower()):
            item = QListWidgetItem(f"{meta['symbol']}  {meta['name']}   [{meta['pos']}]")
            item.setToolTip(f"UID: {asset_uid}")
            self.asset_list.addItem(item)

    def set_event_text(self, text: str):
        self.last_event_label.setText(text)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hospital Asset Tracker — RFID v3")
        self.resize(1240, 540)
        self.setStyleSheet(f"QMainWindow {{ background: {PALETTE['bg']}; }}")

        self.scene = HallwayScene()
        self.view = MapView(self.scene)
        self.panel = SidePanel()
        self._serial_worker = None
        self.ignored_uids: Set[str] = set()

        self.panel.port_connect.connect(self._connect_serial)
        self.panel.demo_scan.connect(self._handle_scan)

        central = QWidget()
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.panel)
        lay.addWidget(self.view)

        self.statusBar().setStyleSheet(
            f"background:{PALETTE['panel']}; color:{PALETTE['subtext']}; font-family:Consolas; font-size:10px;"
        )
        self.statusBar().showMessage(
            "  Connect Arduino, then scan tags. New UIDs can be named, symbolized, or ignored."
        )

    def _connect_serial(self, port: str):
        if not SERIAL_AVAILABLE:
            self.statusBar().showMessage("  Install pyserial: python -m pip install pyserial")
            return

        if not port or port.startswith("("):
            self.statusBar().showMessage("  No valid COM port selected")
            return

        if self._serial_worker:
            self._serial_worker.close()

        self._serial_worker = SerialWorker(port)
        self._serial_worker.scan_received.connect(self._handle_scan)
        self._serial_worker.message_received.connect(self._on_raw_message)

        self.panel.set_connected(port)
        self.statusBar().showMessage(f"  Listening on {port} at 115200 baud...")

    def _on_raw_message(self, line: str):
        if line.startswith("SCAN:"):
            return
        self.panel.set_event_text(line[:60])

    def _register_asset_if_needed(self, uid: str) -> bool:
        if uid in self.ignored_uids:
            return False

        if self.scene.asset_exists(uid):
            return True

        dialog = AssetRegistrationDialog(uid, self)
        if dialog.exec():
            name, symbol, track_asset = dialog.get_data()
            if not track_asset:
                self.ignored_uids.add(uid)
                self.panel.set_event_text(f"Ignoring tag {uid}")
                self.statusBar().showMessage(f"  Ignoring tag {uid}")
                return False

            self.scene.ensure_asset(uid, name, symbol)
            self.panel.update_asset(uid, name, symbol, "UNPLACED")
            return True

        # Cancel means ignore for this session too
        self.ignored_uids.add(uid)
        self.panel.set_event_text(f"Ignoring tag {uid}")
        self.statusBar().showMessage(f"  Ignoring tag {uid}")
        return False

    def _handle_scan(self, reader: str, uid: str):
        reader = reader.upper().strip()
        uid = uid.upper().strip()

        if not self._register_asset_if_needed(uid):
            return

        asset = self.scene.get_asset(uid)
        if not asset:
            return

        self.scene.move_asset(uid, reader)
        self.panel.update_asset(uid, asset.name, asset.symbol, reader)
        self.panel.set_event_text(f"{asset.name} scanned at {reader}")
        self.statusBar().showMessage(f"  {asset.name} ({uid}) detected at {reader}")

    def closeEvent(self, event):
        if self._serial_worker:
            self._serial_worker.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
