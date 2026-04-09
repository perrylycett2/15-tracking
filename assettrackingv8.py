#!/usr/bin/env python3
"""
assettrackingv8.py
Hospital Asset Tracker — fullscreen-ready hospital rollout demo.

What this version adds:
- The active RFID pilot hallway is aligned to a real hallway from the attached
  SJH Level 1 South floorplan.
- Additional rollout hallways are drawn over real corridors in the same plan.
- Assets can be routed across the larger hospital deployment example.
- Every asset keeps a timestamped movement history.
- Selecting an asset shows its trail on the hospital map.
- The left UI is simplified into tabs so it is cleaner in fullscreen.

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
    QListWidget, QListWidgetItem, QCheckBox, QSplitter, QTabWidget, QGridLayout,
    QAbstractItemView
)
from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve,
    pyqtProperty
)
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont, QRadialGradient, QPolygonF, QPixmap,
    QPainterPath, QPainterPathStroker
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
    "panel2": "#11161D",
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
    "route_fill": "#ADB5BD",
    "route_outline": "#6B7280",
    "sensor": "#3FB950",
    "history": "#F2CC60",
}

# -----------------------------------------------------------------------------
# Detailed pilot corridor geometry
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Hospital rollout overlay geometry aligned to the real floorplan crop
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SegmentSpec:
    name: str
    path_points: Tuple[Tuple[float, float], ...]
    checkpoints: Dict[str, Tuple[float, float]]
    active: bool = False
    label_pos: Tuple[float, float] = (0.0, 0.0)


SEGMENTS: Dict[str, SegmentSpec] = {
    # Active RFID pilot aligned to the real curved south corridor that feeds into
    # the long A1-C029 hallway on the uploaded SJH south level floorplan.
    "PILOT": SegmentSpec(
        name="South Pilot Segment",
        path_points=(
            (412, 792),
            (500, 792),
            (596, 792),
            (690, 792),
            (786, 792),
            (878, 792),
            (968, 792),
        ),
        checkpoints={
            "LEFT": (456, 792),
            "CENTER": (690, 792),
            "RIGHT": (924, 792),
        },
        active=True,
        label_pos=(520, 754),
    ),
    # Real west-side vertical corridor spine beside rooms A1-201 to E1-109.
    "WEST_WING": SegmentSpec(
        name="West Wing Spine",
        path_points=(
            (168, 258),
            (168, 345),
            (168, 470),
            (168, 590),
            (168, 710),
            (168, 812),
            (168, 842),
        ),
        checkpoints={
            "NORTH": (168, 305),
            "MID": (168, 535),
            "SOUTH": (168, 742),
        },
        label_pos=(188, 390),
    ),
    # Top-center corridor outside F1-118 / F1-114 / F1-104.
    "EAST_LOOP": SegmentSpec(
        name="North Clinic Corridor",
        path_points=(
            (708, 184),
            (828, 184),
            (958, 184),
            (1086, 184),
            (1166, 184),
        ),
        checkpoints={
            "WEST": (710, 184),
            "MID": (960, 184),
            "EAST": (1166, 184),
        },
        label_pos=(792, 146),
    ),
    # Real right-side vertical corridor on the east edge.
    "EAST_SPINE": SegmentSpec(
        name="East Main Spine",
        path_points=(
            (1292, 102),
            (1292, 240),
            (1292, 392),
            (1292, 560),
            (1292, 744),
            (1292, 840),
        ),
        checkpoints={
            "NORTH": (1292, 190),
            "MID": (1292, 560),
            "SOUTH": (1292, 746),
        },
        label_pos=(1176, 484),
    ),
    # Short real south-east connector branching off the main pilot corridor.
    "SOUTH_CLINICS": SegmentSpec(
        name="South Clinics Connector",
        path_points=(
            (1048, 806),
            (1132, 806),
            (1214, 806),
            (1286, 806),
        ),
        checkpoints={
            "WEST": (1050, 806),
            "MID": (1178, 806),
            "EAST": (1286, 806),
        },
        label_pos=(1036, 768),
    ),
}

HOSPITAL_POSITION_SLOTS = {
    "LEFT": [QPointF(0, 0), QPointF(-12, -14), QPointF(12, 14), QPointF(-24, 16), QPointF(24, -16)],
    "CENTER": [QPointF(0, 0), QPointF(-14, -14), QPointF(14, 14), QPointF(-28, 16), QPointF(28, -16)],
    "RIGHT": [QPointF(0, 0), QPointF(-12, -14), QPointF(12, 14), QPointF(-24, 16), QPointF(24, -16)],
    "NORTH": [QPointF(0, 0), QPointF(-14, 12), QPointF(14, -12), QPointF(-22, 24), QPointF(22, -24)],
    "MID": [QPointF(0, 0), QPointF(-14, -12), QPointF(14, 12), QPointF(-24, 18), QPointF(24, -18)],
    "SOUTH": [QPointF(0, 0), QPointF(-14, 12), QPointF(14, -12), QPointF(-22, 24), QPointF(22, -24)],
    "WEST": [QPointF(0, 0), QPointF(12, -14), QPointF(-12, 14), QPointF(24, -20), QPointF(-24, 20)],
    "EAST": [QPointF(0, 0), QPointF(12, -14), QPointF(-12, 14), QPointF(24, -20), QPointF(-24, 20)],
}

ROUTING_TARGETS: Dict[str, Tuple[str, str]] = {
    "Pilot Hallway — LEFT": ("PILOT", "LEFT"),
    "Pilot Hallway — CENTER": ("PILOT", "CENTER"),
    "Pilot Hallway — RIGHT": ("PILOT", "RIGHT"),
    "West Wing Spine — NORTH": ("WEST_WING", "NORTH"),
    "West Wing Spine — MID": ("WEST_WING", "MID"),
    "West Wing Spine — SOUTH": ("WEST_WING", "SOUTH"),
    "East Upper Loop — WEST": ("EAST_LOOP", "WEST"),
    "East Upper Loop — MID": ("EAST_LOOP", "MID"),
    "East Upper Loop — EAST": ("EAST_LOOP", "EAST"),
    "East Main Spine — NORTH": ("EAST_SPINE", "NORTH"),
    "East Main Spine — MID": ("EAST_SPINE", "MID"),
    "East Main Spine — SOUTH": ("EAST_SPINE", "SOUTH"),
    "South Clinics Corridor — WEST": ("SOUTH_CLINICS", "WEST"),
    "South Clinics Corridor — MID": ("SOUTH_CLINICS", "MID"),
    "South Clinics Corridor — EAST": ("SOUTH_CLINICS", "EAST"),
}


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------


def now_stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def points_to_path(points: List[QPointF]) -> QPainterPath:
    path = QPainterPath(points[0])
    for pt in points[1:]:
        path.lineTo(pt)
    return path


# -----------------------------------------------------------------------------
# Dialog + pilot scene asset marker
# -----------------------------------------------------------------------------

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
            QCheckBox {{ spacing: 8px; color: {PALETTE['text']}; }}
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
            self.track_checkbox.isChecked(),
        )


class AssetNode(QGraphicsObject):
    RADIUS = 24

    def __init__(self, uid: str, name: str, symbol: str):
        super().__init__()
        self.uid = uid
        self.name = name
        self.symbol = symbol
        self.position_key = "CENTER"
        self.status_text = "CENTER"
        self.off_pilot = False
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
        base_color = QColor(PALETTE["idle"])
        color = QColor(170, 180, 195) if self.off_pilot else base_color

        glow = QRadialGradient(0, 0, r + 12)
        glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 75))
        glow.setColorAt(1, Qt.GlobalColor.transparent)
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(-r - 12, -r - 12, (r + 12) * 2, (r + 12) * 2)

        painter.setOpacity(0.38 if self.off_pilot else 1.0)
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

        painter.setOpacity(1.0)
        painter.setPen(QColor(PALETTE["text"]))
        font = QFont("Consolas", 7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(-70, r + 4, 140, 16), Qt.AlignmentFlag.AlignCenter, self.name)

        painter.setPen(QColor(PALETTE["subtext"]))
        painter.setFont(QFont("Consolas", 6))
        painter.drawText(QRectF(-70, r + 18, 140, 14), Qt.AlignmentFlag.AlignCenter, self.status_text)

    def update_identity(self, name: str, symbol: str):
        self.name = name
        self.symbol = symbol
        self.setToolTip(f"{self.name}\nUID: {self.uid}")
        self.update()

    def move_to(self, position_key: str, slot_offset: int = 0):
        self.position_key = position_key
        self.status_text = position_key
        self.off_pilot = False
        self.slot_offset = slot_offset
        target_x = POS_X.get(position_key, POS_X["CENTER"]) + slot_offset
        self._anim.stop()
        self._anim.setStartValue(float(self.x()))
        self._anim.setEndValue(float(target_x))
        self._anim.start()
        self.update()

    def set_off_pilot(self, where: str):
        self.off_pilot = True
        self.status_text = where
        self.update()


class HospitalAssetMarker(QGraphicsObject):
    def __init__(self, uid: str, name: str, symbol: str):
        super().__init__()
        self.uid = uid
        self.name = name
        self.symbol = symbol
        self.segment_key = "PILOT"
        self.checkpoint_key = "CENTER"
        self._pos = QPointF(*SEGMENTS["PILOT"].checkpoints["CENTER"])
        self.setPos(self._pos)
        self.setZValue(40)
        self.setToolTip(f"{self.name}\nUID: {self.uid}")

        self._anim = QPropertyAnimation(self, b"marker_pos", self)
        self._anim.setDuration(550)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def boundingRect(self) -> QRectF:
        return QRectF(-48, -38, 96, 90)

    def get_marker_pos(self):
        return self.pos()

    def set_marker_pos(self, point):
        self.setPos(point)

    marker_pos = pyqtProperty(QPointF, fget=get_marker_pos, fset=set_marker_pos)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        glow = QRadialGradient(0, 0, 22)
        glow.setColorAt(0, QColor(88, 166, 255, 105))
        glow.setColorAt(1, Qt.GlobalColor.transparent)
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(-22, -22, 44, 44)

        painter.setBrush(QColor(PALETTE["pilot_fill"]))
        painter.setPen(QPen(QColor(PALETTE["pilot_outline"]), 2))
        painter.drawEllipse(-14, -14, 28, 28)

        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI Emoji", 12))
        painter.drawText(QRectF(-14, -14, 28, 28), Qt.AlignmentFlag.AlignCenter, self.symbol)

        painter.setPen(QColor("#0F1720"))
        painter.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        painter.drawText(QRectF(-48, 16, 96, 14), Qt.AlignmentFlag.AlignCenter, self.name)

        painter.setPen(QColor("#334155"))
        painter.setFont(QFont("Consolas", 6))
        painter.drawText(QRectF(-48, 28, 96, 12), Qt.AlignmentFlag.AlignCenter, self.checkpoint_key)

    def update_identity(self, name: str, symbol: str):
        self.name = name
        self.symbol = symbol
        self.setToolTip(f"{self.name}\nUID: {self.uid}")
        self.update()

    def move_to(self, segment_key: str, checkpoint_key: str, offset: QPointF):
        self.segment_key = segment_key
        self.checkpoint_key = checkpoint_key
        base = QPointF(*SEGMENTS[segment_key].checkpoints[checkpoint_key])
        target = base + offset
        self._anim.stop()
        self._anim.setStartValue(QPointF(self.pos()))
        self._anim.setEndValue(QPointF(target))
        self._anim.start()
        self.update()


# -----------------------------------------------------------------------------
# Scenes
# -----------------------------------------------------------------------------

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

        lbl("DETAILED ACTIVE RFID PILOT HALLWAY", 260, HALLWAY.top() + 6)
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
            if other_uid != uid and other.position_key == position_key and not other.off_pilot:
                same_pos_count += 1

        slots = POSITION_SLOTS.get(position_key, [0])
        slot_offset = slots[min(same_pos_count, len(slots) - 1)]
        node.move_to(position_key, slot_offset)

    def set_asset_off_pilot(self, uid: str, location_text: str):
        node = self.assets.get(uid)
        if node:
            node.set_off_pilot(location_text)


class HospitalOverviewScene(QGraphicsScene):
    def __init__(self, floorplan_path: Optional[Path]):
        self.floorplan_path = floorplan_path
        self.assets: Dict[str, HospitalAssetMarker] = {}
        self.loaded_pixmap = QPixmap(str(floorplan_path)) if floorplan_path and floorplan_path.exists() else QPixmap()
        if not self.loaded_pixmap.isNull():
            super().__init__(0, 0, self.loaded_pixmap.width(), self.loaded_pixmap.height())
        else:
            super().__init__(0, 0, 1333, 959)
        self.history_items = []
        self._draw_background()
        self._draw_segment_overlay()

    def _draw_background(self):
        self.addRect(self.sceneRect(), QPen(Qt.PenStyle.NoPen), QBrush(QColor("#F2F4F7"))).setZValue(-100)
        if not self.loaded_pixmap.isNull():
            item = self.addPixmap(self.loaded_pixmap)
            item.setOpacity(0.99)
            item.setZValue(-90)
        else:
            t = self.addText("Floorplan image not found. Place the cropped PNG beside this script.")
            t.setDefaultTextColor(QColor(PALETTE["warn"]))
            t.setFont(QFont("Consolas", 16))
            t.setPos(60, 60)
            t.setZValue(-80)

    def _segment_path(self, spec: SegmentSpec) -> QPainterPath:
        return points_to_path([QPointF(x, y) for x, y in spec.path_points])

    def _add_segment_shape(self, spec: SegmentSpec):
        path = self._segment_path(spec)

        # Extra-narrow overlays stay visually inside the real corridor widths.
        glow_color = QColor(84, 166, 255, 58) if spec.active else QColor(107, 114, 128, 36)
        core_color = QColor(PALETTE["accent"] if spec.active else PALETTE["route_outline"])
        halo_pen = QPen(glow_color, 12 if spec.active else 8)
        halo_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        halo_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        halo = self.addPath(path, halo_pen)
        halo.setZValue(6 if spec.active else 3)

        core_pen = QPen(core_color, 5 if spec.active else 3,
                        Qt.PenStyle.SolidLine if spec.active else Qt.PenStyle.DashLine)
        core_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        core_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        line = self.addPath(path, core_pen)
        line.setZValue(7 if spec.active else 4)

        for checkpoint, point in spec.checkpoints.items():
            pt = QPointF(*point)
            ring = self.addEllipse(
                pt.x() - 6, pt.y() - 6, 12, 12,
                QPen(QColor(PALETTE["sensor"]).darker(120), 1.5),
                QBrush(QColor(PALETTE["sensor"]))
            )
            ring.setZValue(12)

            txt = self.addText(checkpoint)
            txt.setDefaultTextColor(QColor("#204B2E"))
            txt.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
            txt.setPos(pt.x() + 8, pt.y() - 10)
            txt.setZValue(13)

        label = self.addText(spec.name if not spec.active else f"ACTIVE PILOT · {spec.name}")
        label.setDefaultTextColor(QColor("#16324F") if spec.active else QColor("#4B5563"))
        label.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        label.setPos(*spec.label_pos)
        label.setZValue(11)

    def _draw_segment_overlay(self):
        for spec in SEGMENTS.values():
            self._add_segment_shape(spec)

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

    def show_history_trail(self, entries: List[Dict[str, str]]):
        for item in self.history_items:
            self.removeItem(item)
        self.history_items.clear()

        if len(entries) < 2:
            return

        dedup_points: List[QPointF] = []
        seen_pairs = []
        for entry in entries:
            pair = (entry["segment_key"], entry["checkpoint_key"])
            if seen_pairs and seen_pairs[-1] == pair:
                continue
            seen_pairs.append(pair)
            dedup_points.append(QPointF(*SEGMENTS[entry["segment_key"]].checkpoints[entry["checkpoint_key"]]))

        if len(dedup_points) < 2:
            return

        trail_path = points_to_path(dedup_points)
        trail_pen = QPen(QColor(PALETTE["history"]), 4, Qt.PenStyle.DashLine)
        trail_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        trail_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        trail = self.addPath(trail_path, trail_pen)
        trail.setZValue(26)
        self.history_items.append(trail)

        for i, pt in enumerate(dedup_points, start=1):
            dot = self.addEllipse(pt.x() - 6, pt.y() - 6, 12, 12,
                                  QPen(QColor("#7C5B00"), 1.5),
                                  QBrush(QColor(PALETTE["history"])))
            dot.setZValue(27)
            self.history_items.append(dot)

            n = self.addText(str(i))
            n.setDefaultTextColor(QColor("#1F2937"))
            n.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
            n.setPos(pt.x() - 4, pt.y() - 12)
            n.setZValue(28)
            self.history_items.append(n)

    def clear_history_trail(self):
        for item in self.history_items:
            self.removeItem(item)
        self.history_items.clear()

    def reset_view_rect(self) -> QRectF:
        return self.sceneRect().adjusted(-20, -20, 20, 20)


# -----------------------------------------------------------------------------
# Views and serial worker
# -----------------------------------------------------------------------------

class ResponsiveMapView(QGraphicsView):
    def __init__(self, scene, min_size: Tuple[int, int] = (900, 440)):
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform |
                            QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QBrush(QColor(PALETTE["bg"])))
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setMinimumSize(*min_size)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._zoom_steps = 0
        self._manual_zoom = False
        self._min_zoom_steps = -6
        self._max_zoom_steps = 18

    def _apply_zoom_step(self, step: int):
        new_steps = max(self._min_zoom_steps, min(self._max_zoom_steps, self._zoom_steps + step))
        if new_steps == self._zoom_steps:
            return
        factor = 1.15 if step > 0 else 1 / 1.15
        self.scale(factor, factor)
        self._zoom_steps = new_steps
        self._manual_zoom = self._zoom_steps != 0
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if self._manual_zoom else QGraphicsView.DragMode.NoDrag)

    def zoom_in(self):
        self._apply_zoom_step(1)

    def zoom_out(self):
        self._apply_zoom_step(-1)

    def wheelEvent(self, event):
        delta_y = event.angleDelta().y()
        delta_x = event.angleDelta().x()
        modifiers = event.modifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            step = 1 if (delta_y or delta_x) > 0 else -1
            self._apply_zoom_step(step)
            event.accept()
            return

        # Default wheel behavior is now navigation, not zoom.
        if modifiers & Qt.KeyboardModifier.ShiftModifier or abs(delta_x) > abs(delta_y):
            bar = self.horizontalScrollBar()
            amount = delta_x if delta_x else delta_y
            bar.setValue(bar.value() - amount)
        else:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - delta_y)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.fit_scene()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._manual_zoom:
            self.fit_scene()

    def fit_scene(self, rect: Optional[QRectF] = None):
        self.resetTransform()
        target_rect = rect or self.sceneRect()
        self.fitInView(target_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(target_rect.center())
        self._zoom_steps = 0
        self._manual_zoom = False
        self.setDragMode(QGraphicsView.DragMode.NoDrag)


class MapCard(QWidget):
    def __init__(self, title: str, subtitle: str, view: ResponsiveMapView, fit_rect_getter=None):
        super().__init__()
        self.view = view
        self.fit_rect_getter = fit_rect_getter
        self.setStyleSheet(f"""
            QWidget#card {{
                background: {PALETTE['panel']};
                border-left: 1px solid {PALETTE['border']};
            }}
            QLabel {{
                color: {PALETTE['text']};
                font-family: Consolas;
            }}
            QPushButton {{
                background: #1F2937;
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border']};
                border-radius: 7px;
                padding: 6px 10px;
                font-family: Consolas;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: #283548;
            }}
        """)
        self.setObjectName("card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size:13px; font-weight:bold; color:{PALETTE['accent']};")
        subtitle_lbl = QLabel(subtitle)
        subtitle_lbl.setStyleSheet(f"font-size:9px; color:{PALETTE['subtext']};")

        text_col.addWidget(title_lbl)
        text_col.addWidget(subtitle_lbl)
        header.addLayout(text_col, 1)

        minus_btn = QPushButton("−")
        minus_btn.setFixedWidth(34)
        minus_btn.clicked.connect(self.view.zoom_out)
        fit_btn = QPushButton("Fit")
        fit_btn.clicked.connect(self.fit_view)
        plus_btn = QPushButton("+")
        plus_btn.setFixedWidth(34)
        plus_btn.clicked.connect(self.view.zoom_in)

        header.addWidget(minus_btn)
        header.addWidget(fit_btn)
        header.addWidget(plus_btn)
        layout.addLayout(header)
        layout.addWidget(view, 1)

    def fit_view(self):
        rect = self.fit_rect_getter() if callable(self.fit_rect_getter) else None
        self.view.fit_scene(rect)


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
                    if reader in ("LEFT", "CENTER", "RIGHT") and uid:
                        self.scan_received.emit(reader, uid)

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()


# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------

class SidePanel(QWidget):
    port_connect = pyqtSignal(str)
    demo_scan = pyqtSignal(str, str)
    reset_views_requested = pyqtSignal()
    route_asset_requested = pyqtSignal(str, str, str)
    history_focus_changed = pyqtSignal(str)
    asset_focus_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(280)
        self.asset_meta: Dict[str, Dict[str, str]] = {}
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget {{ background: {PALETTE['panel']}; color: {PALETTE['text']}; }}
            QLabel {{ font-family: Consolas; color: {PALETTE['text']}; }}
            QGroupBox {{
                border: 1px solid {PALETTE['border']};
                border-radius: 10px;
                margin-top: 10px;
                font-family: Consolas;
                font-size: 10px;
                color: {PALETTE['subtext']};
                background: {PALETTE['panel2']};
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
            QPushButton {{
                background: {PALETTE['accent']};
                color: #0D1117;
                border: none;
                border-radius: 7px;
                padding: 8px;
                font-family: Consolas;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{ background: #79BFFF; }}
            QPushButton[secondary="true"] {{
                background: #222A35;
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border']};
            }}
            QComboBox, QListWidget, QTabWidget::pane {{
                background: {PALETTE['bg']};
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border']};
                border-radius: 8px;
                padding: 4px 6px;
                font-family: Consolas;
            }}
            QListWidget::item {{ padding: 6px 4px; }}
            QListWidget::item:selected {{ background: #1D4ED8; color: white; }}
            QTabBar::tab {{
                background: #1A212B;
                color: {PALETTE['subtext']};
                padding: 8px 12px;
                border: 1px solid {PALETTE['border']};
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                min-width: 70px;
            }}
            QTabBar::tab:selected {{ background: {PALETTE['panel2']}; color: {PALETTE['text']}; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("🏥 Tracker Control")
        title.setStyleSheet(f"font-size:18px; font-weight:bold; color:{PALETTE['accent']}; font-family:Consolas;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        subtitle = QLabel("Cleaner fullscreen layout · route + history")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"font-size:9px; color:{PALETTE['subtext']}; font-family:Consolas;")
        root.addWidget(subtitle)

        status_card = QGroupBox("SESSION")
        status_layout = QVBoxLayout(status_card)
        self.summary_label = QLabel("0 assets tracked")
        self.summary_label.setStyleSheet(f"font-size:11px; color:{PALETTE['text']}; font-weight:bold;")
        self.last_event_label = QLabel("Waiting for scans...")
        self.last_event_label.setWordWrap(True)
        self.last_event_label.setStyleSheet(f"font-size:9px; color:{PALETTE['subtext']};")
        status_layout.addWidget(self.summary_label)
        status_layout.addWidget(self.last_event_label)
        root.addWidget(status_card)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_assets_tab(), "Assets")
        self.tabs.addTab(self._build_demo_tab(), "Demo")
        self.tabs.addTab(self._build_history_tab(), "History")

        footer = QLabel("Use Fit / + / − above each view. Wheel zoom also works.")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(f"font-size:8px; color:{PALETTE['subtext']};")
        root.addWidget(footer)

    def _build_assets_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        grp_serial = QGroupBox("CONNECTION")
        sl = QVBoxLayout(grp_serial)
        self.port_combo = QComboBox()
        self.refresh_ports()
        sl.addWidget(self.port_combo)

        self.connect_btn = QPushButton("Connect Serial")
        self.connect_btn.clicked.connect(lambda: self.port_connect.emit(self.port_combo.currentText()))
        sl.addWidget(self.connect_btn)

        refresh_btn = QPushButton("Refresh Ports")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh_ports)
        sl.addWidget(refresh_btn)

        self.serial_status = QLabel("Not connected")
        self.serial_status.setStyleSheet(f"font-size:9px; color:{PALETTE['subtext']};")
        sl.addWidget(self.serial_status)
        root.addWidget(grp_serial)

        grp_assets = QGroupBox("TRACKED ASSETS")
        al = QVBoxLayout(grp_assets)
        self.asset_list = QListWidget()
        self.asset_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.asset_list.itemSelectionChanged.connect(self._emit_asset_focus)
        al.addWidget(self.asset_list)
        root.addWidget(grp_assets, 1)

        fit_btn = QPushButton("Reset / Auto-Fit Views")
        fit_btn.clicked.connect(self.reset_views_requested.emit)
        root.addWidget(fit_btn)
        return tab

    def _build_demo_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        grp_demo = QGroupBox("QUICK PILOT SCANS")
        grid = QGridLayout(grp_demo)
        demo_buttons = [
            ("Chair L", "LEFT", "AA:AA:AA:AA"),
            ("Chair C", "CENTER", "AA:AA:AA:AA"),
            ("Chair R", "RIGHT", "AA:AA:AA:AA"),
            ("Pump L", "LEFT", "BB:BB:BB:BB"),
            ("Pump C", "CENTER", "BB:BB:BB:BB"),
            ("Bed R", "RIGHT", "CC:CC:CC:CC"),
        ]
        for i, (label, reader, uid) in enumerate(demo_buttons):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, r=reader, u=uid: self.demo_scan.emit(r, u))
            grid.addWidget(btn, i // 3, i % 3)
        root.addWidget(grp_demo)

        grp_route = QGroupBox("HOSPITAL ROUTING")
        rl = QVBoxLayout(grp_route)
        self.route_asset_combo = QComboBox()
        self.route_asset_combo.addItem("Select asset")
        rl.addWidget(self.route_asset_combo)
        self.route_target_combo = QComboBox()
        self.route_target_combo.addItems(list(ROUTING_TARGETS.keys()))
        rl.addWidget(self.route_target_combo)
        route_btn = QPushButton("Move Asset To Selected Hallway")
        route_btn.clicked.connect(self._emit_route_request)
        rl.addWidget(route_btn)
        root.addWidget(grp_route)

        hint = QLabel(
            "Serial scan format:\n"
            "SCAN:LEFT:AA:BB:CC:DD\n"
            "SCAN:CENTER:AA:BB:CC:DD\n"
            "SCAN:RIGHT:AA:BB:CC:DD"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"font-size:8px; color:{PALETTE['subtext']};")
        root.addWidget(hint)
        root.addStretch()
        return tab

    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        grp_history = QGroupBox("MOVEMENT HISTORY")
        hl = QVBoxLayout(grp_history)
        self.history_filter_combo = QComboBox()
        self.history_filter_combo.addItem("All assets")
        self.history_filter_combo.currentTextChanged.connect(self.history_focus_changed.emit)
        hl.addWidget(self.history_filter_combo)

        self.history_list = QListWidget()
        hl.addWidget(self.history_list)
        root.addWidget(grp_history, 1)
        return tab

    def _emit_route_request(self):
        uid = self.route_asset_combo.currentData()
        target = self.route_target_combo.currentText()
        if not uid or target not in ROUTING_TARGETS:
            return
        segment_key, checkpoint_key = ROUTING_TARGETS[target]
        self.route_asset_requested.emit(uid, segment_key, checkpoint_key)

    def _emit_asset_focus(self):
        item = self.asset_list.currentItem()
        if not item:
            self.asset_focus_changed.emit("")
            return
        uid = item.data(Qt.ItemDataRole.UserRole)
        self.asset_focus_changed.emit(uid or "")

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
        current_uid = ""
        current_item = self.asset_list.currentItem()
        if current_item:
            current_uid = current_item.data(Qt.ItemDataRole.UserRole) or ""

        selected_filter = self.history_filter_combo.currentText() if self.history_filter_combo.count() else "All assets"

        self.asset_list.clear()
        self.route_asset_combo.clear()
        self.route_asset_combo.addItem("Select asset")
        self.history_filter_combo.blockSignals(True)
        self.history_filter_combo.clear()
        self.history_filter_combo.addItem("All assets")

        for asset_uid, meta in sorted(self.asset_meta.items(), key=lambda kv: kv[1]["name"].lower()):
            item = QListWidgetItem(f"{meta['symbol']}  {meta['name']}  [{meta['pos']}]")
            item.setToolTip(f"UID: {asset_uid}")
            item.setData(Qt.ItemDataRole.UserRole, asset_uid)
            self.asset_list.addItem(item)
            self.route_asset_combo.addItem(f"{meta['symbol']} {meta['name']}", asset_uid)
            self.history_filter_combo.addItem(meta['name'])
            if asset_uid == current_uid:
                self.asset_list.setCurrentItem(item)

        idx = self.history_filter_combo.findText(selected_filter)
        self.history_filter_combo.setCurrentIndex(max(0, idx))
        self.history_filter_combo.blockSignals(False)
        self.summary_label.setText(f"{len(self.asset_meta)} asset{'s' if len(self.asset_meta) != 1 else ''} tracked")

    def set_event_text(self, text: str):
        self.last_event_label.setText(text)

    def refresh_history(self, rows: List[str]):
        self.history_list.clear()
        for row in rows:
            self.history_list.addItem(QListWidgetItem(row))

    def select_history_asset_by_name(self, name: str):
        idx = self.history_filter_combo.findText(name)
        if idx >= 0:
            self.history_filter_combo.setCurrentIndex(idx)
            self.tabs.setCurrentIndex(2)


# -----------------------------------------------------------------------------
# Main window
# -----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hospital Asset Tracker — Fullscreen Rollout Demo v8")
        self.resize(1680, 980)
        self.setStyleSheet(f"QMainWindow {{ background: {PALETTE['bg']}; }}")

        self.floorplan_path = self._find_floorplan_image()
        self.hospital_scene = HospitalOverviewScene(self.floorplan_path)
        self.pilot_scene = HallwayScene()
        self.hospital_view = ResponsiveMapView(self.hospital_scene, min_size=(1080, 620))
        self.pilot_view = ResponsiveMapView(self.pilot_scene, min_size=(1080, 270))
        self.panel = SidePanel()

        self._serial_worker = None
        self.ignored_uids: Set[str] = set()
        self.asset_names: Dict[str, str] = {}
        self.asset_symbols: Dict[str, str] = {}
        self.asset_history: Dict[str, List[Dict[str, str]]] = {}
        self.history_counter = 0

        self.panel.port_connect.connect(self._connect_serial)
        self.panel.demo_scan.connect(self._handle_scan)
        self.panel.reset_views_requested.connect(self._reset_views)
        self.panel.route_asset_requested.connect(self._route_asset)
        self.panel.history_focus_changed.connect(self._refresh_history_view)
        self.panel.asset_focus_changed.connect(self._focus_asset_from_list)

        central = QWidget()
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.panel)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {PALETTE['border']}; }}")
        splitter.addWidget(
            MapCard(
                "Hospital-wide rollout",
                "Scroll to move • Ctrl + wheel to zoom • overlays sit on real corridor centerlines",
                self.hospital_view,
                fit_rect_getter=self.hospital_scene.reset_view_rect,
            )
        )
        splitter.addWidget(
            MapCard(
                "Detailed RFID pilot corridor",
                "Scroll to move • Ctrl + wheel to zoom • detailed pilot segment kept simple",
                self.pilot_view,
                fit_rect_getter=lambda: self.pilot_view.sceneRect(),
            )
        )
        splitter.setSizes([700, 250])
        lay.addWidget(splitter)

        self.statusBar().setStyleSheet(
            f"background:{PALETTE['panel']}; color:{PALETTE['subtext']}; font-family:Consolas; font-size:10px;"
        )
        if self.floorplan_path:
            self.statusBar().showMessage(
                f"  Loaded floorplan: {self.floorplan_path.name}. Pilot hallway and rollout paths are re-aligned to the floorplan corridor centerlines."
            )
        else:
            self.statusBar().showMessage(
                "  Floorplan image not found. Put the cropped hospital PNG beside this script and restart."
            )

        QTimer.singleShot(100, self._reset_views)

    def _find_floorplan_image(self) -> Optional[Path]:
        script_dir = Path(__file__).resolve().parent
        candidates = [
            script_dir / "SJH_South_Level1_cropped.png",
            script_dir / "plan2_cropped.png",
            script_dir / "SJH_Floorplan_SOUTH_cropped.png",
            script_dir / "SJH Floorplan with Scale Bar-SOUTH.png",
            script_dir / "floorplan_crop.png",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _reset_views(self):
        self.hospital_view.fit_scene(self.hospital_scene.reset_view_rect())
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
        self.panel.set_event_text(line[:80])

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
            self.pilot_scene.ensure_asset(uid, name, symbol)
            self.hospital_scene.ensure_asset(uid, name, symbol)
            self.asset_history.setdefault(uid, [])
            self.panel.update_asset(uid, name, symbol, "UNPLACED")
            return True

        self.ignored_uids.add(uid)
        self.panel.set_event_text(f"Ignoring tag {uid}")
        self.statusBar().showMessage(f"  Ignoring tag {uid}")
        return False

    def _record_history(self, uid: str, segment_key: str, checkpoint_key: str, source_text: str):
        name = self.asset_names.get(uid, uid)
        stamp = now_stamp()
        self.history_counter += 1
        entry = {
            "time": stamp,
            "order": self.history_counter,
            "segment_key": segment_key,
            "checkpoint_key": checkpoint_key,
            "segment_name": SEGMENTS[segment_key].name,
            "display": f"[{stamp}] {name} → {SEGMENTS[segment_key].name} / {checkpoint_key} ({source_text})",
        }
        self.asset_history.setdefault(uid, []).append(entry)

    def _update_history_views_for_uid(self, uid: str):
        name = self.asset_names.get(uid, "")
        current_filter = self.panel.history_filter_combo.currentText()
        rows: List[str]
        if current_filter == name:
            rows = [row["display"] for row in reversed(self.asset_history.get(uid, []))]
            self.panel.refresh_history(rows)
            self.hospital_scene.show_history_trail(self.asset_history.get(uid, []))
        elif current_filter == "All assets":
            self._refresh_history_view("All assets")

    def _handle_scan(self, reader: str, uid: str):
        reader = reader.upper().strip()
        uid = uid.upper().strip()

        if reader not in ("LEFT", "CENTER", "RIGHT"):
            return

        if not self._register_asset_if_needed(uid):
            return

        name = self.asset_names[uid]
        symbol = self.asset_symbols[uid]
        self.pilot_scene.move_asset(uid, reader)
        self.hospital_scene.update_asset(uid, name, symbol, "PILOT", reader)
        self.panel.update_asset(uid, name, symbol, f"Pilot / {reader}")
        self._record_history(uid, "PILOT", reader, "RFID scan")
        self._update_history_views_for_uid(uid)
        self.panel.set_event_text(f"{name} scanned at {reader}")
        self.statusBar().showMessage(
            f"  {name} ({uid}) detected at {reader} — mirrored to the aligned hospital pilot hallway."
        )

    def _route_asset(self, uid: str, segment_key: str, checkpoint_key: str):
        if uid not in self.asset_names:
            return
        name = self.asset_names[uid]
        symbol = self.asset_symbols[uid]

        self.hospital_scene.update_asset(uid, name, symbol, segment_key, checkpoint_key)
        if segment_key == "PILOT":
            self.pilot_scene.move_asset(uid, checkpoint_key)
        else:
            self.pilot_scene.set_asset_off_pilot(uid, f"OFF-PILOT · {SEGMENTS[segment_key].name}")

        self.panel.update_asset(uid, name, symbol, f"{SEGMENTS[segment_key].name} / {checkpoint_key}")
        self._record_history(uid, segment_key, checkpoint_key, "manual route")
        self._update_history_views_for_uid(uid)
        self.panel.set_event_text(f"{name} moved to {SEGMENTS[segment_key].name} / {checkpoint_key}")
        self.statusBar().showMessage(f"  Routed {name} to {SEGMENTS[segment_key].name} / {checkpoint_key}.")

    def _refresh_history_view(self, filter_name: str):
        if filter_name == "All assets":
            rows: List[str] = []
            merged = []
            for entries in self.asset_history.values():
                merged.extend(entries)
            merged.sort(key=lambda entry: entry.get("order", 0), reverse=True)
            rows = [entry["display"] for entry in merged]
            self.panel.refresh_history(rows)
            self.hospital_scene.clear_history_trail()
            return

        uid = self._uid_from_name(filter_name)
        if not uid:
            self.panel.refresh_history([])
            self.hospital_scene.clear_history_trail()
            return

        rows = [entry["display"] for entry in reversed(self.asset_history.get(uid, []))]
        self.panel.refresh_history(rows)
        self.hospital_scene.show_history_trail(self.asset_history.get(uid, []))

    def _uid_from_name(self, name: str) -> Optional[str]:
        for uid, asset_name in self.asset_names.items():
            if asset_name == name:
                return uid
        return None

    def _focus_asset_from_list(self, uid: str):
        if not uid or uid not in self.asset_names:
            return
        self.panel.select_history_asset_by_name(self.asset_names[uid])
        self.hospital_scene.show_history_trail(self.asset_history.get(uid, []))

    def closeEvent(self, event):
        if self._serial_worker:
            self._serial_worker.close()
        super().closeEvent(event)


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
