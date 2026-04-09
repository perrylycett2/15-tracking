#!/usr/bin/env python3
"""
assettrackingv5.py
Hospital Asset Tracker — aligned pilot hallway + multi-segment hospital demo.

What this version adds:
- The working RFID pilot corridor is aligned to a real hallway on the attached hospital floorplan.
- Multiple additional hospital hallway segments are shown so the rollout looks like a full deployment,
  not just a mirrored strip.
- Assets still respond to LEFT / CENTER / RIGHT RFID scans in the detailed pilot view.
- Assets can also be routed through additional hospital segments for presentation/demo purposes.
- A movement history is kept for every asset and shown in the UI.

Requires:
    python -m pip install PyQt6 pyserial
"""

import sys
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QFrame, QGraphicsView, QGraphicsScene, QGroupBox,
    QGraphicsObject, QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QListWidget, QListWidgetItem, QCheckBox, QSplitter, QGridLayout
)
from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve,
    pyqtProperty
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont, QRadialGradient, QPolygonF, QPixmap,
    QPainterPath
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
    "pilot_fill": "#4EA1FF",
    "pilot_outline": "#84C2FF",
    "future_fill": "#8B949E",
    "sensor": "#3FB950",
    "route_fill": "#B5BAC1",
    "route_outline": "#6B7280",
    "history": "#F2CC60",
}

# --- Detailed pilot corridor geometry ----------------------------------------

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


@dataclass(frozen=True)
class SegmentSpec:
    name: str
    rect: QRectF
    checkpoints: Dict[str, QPointF]
    active: bool = False
    label_offset: Tuple[float, float] = (10, -34)


# These corridor overlays are positioned directly over visible hallways in the
# uploaded floorplan image. The pilot hallway is the long horizontal corridor in
# the lower-left / center portion of the hospital map.
SEGMENTS: Dict[str, SegmentSpec] = {
    "PILOT": SegmentSpec(
        name="Pilot Hallway",
        rect=QRectF(257, 493, 575, 64),
        checkpoints={
            "LEFT": QPointF(308, 525),
            "CENTER": QPointF(542, 525),
            "RIGHT": QPointF(775, 525),
        },
        active=True,
        label_offset=(12, -54),
    ),
    "WEST_WING": SegmentSpec(
        name="West Wing Spine",
        rect=QRectF(170, 255, 52, 350),
        checkpoints={
            "NORTH": QPointF(196, 290),
            "MID": QPointF(196, 428),
            "SOUTH": QPointF(196, 570),
        },
        label_offset=(18, -22),
    ),
    "EAST_SPINE": SegmentSpec(
        name="East Main Spine",
        rect=QRectF(845, 135, 58, 690),
        checkpoints={
            "NORTH": QPointF(874, 185),
            "MID": QPointF(874, 474),
            "SOUTH": QPointF(874, 778),
        },
        label_offset=(20, -26),
    ),
    "EAST_LOOP": SegmentSpec(
        name="East Treatment Loop",
        rect=QRectF(930, 176, 315, 56),
        checkpoints={
            "WEST": QPointF(968, 204),
            "MID": QPointF(1089, 204),
            "EAST": QPointF(1206, 204),
        },
        label_offset=(10, -26),
    ),
    "SOUTH_CLINICS": SegmentSpec(
        name="South Clinics Corridor",
        rect=QRectF(915, 905, 530, 60),
        checkpoints={
            "WEST": QPointF(965, 935),
            "MID": QPointF(1176, 935),
            "EAST": QPointF(1387, 935),
        },
        label_offset=(10, 68),
    ),
}

HOSPITAL_POSITION_SLOTS = {
    "LEFT": [QPointF(0, 0), QPointF(-14, -16), QPointF(14, 16), QPointF(-24, 18), QPointF(24, -18)],
    "CENTER": [QPointF(0, 0), QPointF(-18, -16), QPointF(18, 16), QPointF(32, -18), QPointF(-32, 18)],
    "RIGHT": [QPointF(0, 0), QPointF(-14, -16), QPointF(14, 16), QPointF(-24, 18), QPointF(24, -18)],
    "NORTH": [QPointF(0, 0), QPointF(-16, 12), QPointF(16, -12), QPointF(-22, 26), QPointF(22, -26)],
    "MID": [QPointF(0, 0), QPointF(-16, -12), QPointF(16, 12), QPointF(28, -16), QPointF(-28, 16)],
    "SOUTH": [QPointF(0, 0), QPointF(-16, 12), QPointF(16, -12), QPointF(-22, 26), QPointF(22, -26)],
    "WEST": [QPointF(0, 0), QPointF(12, -16), QPointF(-12, 16), QPointF(24, -24), QPointF(-24, 24)],
    "EAST": [QPointF(0, 0), QPointF(12, -16), QPointF(-12, 16), QPointF(24, -24), QPointF(-24, 24)],
}

ROUTING_TARGETS = {
    "Pilot Hallway — LEFT": ("PILOT", "LEFT"),
    "Pilot Hallway — CENTER": ("PILOT", "CENTER"),
    "Pilot Hallway — RIGHT": ("PILOT", "RIGHT"),
    "West Wing Spine — NORTH": ("WEST_WING", "NORTH"),
    "West Wing Spine — MID": ("WEST_WING", "MID"),
    "West Wing Spine — SOUTH": ("WEST_WING", "SOUTH"),
    "East Main Spine — NORTH": ("EAST_SPINE", "NORTH"),
    "East Main Spine — MID": ("EAST_SPINE", "MID"),
    "East Main Spine — SOUTH": ("EAST_SPINE", "SOUTH"),
    "East Treatment Loop — WEST": ("EAST_LOOP", "WEST"),
    "East Treatment Loop — MID": ("EAST_LOOP", "MID"),
    "East Treatment Loop — EAST": ("EAST_LOOP", "EAST"),
    "South Clinics Corridor — WEST": ("SOUTH_CLINICS", "WEST"),
    "South Clinics Corridor — MID": ("SOUTH_CLINICS", "MID"),
    "South Clinics Corridor — EAST": ("SOUTH_CLINICS", "EAST"),
}


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
        pts = QPolygonF([QPointF(0, -r), QPointF(r, 0), QPointF(0, r), QPointF(-r, 0)])
        painter.drawPolygon(pts)

        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI Emoji", 15))
        painter.drawText(QRectF(-r, -r, r * 2, r * 2), Qt.AlignmentFlag.AlignCenter, self.symbol)

        painter.setPen(QColor(PALETTE["text"]))
        font = QFont("Consolas", 7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(-70, r + 4, 140, 16), Qt.AlignmentFlag.AlignCenter, self.name)

        painter.setPen(QColor(PALETTE["subtext"]))
        painter.setFont(QFont("Consolas", 6))
        painter.drawText(QRectF(-70, r + 18, 140, 14), Qt.AlignmentFlag.AlignCenter, self.position_key)

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


class HospitalAssetMarker(QGraphicsObject):
    def __init__(self, uid: str, name: str, symbol: str):
        super().__init__()
        self.uid = uid
        self.name = name
        self.symbol = symbol
        self.segment_key = "PILOT"
        self.checkpoint_key = "CENTER"
        self._pos = QPointF(SEGMENTS["PILOT"].checkpoints["CENTER"])
        self.setPos(self._pos)
        self.setZValue(40)
        self.setToolTip(f"{self.name}\nUID: {self.uid}")

        self._anim = QPropertyAnimation(self, b"marker_pos", self)
        self._anim.setDuration(550)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def boundingRect(self) -> QRectF:
        return QRectF(-48, -44, 96, 96)

    def get_marker_pos(self):
        return self.pos()

    def set_marker_pos(self, point):
        self.setPos(point)

    marker_pos = pyqtProperty(QPointF, fget=get_marker_pos, fset=set_marker_pos)

    def update_identity(self, name: str, symbol: str):
        self.name = name
        self.symbol = symbol
        self.setToolTip(f"{self.name}\nUID: {self.uid}")
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        halo = QRadialGradient(0, 0, 34)
        halo.setColorAt(0, QColor(88, 166, 255, 90))
        halo.setColorAt(1, Qt.GlobalColor.transparent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(-34, -34, 68, 68)

        painter.setPen(QPen(QColor(PALETTE["accent"]).darker(130), 2))
        painter.setBrush(QColor(PALETTE["accent"]))
        painter.drawEllipse(-12, -12, 24, 24)

        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI Emoji", 11))
        painter.drawText(QRectF(-15, -16, 30, 30), Qt.AlignmentFlag.AlignCenter, self.symbol)

        painter.setPen(QColor(PALETTE["text"]))
        font = QFont("Consolas", 7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(-48, 16, 96, 14), Qt.AlignmentFlag.AlignCenter, self.name)

        painter.setPen(QColor(PALETTE["subtext"]))
        painter.setFont(QFont("Consolas", 6))
        painter.drawText(QRectF(-48, 28, 96, 12), Qt.AlignmentFlag.AlignCenter, self.checkpoint_key)

    def move_to(self, segment_key: str, checkpoint_key: str, offset: QPointF):
        self.segment_key = segment_key
        self.checkpoint_key = checkpoint_key
        base = SEGMENTS[segment_key].checkpoints[checkpoint_key]
        target = base + offset
        self._anim.stop()
        self._anim.setStartValue(QPointF(self.pos()))
        self._anim.setEndValue(QPointF(target))
        self._anim.start()
        self.update()


class HallwayScene(QGraphicsScene):
    def __init__(self):
        super().__init__(0, 0, 900, 420)
        self.assets: Dict[str, AssetNode] = {}
        self._draw_hallway()
        self._draw_sensor_markers()

    def _draw_hallway(self):
        self.addRect(self.sceneRect(), QPen(Qt.PenStyle.NoPen), QBrush(QColor(PALETTE["bg"]))).setZValue(-20)
        self.addRect(HALLWAY, QPen(Qt.PenStyle.NoPen), QBrush(QColor(PALETTE["hall_floor"]))).setZValue(-10)

        pen_grid = QPen(QColor(PALETTE["hall_stripe"]), 0.5, Qt.PenStyle.DotLine)
        for x in range(int(HALLWAY.left()), int(HALLWAY.right()) + 1, 40):
            self.addLine(x, HALLWAY.top(), x, HALLWAY.bottom(), pen_grid).setZValue(-9)
        for y in range(int(HALLWAY.top()), int(HALLWAY.bottom()) + 1, 40):
            self.addLine(HALLWAY.left(), y, HALLWAY.right(), y, pen_grid).setZValue(-9)

        self.addRect(HALLWAY, QPen(QColor(PALETTE["border"]), 3), QBrush(Qt.BrushStyle.NoBrush)).setZValue(-8)

        dash_pen = QPen(QColor("#2D333B"), 1.5, Qt.PenStyle.DashLine)
        self.addLine(HALLWAY.left() + 5, HALL_CY, HALLWAY.right() - 5, HALL_CY, dash_pen).setZValue(-7)

        for x1, x2 in [(HALLWAY.left() + 30, HALLWAY.left() + 70), (HALLWAY.right() - 70, HALLWAY.right() - 30)]:
            self._arrow(x1, HALL_CY, x2, HALL_CY)

        def lbl(text, x, y, size=8, color="#58A6FF"):
            t = self.addText(text)
            t.setDefaultTextColor(QColor(color))
            t.setFont(QFont("Consolas", size))
            t.setPos(x, y)
            t.setZValue(-5)

        lbl("ACTIVE RFID PILOT HALLWAY", 302, HALLWAY.top() + 6)
        lbl("← LEFT reader", 78, HALLWAY.bottom() + 8, 7, PALETTE["subtext"])
        lbl("CENTER reader", 400, HALLWAY.bottom() + 8, 7, PALETTE["subtext"])
        lbl("RIGHT reader →", 680, HALLWAY.bottom() + 8, 7, PALETTE["subtext"])

    def _draw_sensor_markers(self):
        for x, tag in [(POS_X["LEFT"], "RFID\nLEFT"), (POS_X["CENTER"], "RFID\nCENTER"), (POS_X["RIGHT"], "RFID\nRIGHT")]:
            y_top = HALLWAY.top() - 56
            pen = QPen(QColor("#30363D"), 2)
            self.addLine(x, y_top + 8, x, HALLWAY.top(), pen).setZValue(-4)
            for radius in (10, 18, 26):
                arc_pen = QPen(QColor(58, 90, 160, max(20, 120 - radius * 3)), 1.5)
                self.addEllipse(x - radius, y_top - radius + 8, radius * 2, radius * 2, arc_pen, QBrush(Qt.BrushStyle.NoBrush)).setZValue(-4)
            t = self.addText(tag)
            t.setDefaultTextColor(QColor(PALETTE["subtext"]))
            t.setFont(QFont("Consolas", 7))
            t.setPos(x - 24, y_top - 28)
            t.setZValue(-3)

    def _arrow(self, x1, y1, x2, y2):
        pen = QPen(QColor("#30363D"), 1.5)
        self.addLine(x1, y1, x2, y2, pen).setZValue(-6)
        angle = math.atan2(y2 - y1, x2 - x1)
        for da in (0.4, -0.4):
            self.addLine(x2, y2, x2 - 8 * math.cos(angle - da), y2 - 8 * math.sin(angle - da), pen).setZValue(-6)

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
            if other_uid != uid and other.position_key == position_key:
                same_pos_count += 1

        slots = POSITION_SLOTS.get(position_key, [0])
        slot_offset = slots[min(same_pos_count, len(slots) - 1)]
        node.move_to(position_key, slot_offset)


class HospitalOverviewScene(QGraphicsScene):
    def __init__(self, floorplan_path: Optional[Path]):
        self.floorplan_path = floorplan_path
        self.assets: Dict[str, HospitalAssetMarker] = {}
        self.loaded_pixmap = QPixmap(str(floorplan_path)) if floorplan_path and floorplan_path.exists() else QPixmap()
        if not self.loaded_pixmap.isNull():
            super().__init__(0, 0, self.loaded_pixmap.width(), self.loaded_pixmap.height())
        else:
            super().__init__(0, 0, 1945, 1526)
        self._draw_background()
        self._draw_segment_overlay()

    def _draw_background(self):
        self.addRect(self.sceneRect(), QPen(Qt.PenStyle.NoPen), QBrush(QColor("#F2F4F7"))).setZValue(-100)
        if not self.loaded_pixmap.isNull():
            item = self.addPixmap(self.loaded_pixmap)
            item.setOpacity(0.96)
            item.setZValue(-90)
        else:
            t = self.addText("Floorplan image not found.\nPlace the PNG beside this script.")
            t.setDefaultTextColor(QColor(PALETTE["warn"]))
            t.setFont(QFont("Consolas", 16))
            t.setPos(60, 60)
            t.setZValue(-80)

    def _add_segment_shape(self, spec: SegmentSpec):
        path = QPainterPath()
        path.addRoundedRect(spec.rect, 18, 18)
        outline_color = QColor(PALETTE["pilot_outline"] if spec.active else PALETTE["route_outline"])
        fill_color = QColor(78, 161, 255, 145) if spec.active else QColor(107, 114, 128, 34)
        item = self.addPath(path, QPen(outline_color, 4 if spec.active else 2, Qt.PenStyle.DashLine if not spec.active else Qt.PenStyle.SolidLine), QBrush(fill_color))
        item.setZValue(8 if spec.active else 4)

        label = self.addText(("ACTIVE RFID PILOT\n" if spec.active else "ROLLOUT SEGMENT\n") + spec.name)
        label.setDefaultTextColor(QColor("#0B203A") if spec.active else QColor("#39424E"))
        font = QFont("Consolas", 10)
        font.setBold(True)
        label.setFont(font)
        label.setPos(spec.rect.left() + spec.label_offset[0], spec.rect.top() + spec.label_offset[1])
        label.setZValue(9)

        for checkpoint, pt in spec.checkpoints.items():
            self.addEllipse(pt.x() - 8, pt.y() - 8, 16, 16,
                            QPen(QColor(PALETTE["sensor"]).darker(120), 2),
                            QBrush(QColor(PALETTE["sensor"]))).setZValue(10)
            txt = self.addText(checkpoint)
            txt.setDefaultTextColor(QColor("#204B2E"))
            txt.setFont(QFont("Consolas", 7))
            txt.setPos(pt.x() - 16, pt.y() + 10)
            txt.setZValue(11)

    def _draw_segment_overlay(self):
        for spec in SEGMENTS.values():
            self._add_segment_shape(spec)

        legend = self.addText(
            "Hospital-wide deployment demo:\n"
            "• solid blue corridor = active RFID pilot hallway aligned to real floorplan corridor\n"
            "• dashed grey corridors = additional rollout segments\n"
            "• markers show last known asset position\n"
            "• movement history is logged in the side panel"
        )
        legend.setDefaultTextColor(QColor("#223142"))
        legend.setFont(QFont("Consolas", 9))
        legend.setPos(35, self.sceneRect().height() - 126)
        legend.setZValue(20)

    def ensure_asset(self, uid: str, name: str, symbol: str):
        if uid not in self.assets:
            marker = HospitalAssetMarker(uid, name, symbol)
            self.assets[uid] = marker
            self.addItem(marker)
        else:
            self.assets[uid].update_identity(name, symbol)

    def update_asset(self, uid: str, name: str, symbol: str, segment_key: str, checkpoint_key: str):
        self.ensure_asset(uid, name, symbol)
        marker = self.assets[uid]

        same_pos_count = 0
        for other_uid, other in self.assets.items():
            if other_uid != uid and other.segment_key == segment_key and other.checkpoint_key == checkpoint_key:
                same_pos_count += 1

        offsets = HOSPITAL_POSITION_SLOTS.get(checkpoint_key, [QPointF(0, 0)])
        offset = offsets[min(same_pos_count, len(offsets) - 1)]
        marker.move_to(segment_key, checkpoint_key, offset)

    def reset_view_rect(self) -> QRectF:
        return self.sceneRect().adjusted(-25, -25, 25, 25)


class MapView(QGraphicsView):
    def __init__(self, scene, min_size: Tuple[int, int] = (900, 440)):
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform |
                            QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QBrush(QColor(PALETTE["bg"])))
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setMinimumSize(*min_size)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._zoom = 0

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            factor = 1.15
            self._zoom += 1
        else:
            factor = 1 / 1.15
            self._zoom -= 1
        self.scale(factor, factor)

    def mouseDoubleClickEvent(self, event):
        self.fit_scene()
        super().mouseDoubleClickEvent(event)

    def fit_scene(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 0


class SerialWorker(QTimer):
    scan_received = pyqtSignal(str, str)
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
    reset_views_requested = pyqtSignal()
    route_asset_requested = pyqtSignal(str, str, str)
    history_filter_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(390)
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

        title = QLabel("🏥 Full-Hospital\nAsset Tracker v5")
        title.setStyleSheet(f"font-size:18px; font-weight:bold; color:{PALETTE['accent']}; font-family:Consolas;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        subtitle = QLabel("Real hallway alignment + multi-segment rollout + history")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"font-size:9px; color:{PALETTE['subtext']}; font-family:Consolas;")
        root.addWidget(subtitle)

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
        self.connect_btn.clicked.connect(lambda: self.port_connect.emit(self.port_combo.currentText()))
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

        grp_demo = QGroupBox("RFID PILOT DEMO SCANS")
        grid = QGridLayout(grp_demo)
        demo_buttons = [
            ("Wheelchair @ LEFT", "LEFT", "AA:AA:AA:AA"),
            ("Wheelchair @ CENTER", "CENTER", "AA:AA:AA:AA"),
            ("Wheelchair @ RIGHT", "RIGHT", "AA:AA:AA:AA"),
            ("IV Pump @ LEFT", "LEFT", "BB:BB:BB:BB"),
            ("IV Pump @ CENTER", "CENTER", "BB:BB:BB:BB"),
            ("Bed @ RIGHT", "RIGHT", "CC:CC:CC:CC"),
        ]
        for i, (label, reader, uid) in enumerate(demo_buttons):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, r=reader, u=uid: self.demo_scan.emit(r, u))
            grid.addWidget(btn, i // 2, i % 2)
        root.addWidget(grp_demo)

        grp_route = QGroupBox("HOSPITAL ROLLOUT DEMO")
        rl = QVBoxLayout(grp_route)
        self.route_asset_combo = QComboBox()
        self.route_asset_combo.addItem("Select asset")
        rl.addWidget(self.route_asset_combo)
        self.route_target_combo = QComboBox()
        self.route_target_combo.addItems(list(ROUTING_TARGETS.keys()))
        rl.addWidget(self.route_target_combo)
        route_btn = QPushButton("Route Asset To Selected Segment")
        route_btn.clicked.connect(self._emit_route_request)
        rl.addWidget(route_btn)
        root.addWidget(grp_route)

        grp_history = QGroupBox("MOVEMENT HISTORY")
        hl = QVBoxLayout(grp_history)
        self.history_filter_combo = QComboBox()
        self.history_filter_combo.addItems(["All assets"])
        self.history_filter_combo.currentTextChanged.connect(self.history_filter_changed.emit)
        hl.addWidget(self.history_filter_combo)
        self.history_list = QListWidget()
        hl.addWidget(self.history_list)
        root.addWidget(grp_history, 1)

        reset_btn = QPushButton("Reset / Auto-Fit Views")
        reset_btn.clicked.connect(self.reset_views_requested.emit)
        root.addWidget(reset_btn)

        self.last_event_label = QLabel("Waiting for scans...")
        self.last_event_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.last_event_label.setStyleSheet(f"font-size:10px; color:{PALETTE['subtext']}; font-family:Consolas;")
        root.addWidget(self.last_event_label)

        hint = QLabel("Serial format:\nSCAN:LEFT:AA:BB:CC:DD\nSCAN:CENTER:AA:BB:CC:DD\nSCAN:RIGHT:AA:BB:CC:DD")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"font-size:8px; color:{PALETTE['subtext']}; font-family:Consolas;")
        root.addWidget(hint)

    def _emit_route_request(self):
        uid = self.route_asset_combo.currentData()
        target = self.route_target_combo.currentText()
        if not uid or target not in ROUTING_TARGETS:
            return
        segment_key, checkpoint_key = ROUTING_TARGETS[target]
        self.route_asset_requested.emit(uid, segment_key, checkpoint_key)

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
        self.route_asset_combo.clear()
        self.route_asset_combo.addItem("Select asset")
        self.history_filter_combo.blockSignals(True)
        selected_filter = self.history_filter_combo.currentText() if self.history_filter_combo.count() else "All assets"
        self.history_filter_combo.clear()
        self.history_filter_combo.addItem("All assets")
        for asset_uid, meta in sorted(self.asset_meta.items(), key=lambda kv: kv[1]["name"].lower()):
            item = QListWidgetItem(f"{meta['symbol']}  {meta['name']}   [{meta['pos']}]")
            item.setToolTip(f"UID: {asset_uid}")
            self.asset_list.addItem(item)
            self.route_asset_combo.addItem(f"{meta['symbol']} {meta['name']}", asset_uid)
            self.history_filter_combo.addItem(meta['name'])
        idx = self.history_filter_combo.findText(selected_filter)
        self.history_filter_combo.setCurrentIndex(max(0, idx))
        self.history_filter_combo.blockSignals(False)

    def set_event_text(self, text: str):
        self.last_event_label.setText(text)

    def refresh_history(self, rows: List[str]):
        self.history_list.clear()
        for row in rows:
            self.history_list.addItem(QListWidgetItem(row))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hospital Asset Tracker — Full Hospital Demo v5")
        self.resize(1640, 960)
        self.setStyleSheet(f"QMainWindow {{ background: {PALETTE['bg']}; }}")

        self.floorplan_path = self._find_floorplan_image()
        self.hospital_scene = HospitalOverviewScene(self.floorplan_path)
        self.pilot_scene = HallwayScene()
        self.hospital_view = MapView(self.hospital_scene, min_size=(1000, 560))
        self.pilot_view = MapView(self.pilot_scene, min_size=(1000, 280))
        self.panel = SidePanel()

        self._serial_worker = None
        self.ignored_uids: Set[str] = set()
        self.asset_names: Dict[str, str] = {}
        self.asset_symbols: Dict[str, str] = {}
        self.asset_history: Dict[str, List[str]] = {}

        self.panel.port_connect.connect(self._connect_serial)
        self.panel.demo_scan.connect(self._handle_scan)
        self.panel.reset_views_requested.connect(self._reset_views)
        self.panel.route_asset_requested.connect(self._route_asset)
        self.panel.history_filter_changed.connect(self._refresh_history_view)

        central = QWidget()
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.panel)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {PALETTE['border']}; }}")
        splitter.addWidget(self._wrap_view("Hospital-wide rollout aligned to the uploaded floorplan", self.hospital_view))
        splitter.addWidget(self._wrap_view("Detailed working RFID pilot hallway", self.pilot_view))
        splitter.setSizes([640, 300])
        lay.addWidget(splitter)

        self.statusBar().setStyleSheet(f"background:{PALETTE['panel']}; color:{PALETTE['subtext']}; font-family:Consolas; font-size:10px;")
        if self.floorplan_path:
            self.statusBar().showMessage(f"  Loaded floorplan: {self.floorplan_path.name}. Double-click a view to auto-fit it.")
        else:
            self.statusBar().showMessage("  Floorplan image not found. Put the hospital PNG beside this script and restart.")

        QTimer.singleShot(100, self._reset_views)

    def _wrap_view(self, title: str, view: QGraphicsView) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(10, 10, 10, 10)
        l.setSpacing(8)
        hdr = QLabel(title)
        hdr.setStyleSheet(f"font-size:12px; font-weight:bold; color:{PALETTE['accent']}; font-family:Consolas;")
        l.addWidget(hdr)
        l.addWidget(view, 1)
        return w

    def _find_floorplan_image(self) -> Optional[Path]:
        script_dir = Path(__file__).resolve().parent
        candidates = [
            script_dir / "SJH_Floorplan_SOUTH_cropped.png",
            script_dir / "SJH Floorplan with Scale Bar-SOUTH.png",
            script_dir / "floorplan_crop.png",
            script_dir / "floorplan_render.png",
            script_dir / "SJH Floorplan with Scale Bar-SOUTH.jpg",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _reset_views(self):
        self.hospital_view.fitInView(self.hospital_scene.reset_view_rect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.pilot_view.fit_scene()

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
        if self.pilot_scene.asset_exists(uid):
            return True

        dialog = AssetRegistrationDialog(uid, self)
        if dialog.exec():
            name, symbol, track_asset = dialog.get_data()
            if not track_asset:
                self.ignored_uids.add(uid)
                self.panel.set_event_text(f"Ignoring tag {uid}")
                self.statusBar().showMessage(f"  Ignoring tag {uid}")
                return False

            self.asset_names[uid] = name
            self.asset_symbols[uid] = symbol
            self.asset_history[uid] = []
            self.pilot_scene.ensure_asset(uid, name, symbol)
            self.hospital_scene.ensure_asset(uid, name, symbol)
            self.panel.update_asset(uid, name, symbol, "UNPLACED")
            self._append_history(uid, f"registered {name} for tracking")
            return True

        self.ignored_uids.add(uid)
        self.panel.set_event_text(f"Ignoring tag {uid}")
        self.statusBar().showMessage(f"  Ignoring tag {uid}")
        return False

    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _append_history(self, uid: str, detail: str):
        name = self.asset_names.get(uid, uid)
        entry = f"[{self._timestamp()}] {name}: {detail}"
        self.asset_history.setdefault(uid, []).append(entry)
        self._refresh_history_view(self.panel.history_filter_combo.currentText())

    def _refresh_history_view(self, filter_text: str):
        rows: List[str] = []
        if filter_text and filter_text != "All assets":
            for uid, name in self.asset_names.items():
                if name == filter_text:
                    rows = list(reversed(self.asset_history.get(uid, [])))
                    break
        else:
            for uid in self.asset_history:
                rows.extend(self.asset_history[uid])
            rows = list(reversed(rows))
        self.panel.refresh_history(rows)

    def _sync_asset_lists(self, uid: str, location_text: str):
        self.panel.update_asset(uid, self.asset_names[uid], self.asset_symbols[uid], location_text)

    def _move_asset_on_hospital(self, uid: str, segment_key: str, checkpoint_key: str):
        self.hospital_scene.update_asset(uid, self.asset_names[uid], self.asset_symbols[uid], segment_key, checkpoint_key)

    def _handle_scan(self, reader: str, uid: str):
        reader = reader.upper().strip()
        uid = uid.upper().strip()
        if reader not in ("LEFT", "CENTER", "RIGHT"):
            return
        if not self._register_asset_if_needed(uid):
            return
        asset = self.pilot_scene.get_asset(uid)
        if not asset:
            return

        self.pilot_scene.move_asset(uid, reader)
        self._move_asset_on_hospital(uid, "PILOT", reader)
        self._sync_asset_lists(uid, f"Pilot Hallway / {reader}")
        self._append_history(uid, f"RFID scan at Pilot Hallway / {reader}")
        self.panel.set_event_text(f"{asset.name} scanned at {reader}")
        self.statusBar().showMessage(f"  {asset.name} ({uid}) detected at {reader} — placed on aligned pilot hallway in full hospital map.")

    def _route_asset(self, uid: str, segment_key: str, checkpoint_key: str):
        if uid not in self.asset_names:
            return
        spec = SEGMENTS[segment_key]
        if segment_key == "PILOT" and checkpoint_key in ("LEFT", "CENTER", "RIGHT"):
            self.pilot_scene.move_asset(uid, checkpoint_key)
        self._move_asset_on_hospital(uid, segment_key, checkpoint_key)
        self._sync_asset_lists(uid, f"{spec.name} / {checkpoint_key}")
        self._append_history(uid, f"routed to {spec.name} / {checkpoint_key}")
        self.panel.set_event_text(f"{self.asset_names[uid]} routed to {spec.name} / {checkpoint_key}")
        self.statusBar().showMessage(f"  {self.asset_names[uid]} routed to {spec.name} / {checkpoint_key} for rollout demonstration.")

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
