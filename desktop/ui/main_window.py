"""The main window: search, the list of jobs and transcripts, the live page, a transcript page, the record button,
and the bottom bar (drop hint, add files, folder, link, merge, settings)."""

from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QFile, QFileSystemWatcher, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from desktop import commands, history, jobs
from desktop.history import HistoryItem
from desktop.models import catalog
from desktop.paths import APP_NAME
from desktop.settings import Settings
from desktop.ui import record_button
from desktop.ui.cards import HistoryCard, JobCard
from desktop.ui.dialogs import HEADPHONES_NOTE, LinkDialog, MergeDialog, SettingsDialog
from desktop.ui.icons import icon
from desktop.ui.models_view import DownloadManager
from desktop.ui.pages import LivePage, TranscriptPage

LIST, LIVE, TRANSCRIPT = 0, 1, 2
SEARCH_DELAY_MS = 200
WAIT_FOR_SAVE_SECONDS = 10


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, save_settings: Callable[[], None], manager: DownloadManager):
        super().__init__()
        self.settings = settings
        self.save_settings = save_settings
        self.manager = manager
        self.queue = jobs.JobQueue()
        self.queue.job_added.connect(lambda job: self.refresh())
        self.queue.job_finished.connect(self.on_job_finished)
        self.live: Optional[jobs.LiveRecording] = None
        self.items: List[HistoryItem] = []
        self.setWindowTitle(APP_NAME)
        self.resize(450, 680)
        self.setMinimumSize(380, 520)
        self.setAcceptDrops(True)

        page = QWidget()
        page.setObjectName("page")
        self.setCentralWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 12)

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search in transcriptions")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(QAction(icon("search"), "", self.search), QLineEdit.LeadingPosition)
        self.search_timer = QTimer(self, singleShot=True, interval=SEARCH_DELAY_MS, timeout=self.apply_search)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        search_row = QHBoxLayout()
        search_row.setContentsMargins(16, 0, 16, 4)
        search_row.addWidget(self.search)
        layout.addLayout(search_row)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.list_area = QScrollArea()
        self.list_area.setObjectName("list")
        self.list_area.setWidgetResizable(True)
        self.list_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stack.addWidget(self.list_area)
        self.live_page = LivePage()
        self.stack.addWidget(self.live_page)
        self.transcript_page = TranscriptPage()
        self.transcript_page.back_requested.connect(lambda: self.stack.setCurrentIndex(LIST))
        self.connect_item_actions(self.transcript_page)
        self.stack.addWidget(self.transcript_page)

        self.record = record_button.RecordButton()
        self.record.clicked.connect(self.toggle_recording)
        layout.addWidget(self.record, alignment=Qt.AlignHCenter)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(16, 0, 16, 0)
        hints = QVBoxLayout()
        hints.setSpacing(4)
        drop = QLabel("Drop audio files or a folder here to transcribe")
        drop.setObjectName("hint")
        hints.addWidget(drop)
        links = QHBoxLayout()
        links.setSpacing(14)
        for text, handler in (("Files…", self.choose_files), ("Folder…", self.choose_folder),
                              ("Link…", self.enter_link), ("Merge…", self.merge)):
            button = QPushButton(text)
            button.setObjectName("link")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(handler)
            links.addWidget(button)
        links.addStretch()
        hints.addLayout(links)
        bottom.addLayout(hints, 1)
        gear = QToolButton()
        gear.setObjectName("iconButton")
        gear.setIcon(icon("gear", size=22))
        gear.setIconSize(QSize(22, 22))
        gear.setToolTip("Settings")
        gear.setAccessibleName("Settings")
        gear.clicked.connect(self.open_settings)
        bottom.addWidget(gear, alignment=Qt.AlignBottom)
        layout.addLayout(bottom)

        self.drop_overlay = QLabel("Drop to transcribe", page)
        self.drop_overlay.setObjectName("dropOverlay")
        self.drop_overlay.setAlignment(Qt.AlignCenter)
        self.drop_overlay.hide()

        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda path: self.refresh_timer.start())
        self.refresh_timer = QTimer(self, singleShot=True, interval=500, timeout=self.refresh)
        self.refresh()

    # List

    def watch_output(self) -> None:
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        out = Path(self.settings.output_dir)
        for folder in (out, out / history.LIVE_DIRNAME):
            if folder.is_dir():
                self.watcher.addPath(str(folder))

    def refresh(self) -> None:
        self.watch_output()
        self.items = history.scan(Path(self.settings.output_dir))
        self.apply_search()
        if self.stack.currentIndex() == TRANSCRIPT and self.transcript_page.item is not None:
            current = self.transcript_page.item
            match = next((i for i in self.items if i.title == current.title and i.kind == current.kind), None)
            if match is None:
                self.stack.setCurrentIndex(LIST)
            elif match.modified != current.modified or match.finalized != current.finalized:
                self.transcript_page.show_item(match, self.search.text())

    def apply_search(self) -> None:
        query = self.search.text()
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(16, 4, 16, 4)
        column.setSpacing(8)
        for job in self.queue.jobs:
            card = JobCard(job)
            card.stop_requested.connect(lambda j: j.stop())
            card.remove_requested.connect(self.remove_job)
            card.log_requested.connect(self.show_log)
            column.addWidget(card)
        shown = history.search(self.items, query)
        for item in shown:
            card = HistoryCard(item)
            card.opened.connect(self.open_item)
            self.connect_item_actions(card)
            column.addWidget(card)
        if not shown and not self.queue.jobs:
            empty = QLabel("No results found\nTry different search terms" if query.strip() else
                           "No transcripts yet\nPress the record button below, or drop an audio file here")
            empty.setObjectName("secondary")
            empty.setAlignment(Qt.AlignCenter)
            column.addSpacing(80)
            column.addWidget(empty)
        column.addStretch()
        scroll = self.list_area.verticalScrollBar().value()
        self.list_area.setWidget(container)
        self.list_area.verticalScrollBar().setValue(scroll)
        if self.stack.currentIndex() == TRANSCRIPT:
            self.transcript_page.mark(query)

    def connect_item_actions(self, source) -> None:
        source.copy_requested.connect(self.copy_item)
        source.reveal_requested.connect(self.reveal_item)
        source.finalize_requested.connect(self.finalize_item)
        source.delete_requested.connect(self.delete_item)

    def open_item(self, item: HistoryItem) -> None:
        self.transcript_page.show_item(item, self.search.text())
        self.stack.setCurrentIndex(TRANSCRIPT)

    def copy_item(self, item: HistoryItem) -> None:
        if item.transcript is not None:
            text = item.transcript.read_text(encoding="utf-8", errors="replace").split("\n", 2)[-1].strip()
            QGuiApplication.clipboard().setText(text)
            self.statusBar().showMessage("Copied", 2000)

    def reveal_item(self, item: HistoryItem) -> None:
        folder = item.session or (item.transcript.parent if item.transcript else Path(self.settings.output_dir))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def finalize_item(self, item: HistoryItem) -> None:
        if item.session is None:
            return
        self.run_job("finalize", item.title,
                     lambda: commands.finalize(self.settings, self.manager.models_dir, str(item.session)))

    def delete_item(self, item: HistoryItem) -> None:
        target = item.session or item.transcript
        if target is None:
            return
        what = "this live session (transcript and audio)" if item.session else "this transcript"
        if QMessageBox.question(self, "Move to trash", f"Move {what} to the trash?\n{target}") != QMessageBox.Yes:
            return
        if not QFile.moveToTrash(str(target)):
            QMessageBox.warning(self, "Move to trash", f"Could not move {target} to the trash.")
        self.stack.setCurrentIndex(LIST)
        self.refresh()

    # Jobs

    def run_job(self, kind: str, title: str, build: Callable[[], List[str]]) -> Optional[jobs.Job]:
        try:
            argv = build()
        except ValueError as error:
            QMessageBox.information(self, APP_NAME, str(error))
            return None
        self.stack.setCurrentIndex(LIST)
        return self.queue.submit(kind, title, argv)

    def on_job_finished(self, job: jobs.Job) -> None:
        """A done job leaves the list: its transcript card takes its place. A failed or stopped job stays."""
        if job.status == jobs.DONE:
            self.queue.remove(job)
        self.refresh()

    def remove_job(self, job: jobs.Job) -> None:
        self.queue.remove(job)
        self.refresh()

    def show_log(self, job: jobs.Job) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Log: {job.title}")
        dialog.resize(640, 420)
        text = QPlainTextEdit("\n".join(job.log))
        text.setReadOnly(True)
        QVBoxLayout(dialog).addWidget(text)
        dialog.show()

    def transcribe_paths(self, paths: List[str]) -> None:
        for path in paths:
            if Path(path).is_dir():
                self.run_job("folder", Path(path).name or path,
                             lambda p=path: commands.transcribe_folder(self.settings, self.manager.models_dir, p))
            elif Path(path).is_file():
                self.run_job("file", Path(path).name,
                             lambda p=path: commands.transcribe_file(self.settings, self.manager.models_dir, p))

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Audio or video files to transcribe")
        self.transcribe_paths(paths)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder of audio or video files to transcribe")
        if folder:
            self.transcribe_paths([folder])

    def enter_link(self) -> None:
        dialog = LinkDialog(self)
        if dialog.exec() == QDialog.Accepted:
            url = dialog.url()
            self.run_job("url", url, lambda: commands.transcribe_url(self.settings, self.manager.models_dir, url))

    def merge(self) -> None:
        dialog = MergeDialog(self.settings.output_dir, self)
        if dialog.exec() != QDialog.Accepted:
            return
        sources = dialog.sources()
        name = dialog.merged_name()
        argv = commands.merge(sources, name or "", self.settings.output_dir)
        if not name:
            del argv[argv.index("--name"):argv.index("--name") + 2]
        self.run_job("merge", name or "Merged transcript", lambda: argv)

    # Drag and drop

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
            self.drop_overlay.setGeometry(self.centralWidget().rect().adjusted(10, 10, -10, -10))
            self.drop_overlay.show()
            self.drop_overlay.raise_()

    def dragLeaveEvent(self, event) -> None:
        self.drop_overlay.hide()

    def dropEvent(self, event) -> None:
        self.drop_overlay.hide()
        self.transcribe_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    # Live recording

    def toggle_recording(self) -> None:
        if self.live is not None and self.live.running():
            self.live.stop()
            self.record.set_state(record_button.BUSY)
            return
        model = catalog.find(self.settings.model_id)
        if model is None or not catalog.is_installed(model, self.manager.models_dir):
            QMessageBox.information(self, APP_NAME, "Download and select a model first (Settings > Model).")
            self.open_settings()
            return
        try:
            argv = commands.live_start(self.settings, self.manager.models_dir)
        except ValueError as error:
            QMessageBox.information(self, APP_NAME, str(error))
            return
        self.live = jobs.LiveRecording(argv)
        self.live.state_changed.connect(self.on_live_state)
        self.live.line_added.connect(self.live_page.add_line)
        self.live.draft_changed.connect(self.live_page.set_draft)
        self.live.finished.connect(self.on_live_finished)
        notice = HEADPHONES_NOTE if self.settings.mic_enabled and self.settings.system_source != "none" else ""
        self.live_page.begin(notice)
        self.stack.setCurrentIndex(LIVE)
        self.record.set_state(record_button.RECORDING)
        try:
            self.live.start()
        except OSError as error:
            self.record.set_state(record_button.IDLE)
            self.stack.setCurrentIndex(LIST)
            QMessageBox.warning(self, APP_NAME, f"Cannot start the recording: {error}")

    def on_live_state(self, state: str) -> None:
        self.live_page.set_state(state)
        if state in ("stopping", "finishing"):
            self.record.set_state(record_button.BUSY)

    def on_live_finished(self, code: int) -> None:
        live = self.live
        self.record.set_state(record_button.IDLE)
        self.live_page.set_state("done" if code == 0 else "failed")
        self.refresh()
        if code == 0:
            self.stack.setCurrentIndex(LIST)
            return
        details = "\n".join(live.logs[-12:]) if live else ""
        box = QMessageBox(QMessageBox.Warning, APP_NAME, "The recording stopped with an error.", parent=self)
        box.setInformativeText(details[-1500:] or f"exit code {code}")
        box.exec()
        self.stack.setCurrentIndex(LIST)

    # Settings and quit

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.manager, self)
        dialog.exec()
        self.save_settings()
        self.refresh()

    def closeEvent(self, event) -> None:
        recording = self.live is not None and self.live.running()
        active = self.queue.active()
        if recording or active:
            parts = (["A recording is running. It stops, and its transcript can be finished later from the list."]
                     if recording else []) + ([f"{len(active)} job(s) will stop. They continue from where they "
                                               "stopped when you add them again."] if active else [])
            if QMessageBox.question(self, f"Quit {APP_NAME}?", "\n\n".join(parts)) != QMessageBox.Yes:
                event.ignore()
                return
        if recording:
            self.live.stop()
            if self.live.child is not None:
                try:
                    self.live.child.process.wait(WAIT_FOR_SAVE_SECONDS)
                except Exception:
                    pass
            self.live.kill()
        self.queue.stop_all()
        self.save_settings()
        event.accept()
