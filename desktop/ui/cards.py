"""Cards of the main list: one per transcript in the output folder, and one per job in the queue."""

from datetime import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QToolButton, QVBoxLayout, QWidget

from desktop import jobs
from desktop.history import HistoryItem
from desktop.ui.icons import icon

PREVIEW_LINES = 3
KIND_LABELS = {"file": "", "live": "Live", "folder": "Folder", "url": "Link", "merge": "Merge", "finalize": "Finalize"}


def icon_button(name: str, tip: str, parent=None) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("flatIcon")
    button.setIcon(icon(name))
    button.setIconSize(QSize(18, 18))
    button.setToolTip(tip)
    button.setAccessibleName(tip)
    button.setCursor(Qt.PointingHandCursor)
    return button


def rule() -> QFrame:
    line = QFrame()
    line.setObjectName("rule")
    return line


class HistoryCard(QFrame):
    """Preview of a transcript (3 lines), its date and time, and actions that show on hover."""

    opened = Signal(object)
    copy_requested = Signal(object)
    reveal_requested = Signal(object)
    finalize_requested = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, item: HistoryItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(8)

        text = QLabel(item.preview or self.empty_text())
        text.setObjectName("cardText" if item.preview else "secondary")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        text.ensurePolished()
        text.setMaximumHeight(text.fontMetrics().lineSpacing() * PREVIEW_LINES)
        text.setTextInteractionFlags(Qt.NoTextInteraction)
        layout.addWidget(text)
        layout.addWidget(rule())

        bottom = QHBoxLayout()
        when = datetime.fromtimestamp(item.modified)
        dates = QLabel(f"{when:%d %B %Y}\n{when:%H:%M}")
        dates.setObjectName("cardDate")
        bottom.addWidget(dates)
        tag = self.tag_text()
        if tag:
            label = QLabel(tag)
            label.setObjectName("secondary")
            bottom.addWidget(label, alignment=Qt.AlignBottom)
        bottom.addStretch()

        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        buttons = []
        if item.transcript is not None:
            buttons.append(("copy", "Copy text", self.copy_requested))
        buttons.append(("folder", "Show in folder", self.reveal_requested))
        if item.kind == "live" and not item.finalized:
            buttons.append(("refresh", "Finish transcript and merge audio", self.finalize_requested))
        buttons.append(("trash", "Move to trash", self.delete_requested))
        for name, tip, signal in buttons:
            button = icon_button(name, tip)
            button.clicked.connect(lambda checked=False, s=signal: s.emit(self.item))
            actions.addWidget(button)
        self.actions.setVisible(False)
        bottom.addWidget(self.actions, alignment=Qt.AlignBottom)
        layout.addLayout(bottom)

    def empty_text(self) -> str:
        return "No speech yet" if self.item.kind == "live" else "Empty transcript"

    def tag_text(self) -> str:
        if self.item.kind != "live":
            return ""
        return "Live" if self.item.finalized else "Live · not finished"

    def enterEvent(self, event) -> None:
        self.actions.setVisible(True)

    def leaveEvent(self, event) -> None:
        self.actions.setVisible(False)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.opened.emit(self.item)


class JobCard(QFrame):
    """A queued, running or ended job: its name, status, a busy bar while it runs, Stop or Remove."""

    stop_requested = Signal(object)
    remove_requested = Signal(object)
    log_requested = Signal(object)

    def __init__(self, job: jobs.Job, parent=None):
        super().__init__(parent)
        self.job = job
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        top = QHBoxLayout()
        title = QLabel(job.title)
        title.setObjectName("heading")
        title.setTextFormat(Qt.PlainText)
        title.setMinimumWidth(10)
        top.addWidget(title, 1)
        kind = QLabel(KIND_LABELS.get(job.kind, ""))
        kind.setObjectName("secondary")
        top.addWidget(kind)
        self.log_button = icon_button("file", "Show log")
        self.log_button.clicked.connect(lambda: self.log_requested.emit(self.job))
        top.addWidget(self.log_button)
        self.stop_button = icon_button("stop", "Stop")
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit(self.job))
        top.addWidget(self.stop_button)
        self.remove_button = icon_button("close", "Remove from list")
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.job))
        top.addWidget(self.remove_button)
        layout.addLayout(top)
        self.detail = QLabel()
        self.detail.setObjectName("secondary")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.PlainText)
        layout.addWidget(self.detail)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        job.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        active = self.job.status in (jobs.QUEUED, jobs.RUNNING)
        self.detail.setText(self.job.detail)
        self.bar.setVisible(self.job.status == jobs.RUNNING)
        self.stop_button.setVisible(active)
        self.remove_button.setVisible(not active)
