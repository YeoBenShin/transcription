"""Model download and selection: the download manager (downloads go on when a dialog closes) and the
model list used by onboarding and settings."""

import threading
from pathlib import Path
from typing import Dict

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from desktop.models import catalog, download
from desktop.models.catalog import ModelSpec
from desktop.ui.cards import icon_button


def size_text(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


class DownloadManager(QObject):
    """Runs each model download in its own thread. Signals come in the GUI thread."""

    progress = Signal(str, int, int)  # model id, bytes done, bytes total
    finished = Signal(str)
    failed = Signal(str, str)  # model id, message ("" when cancelled)

    def __init__(self, models_dir: Path):
        super().__init__()
        self.models_dir = models_dir
        self.cancel_flags: Dict[str, threading.Event] = {}
        self.last: Dict[str, tuple] = {}

    def is_downloading(self, model_id: str) -> bool:
        return model_id in self.cancel_flags

    def start(self, model: ModelSpec) -> None:
        if self.is_downloading(model.id):
            return
        cancel = threading.Event()
        self.cancel_flags[model.id] = cancel
        threading.Thread(target=self._run, args=(model, cancel), daemon=True).start()

    def cancel(self, model_id: str) -> None:
        flag = self.cancel_flags.get(model_id)
        if flag:
            flag.set()

    def _run(self, model: ModelSpec, cancel: threading.Event) -> None:
        def report(done: int, total: int) -> None:
            self.last[model.id] = (done, total)
            self.progress.emit(model.id, done, total)

        try:
            download.download(model, self.models_dir, report, cancelled=cancel.is_set)
        except download.Cancelled:
            self._end(model.id)
            self.failed.emit(model.id, "")
            return
        except (download.DownloadFailed, OSError) as error:
            self._end(model.id)
            self.failed.emit(model.id, str(error))
            return
        self._end(model.id)
        self.finished.emit(model.id)

    def _end(self, model_id: str) -> None:
        self.cancel_flags.pop(model_id, None)
        self.last.pop(model_id, None)


class ModelRow(QFrame):
    """One model: name, note, size, and Download / Cancel / Select / Selected with Delete."""

    select_requested = Signal(str)
    deleted = Signal(str)

    def __init__(self, model: ModelSpec, manager: DownloadManager, parent=None):
        super().__init__(parent)
        self.model = model
        self.manager = manager
        self.selected = False
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        name = QLabel(model.title)
        name.setObjectName("heading")
        top.addWidget(name, 1)
        size = QLabel(size_text(model.size_bytes))
        size.setObjectName("secondary")
        top.addWidget(size)
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(lambda: self.manager.start(self.model))
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(lambda: self.manager.cancel(self.model.id))
        self.select_button = QPushButton("Select")
        self.select_button.clicked.connect(lambda: self.select_requested.emit(self.model.id))
        self.selected_label = QLabel("✓ Selected")
        self.delete_button = icon_button("trash", f"Delete {model.title}")
        self.delete_button.clicked.connect(self.delete)
        for widget in (self.download_button, self.cancel_button, self.select_button, self.selected_label,
                       self.delete_button):
            top.addWidget(widget)
        layout.addLayout(top)
        note = QLabel(model.note)
        note.setObjectName("secondary")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        layout.addWidget(self.bar)
        self.bar_text = QLabel()
        self.bar_text.setObjectName("secondary")
        layout.addWidget(self.bar_text)
        manager.progress.connect(self.on_progress)
        manager.finished.connect(lambda mid: mid == self.model.id and self.refresh())
        manager.failed.connect(self.on_failed)
        self.refresh()

    def installed(self) -> bool:
        return catalog.is_installed(self.model, self.manager.models_dir)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self.refresh()

    def refresh(self) -> None:
        downloading = self.manager.is_downloading(self.model.id)
        installed = self.installed()
        self.download_button.setVisible(not downloading and not installed)
        self.cancel_button.setVisible(downloading)
        self.select_button.setVisible(installed and not self.selected)
        self.selected_label.setVisible(installed and self.selected)
        self.delete_button.setVisible(installed and not self.selected)
        self.bar.setVisible(downloading)
        self.bar_text.setVisible(downloading)
        if downloading and self.model.id in self.manager.last:
            self.on_progress(self.model.id, *self.manager.last[self.model.id])

    def on_progress(self, model_id: str, done: int, total: int) -> None:
        if model_id != self.model.id:
            return
        if not self.bar.isVisible():
            self.refresh()
        self.bar.setValue(int(done * 1000 / total) if total else 0)
        self.bar_text.setText(f"{size_text(done)} of {size_text(total)}")

    def on_failed(self, model_id: str, message: str) -> None:
        if model_id != self.model.id:
            return
        self.refresh()
        if message:
            QMessageBox.warning(self, "Download failed", f"{self.model.title}: {message}")

    def delete(self) -> None:
        answer = QMessageBox.question(self, "Delete model", f"Delete {self.model.title} from this computer?")
        if answer == QMessageBox.Yes:
            catalog.delete(self.model, self.manager.models_dir)
            self.refresh()
            self.deleted.emit(self.model.id)


class ModelList(QWidget):
    """All models this computer can run. Selecting an installed model emits model_selected."""

    model_selected = Signal(str)

    def __init__(self, manager: DownloadManager, selected_id: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.rows = []
        for model in catalog.available():
            row = ModelRow(model, manager)
            row.select_requested.connect(self.select)
            row.deleted.connect(self.on_deleted)
            layout.addWidget(row)
            self.rows.append(row)
        layout.addStretch()
        self.select(selected_id, emit=False)

    def select(self, model_id: str, emit: bool = True) -> None:
        for row in self.rows:
            row.set_selected(row.model.id == model_id and row.installed())
        if emit:
            self.model_selected.emit(model_id)

    def on_deleted(self, model_id: str) -> None:
        """A deleted model cannot stay selected."""
        if any(row.model.id == model_id and row.selected for row in self.rows):
            self.select("")

    def selected_installed(self) -> bool:
        return any(row.selected for row in self.rows)
