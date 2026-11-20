"""Dialogs: first-run onboarding, settings (Model, Recording, Output), merge of transcripts, and a link to transcribe."""

import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from desktop import devices, permissions
from desktop.paths import APP_NAME
from desktop.settings import Settings
from desktop.ui.models_view import DownloadManager, ModelList

HEADPHONES_NOTE = ("With the microphone on, use headphones. With speakers, the microphone also hears the others, so "
                   "their lines show twice. Mute in the meeting app does not stop this recording.")


def scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(widget)
    return area


def section(title: str, subtitle: str = "") -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 8, 0, 0)
    layout.setSpacing(2)
    heading = QLabel(title)
    heading.setObjectName("heading")
    layout.addWidget(heading)
    if subtitle:
        note = QLabel(subtitle)
        note.setObjectName("secondary")
        note.setWordWrap(True)
        layout.addWidget(note)
    return box


class PermissionCheck(QObject):
    """Asks the macOS helper for the System Audio Recording permission in a thread."""

    result = Signal(str)

    def run(self, request: bool) -> None:
        target = permissions.request_system_audio if request else permissions.system_audio_status
        threading.Thread(target=lambda: self.result.emit(target()), daemon=True).start()


class RecordingSettings(QWidget):
    """System audio source, its device, and the microphone."""

    SOURCES_MAC = [("tap", "All system audio (no driver, macOS 14.4+)"), ("device", "Input device (for example BlackHole)"),
                   ("none", "Off (microphone only)")]
    SOURCES_WINDOWS = [("loopback", "What a playback device plays (loopback)"), ("device", "Input device (for example "
                       "VB-Cable)"), ("none", "Off (microphone only)")]

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section("System audio", "The sound of the meeting app and other apps. Lines get the label "
                                 "'Them:' when the microphone is also on."))
        form = QFormLayout()
        self.source = QComboBox()
        for value, text in (self.SOURCES_MAC if sys.platform == "darwin" else self.SOURCES_WINDOWS):
            self.source.addItem(text, value)
        self.source.setCurrentIndex(max(self.source.findData(settings.system_source), 0))
        self.source.currentIndexChanged.connect(self.on_source)
        form.addRow("Source", self.source)
        self.device = QComboBox()
        self.device.setEditable(True)
        self.device.currentTextChanged.connect(self.on_device)
        form.addRow("Device", self.device)
        layout.addLayout(form)

        self.permission_row = QWidget()
        row = QHBoxLayout(self.permission_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.permission_text = QLabel("System Audio Recording permission: checking…")
        self.permission_text.setObjectName("secondary")
        self.permission_text.setWordWrap(True)
        row.addWidget(self.permission_text, 1)
        self.permission_button = QPushButton("Allow")
        self.permission_button.clicked.connect(self.on_permission_button)
        row.addWidget(self.permission_button)
        layout.addWidget(self.permission_row)
        self.permission = PermissionCheck()
        self.permission.result.connect(self.on_permission)

        layout.addWidget(section("Microphone", "Your own voice. Lines get the label 'Me:'."))
        self.mic_enabled = QCheckBox("Record the microphone")
        self.mic_enabled.setChecked(settings.mic_enabled)
        self.mic_enabled.toggled.connect(self.on_mic)
        layout.addWidget(self.mic_enabled)
        mic_form = QFormLayout()
        self.mic_device = QComboBox()
        self.mic_device.addItem("Default input device", "")
        for name in devices.input_devices():
            self.mic_device.addItem(name, name)
        self.mic_device.setCurrentIndex(max(self.mic_device.findData(settings.mic_device), 0))
        self.mic_device.currentIndexChanged.connect(
            lambda: setattr(self.settings, "mic_device", self.mic_device.currentData() or ""))
        mic_form.addRow("Device", self.mic_device)
        layout.addLayout(mic_form)
        note = QLabel(HEADPHONES_NOTE)
        note.setObjectName("secondary")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        self.on_source()
        self.on_mic(settings.mic_enabled)
        if sys.platform == "darwin":
            self.permission.run(request=False)

    def on_source(self) -> None:
        source = self.source.currentData()
        self.settings.system_source = source
        self.device.blockSignals(True)
        self.device.clear()
        if source == "device":
            self.device.addItems(devices.input_devices())
        elif source == "loopback":
            self.device.addItem("Default playback device", "")
            self.device.addItems(devices.loopback_devices())
        self.device.setEditable(source == "device")
        if source in ("device", "loopback"):
            index = self.device.findText(self.settings.system_device)
            if index >= 0:
                self.device.setCurrentIndex(index)
            elif source == "device":
                self.device.setEditText(self.settings.system_device or "BlackHole")
        self.device.blockSignals(False)
        self.device.setEnabled(source in ("device", "loopback"))
        self.on_device(self.device.currentText())
        self.permission_row.setVisible(source == "tap")

    def on_device(self, text: str) -> None:
        source = self.settings.system_source
        if source == "loopback":
            self.settings.system_device = "" if self.device.currentIndex() <= 0 else text
        elif source == "device":
            self.settings.system_device = text

    def on_mic(self, enabled: bool) -> None:
        self.settings.mic_enabled = enabled
        self.mic_device.setEnabled(enabled)

    def on_permission_button(self) -> None:
        if self.permission_button.text() == "Open Settings":
            QDesktopServices.openUrl(QUrl(permissions.SETTINGS_URL))
            return
        self.permission_text.setText("System Audio Recording permission: answer the macOS prompt…")
        self.permission.run(request=True)

    def on_permission(self, status: str) -> None:
        texts = {
            permissions.GRANTED: "System Audio Recording is allowed.",
            permissions.DENIED: "System Audio Recording is denied. Allow it in System Settings > Privacy & Security.",
            permissions.UNDETERMINED: "System Audio Recording is not allowed yet.",
        }
        self.permission_text.setText(texts.get(status, "System Audio Recording permission is unknown. The first "
                                                       "recording asks for it."))
        self.permission_button.setVisible(status != permissions.GRANTED)
        self.permission_button.setText("Open Settings" if status == permissions.DENIED else "Allow")


class SettingsDialog(QDialog):
    """Model, Recording and Output tabs. Changes apply to `settings`; the caller saves them."""

    def __init__(self, settings: Settings, manager: DownloadManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.resize(520, 600)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        model_page = QWidget()
        model_layout = QVBoxLayout(model_page)
        model_layout.addWidget(section("Speech model", "Models download from Hugging Face into this computer. "
                                       "Parakeet models need Apple Silicon."))
        self.models = ModelList(manager, settings.model_id)
        self.models.model_selected.connect(lambda mid: setattr(self.settings, "model_id", mid))
        model_layout.addWidget(self.models)
        folder = QPushButton("Open models folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(manager.models_dir))))
        model_layout.addWidget(folder, alignment=Qt.AlignLeft)
        tabs.addTab(scroll(model_page), "Model")

        recording_page = QWidget()
        QVBoxLayout(recording_page).addWidget(RecordingSettings(settings))
        tabs.addTab(scroll(recording_page), "Recording")

        output_page = QWidget()
        output_layout = QVBoxLayout(output_page)
        output_layout.addWidget(section("Transcripts folder", "Transcripts (.txt), live sessions and their audio "
                                        "(.flac) are saved here."))
        row = QHBoxLayout()
        self.output = QLineEdit(settings.output_dir)
        self.output.textChanged.connect(lambda text: setattr(self.settings, "output_dir", text))
        row.addWidget(self.output, 1)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_output)
        row.addWidget(choose)
        output_layout.addLayout(row)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.settings.output_dir)))
        output_layout.addWidget(open_folder, alignment=Qt.AlignLeft)
        output_layout.addStretch()
        tabs.addTab(output_page, "Output")

        buttons = QDialogButtonBox()
        done = buttons.addButton("Done", QDialogButtonBox.AcceptRole)
        done.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Transcripts folder", self.settings.output_dir)
        if folder:
            self.output.setText(folder)


class OnboardingDialog(QDialog):
    """First run: download and select a model, choose what to record. Continue needs an installed, selected model."""

    def __init__(self, settings: Settings, manager: DownloadManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.resize(480, 680)
        layout = QVBoxLayout(self)
        welcome = QLabel("Welcome to")
        welcome.setObjectName("secondary")
        layout.addWidget(welcome)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        layout.addWidget(title)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(section("Model", "Download a model to get started. Base is a good first choice; on "
                                      "Apple Silicon, Parakeet v2 is faster and more accurate for English."))
        self.models = ModelList(manager, settings.model_id)
        self.models.model_selected.connect(self.on_model)
        manager.finished.connect(self.on_downloaded)
        body_layout.addWidget(self.models)
        body_layout.addWidget(RecordingSettings(settings))
        body_layout.addWidget(QLabel("You can change all of this later in Settings."))
        layout.addWidget(scroll(body), 1)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("primary")
        self.continue_button.clicked.connect(self.accept)
        layout.addWidget(self.continue_button, alignment=Qt.AlignRight)
        self.update_continue()

    def on_model(self, model_id: str) -> None:
        self.settings.model_id = model_id
        self.update_continue()

    def on_downloaded(self, model_id: str) -> None:
        if not self.models.selected_installed():
            self.models.select(model_id)

    def update_continue(self) -> None:
        self.continue_button.setEnabled(self.models.selected_installed())


class MergeDialog(QDialog):
    """Choose 2 or more transcripts on the same time axis, a label for each, and the name of the result."""

    def __init__(self, outdir: str, parent=None):
        super().__init__(parent)
        self.outdir = outdir
        self.setWindowTitle("Merge transcripts")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        note = QLabel("Merges transcripts of audio on the same time axis into one, in time order. Example: the "
                      "<name>.system.flac and <name>.mic.flac files of a live session, each transcribed. Each "
                      "line gets the label of its file.")
        note.setObjectName("secondary")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.grid = QGridLayout()
        self.grid.addWidget(QLabel("Label"), 0, 0)
        self.grid.addWidget(QLabel("Transcript"), 0, 1)
        layout.addLayout(self.grid)
        self.rows: List[Tuple[QLineEdit, QLineEdit]] = []
        for _ in range(2):
            self.add_row()
        add = QPushButton("Add transcript")
        add.clicked.connect(lambda: self.add_row())
        layout.addWidget(add, alignment=Qt.AlignLeft)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("default: the common start of the file names")
        form.addRow("Name", self.name)
        layout.addLayout(form)
        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.merge_button = buttons.addButton("Merge", QDialogButtonBox.AcceptRole)
        self.merge_button.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_button()

    def add_row(self, label: str = "", path: str = "") -> None:
        row = len(self.rows) + 1
        label_edit = QLineEdit(label)
        label_edit.setPlaceholderText(["Them", "Me"][row - 1] if row <= 2 else "")
        label_edit.setMaximumWidth(110)
        path_edit = QLineEdit(path)
        path_edit.setPlaceholderText("transcript .txt")
        path_edit.textChanged.connect(self.update_button)
        browse = QPushButton("Choose…")
        browse.clicked.connect(lambda: self.browse(path_edit))
        self.grid.addWidget(label_edit, row, 0)
        self.grid.addWidget(path_edit, row, 1)
        self.grid.addWidget(browse, row, 2)
        self.rows.append((label_edit, path_edit))

    def browse(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Transcript", self.outdir, "Transcripts (*.txt)")
        if path:
            target.setText(path)

    def update_button(self) -> None:
        self.merge_button.setEnabled(len(self.sources()) >= 2)

    def sources(self) -> List[Tuple[str, str]]:
        """(label, path) of each row with an existing file. An empty label uses the placeholder."""
        result = []
        for label, path in self.rows:
            if path.text().strip() and Path(path.text().strip()).is_file():
                result.append((label.text().strip() or label.placeholderText(), path.text().strip()))
        return result

    def merged_name(self) -> Optional[str]:
        return self.name.text().strip() or None


class LinkDialog(QDialog):
    """A YouTube video or Spotify episode link to transcribe."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transcribe a link")
        self.resize(480, 140)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("YouTube video or Spotify episode link"))
        self.link = QLineEdit()
        self.link.setPlaceholderText("https://www.youtube.com/watch?v=…")
        layout.addWidget(self.link)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = buttons.addButton("Transcribe", QDialogButtonBox.AcceptRole)
        self.ok.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.link.textChanged.connect(lambda t: self.ok.setEnabled(t.strip().startswith(("http://", "https://"))))
        self.ok.setEnabled(False)

    def url(self) -> str:
        return self.link.text().strip()
