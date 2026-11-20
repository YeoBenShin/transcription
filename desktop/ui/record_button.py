"""The round record button: a white (dark mode) or blue (light mode) circle with a glow; red while recording,
a spinning arc while the recording stops and the transcript finishes."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from desktop.ui import theme

IDLE, RECORDING, BUSY = "idle", "recording", "busy"
DIAMETER = 48
MARGIN = 18  # room for the glow


class RecordButton(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = IDLE
        self.angle = 0
        self.timer = QTimer(self, interval=30, timeout=self._spin)
        self.setFixedSize(QSize(DIAMETER + 2 * MARGIN, DIAMETER + 2 * MARGIN))
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Record")
        self.setToolTip("Start recording")

    def set_state(self, state: str) -> None:
        self.state = state
        self.setToolTip({IDLE: "Start recording", RECORDING: "Stop recording", BUSY: "Finishing the transcript…"}[state])
        self.setAccessibleName({IDLE: "Record", RECORDING: "Stop", BUSY: "Busy"}[state])
        self.setCursor(Qt.ArrowCursor if state == BUSY else Qt.PointingHandCursor)
        if state == BUSY:
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def _spin(self) -> None:
        self.angle = (self.angle + 8) % 360
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.state != BUSY and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        color = QColor(theme.RECORDING if self.state == RECORDING else theme.current().record)
        glow = QRadialGradient(center, DIAMETER / 2 + MARGIN)
        halo = QColor(color)
        halo.setAlpha(110 if self.state == RECORDING else 70)
        glow.setColorAt(0.55, halo)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, DIAMETER / 2 + MARGIN, DIAMETER / 2 + MARGIN)
        radius = DIAMETER / 2 * (0.9 if self.state == RECORDING else 1.0)
        painter.setBrush(color)
        painter.drawEllipse(center, radius, radius)
        if self.state == RECORDING:
            painter.setBrush(QColor("white"))
            painter.drawRoundedRect(QRectF(center.x() - 8, center.y() - 8, 16, 16), 3, 3)
        if self.state == BUSY:
            pen = QPen(QColor(theme.current().accent), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            box = QRectF(center.x() - radius - 5, center.y() - radius - 5, 2 * radius + 10, 2 * radius + 10)
            painter.drawArc(box, -self.angle * 16, 100 * 16)
