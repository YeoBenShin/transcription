"""Pages in the middle of the main window, besides the list: the live transcript while recording, and one
transcript opened from the list."""

import html
import time
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget

from app.models import timestamp
from desktop.history import HistoryItem
from desktop.ui import theme
from desktop.ui.cards import icon_button

STATE_TEXT = {
    "starting": "Starting…",
    "recording": "Recording",
    "stopping": "Saving the last audio…",
    "finishing": "Finishing the transcript…",
    "done": "Done",
    "failed": "Stopped with an error",
}


class LivePage(QWidget):
    """Lines that will not change, then the draft: agreed words in normal text, words that can change dimmed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 0)
        header = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {theme.RECORDING}; font-size: 14px;")
        header.addWidget(self.dot)
        self.status = QLabel()
        self.status.setObjectName("heading")
        header.addWidget(self.status)
        header.addStretch()
        self.clock = QLabel()
        self.clock.setObjectName("secondary")
        header.addWidget(self.clock)
        layout.addLayout(header)
        self.text = QTextBrowser()
        self.text.setObjectName("liveText")
        self.text.setOpenLinks(False)
        layout.addWidget(self.text, 1)
        self.notice = QLabel()
        self.notice.setObjectName("secondary")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.lines: List[Tuple[float, str]] = []
        self.draft: Optional[dict] = None
        self.started = time.monotonic()
        self.state = "starting"
        self.timer = QTimer(self, interval=500, timeout=self.tick)

    def begin(self, notice: str = "") -> None:
        self.lines, self.draft = [], None
        self.started = time.monotonic()
        self.notice.setText(notice)
        self.notice.setVisible(bool(notice))
        self.set_state("starting")
        self.timer.start()
        self.render()

    def set_state(self, state: str) -> None:
        self.state = state
        self.status.setText(STATE_TEXT.get(state, state))
        self.dot.setVisible(state == "recording")
        if state not in ("starting", "recording"):
            self.timer.stop()
        self.tick()

    def tick(self) -> None:
        if self.state in ("starting", "recording"):
            self.clock.setText(timestamp(time.monotonic() - self.started))

    def add_line(self, start: float, text: str) -> None:
        self.lines.append((start, text))
        self.render()

    def set_draft(self, draft: Optional[dict]) -> None:
        self.draft = draft if draft and draft.get("start") is not None else None
        self.render()

    def render(self) -> None:
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        dim = theme.current().secondary
        rows = [f"<p><span style='color:{dim}'>[{timestamp(s)}]</span> {html.escape(t)}</p>" for s, t in self.lines]
        if self.draft:
            label = f"{html.escape(self.draft['label'])}: " if self.draft.get("label") else ""
            rows.append(f"<p><span style='color:{dim}'>[{timestamp(self.draft['start'])}]</span> {label}"
                        f"{html.escape(self.draft.get('agreed', ''))} "
                        f"<span style='color:{dim}'>{html.escape(self.draft.get('tail', ''))}</span></p>")
        if not rows:
            rows.append(f"<p style='color:{dim}'>Listening… text shows here a few seconds after speech.</p>")
        self.text.setHtml("".join(rows))
        if at_bottom:
            bar.setValue(bar.maximum())


class TranscriptPage(QWidget):
    """One transcript: its text with timestamps, search matches marked, and its actions."""

    back_requested = Signal()
    copy_requested = Signal(object)
    reveal_requested = Signal(object)
    finalize_requested = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.item: Optional[HistoryItem] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 0)
        top = QHBoxLayout()
        back = icon_button("back", "Back to the list")
        back.clicked.connect(self.back_requested.emit)
        top.addWidget(back)
        self.title = QLabel()
        self.title.setObjectName("heading")
        self.title.setTextFormat(Qt.PlainText)
        self.title.setMinimumWidth(10)
        top.addWidget(self.title, 1)
        self.buttons = {}
        for name, tip, signal in (("copy", "Copy text", self.copy_requested),
                                  ("folder", "Show in folder", self.reveal_requested),
                                  ("refresh", "Finish transcript and merge audio", self.finalize_requested),
                                  ("trash", "Move to trash", self.delete_requested)):
            button = icon_button(name, tip)
            button.clicked.connect(lambda checked=False, s=signal: s.emit(self.item))
            top.addWidget(button)
            self.buttons[name] = button
        layout.addLayout(top)
        self.text = QTextBrowser()
        self.text.setObjectName("transcript")
        layout.addWidget(self.text, 1)

    def show_item(self, item: HistoryItem, query: str = "") -> None:
        self.item = item
        self.title.setText(item.title)
        self.buttons["copy"].setVisible(item.transcript is not None)
        self.buttons["refresh"].setVisible(item.kind == "live" and not item.finalized)
        body = ""
        if item.transcript is not None:
            try:
                body = item.transcript.read_text(encoding="utf-8", errors="replace").split("\n", 2)[-1].strip()
            except OSError:
                body = ""
        dim = theme.current().secondary
        rows = []
        for line in body.splitlines():
            stamp, _, text = line.partition("] ") if line.startswith("[") else ("", "", line)
            prefix = f"<span style='color:{dim}'>{html.escape(stamp)}]</span> " if stamp else ""
            rows.append(f"<p>{prefix}{html.escape(text)}</p>")
        self.text.setHtml("".join(rows) or f"<p style='color:{dim}'>No text yet.</p>")
        self.mark(query)

    def mark(self, query: str) -> None:
        """Highlight each match of the search text and scroll to the first one."""
        selections = []
        query = query.strip()
        if query:
            document = self.text.document()
            cursor = document.find(query)
            while not cursor.isNull():
                selection = QTextBrowser.ExtraSelection()
                selection.cursor = cursor
                selection.format.setBackground(QColor(theme.current().highlight))
                selections.append(selection)
                cursor = document.find(query, cursor)
        self.text.setExtraSelections(selections)
        if selections:
            first = QTextCursor(selections[0].cursor)
            self.text.setTextCursor(first)
            self.text.ensureCursorVisible()
