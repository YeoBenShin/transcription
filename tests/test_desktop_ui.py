"""Main window flows with pytest-qt, off screen: list and search, drop and add jobs, the record button with a fake
`live start`, onboarding Continue. Child processes are small Python scripts; no audio device, no model."""

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from desktop import commands, jobs  # noqa: E402
from desktop.settings import Settings  # noqa: E402
from desktop.ui import main_window, record_button  # noqa: E402
from desktop.ui.cards import HistoryCard, JobCard  # noqa: E402
from desktop.ui.dialogs import OnboardingDialog  # noqa: E402
from desktop.ui.models_view import DownloadManager  # noqa: E402


@pytest.fixture
def models_dir(tmp_path):
    folder = tmp_path / "models"
    (folder / "whisper-base").mkdir(parents=True)
    return folder


@pytest.fixture
def window(qtbot, tmp_path, models_dir, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    out = tmp_path / "out"
    (out / "live" / "s1").mkdir(parents=True)
    (out / "live" / "s1" / "s1.txt").write_text("s1-live\n\n[00:00:01] Them: Budget review.\n", encoding="utf-8")
    (out / "talk.txt").write_text("talk-x\n\n[00:00:00] Hiring plan.\n", encoding="utf-8")
    settings = Settings(model_id="whisper-base", output_dir=str(out), system_source="tap", onboarded=True)
    win = main_window.MainWindow(settings, lambda: None, DownloadManager(models_dir))
    win.messages = messages
    qtbot.addWidget(win)
    return win


def cards(win, kind):
    return win.list_area.widget().findChildren(kind)


def test_list_shows_transcripts_and_search_filters(window, qtbot):
    assert {c.item.title for c in cards(window, HistoryCard)} == {"s1", "talk"}
    window.search.setText("budget")
    qtbot.waitUntil(lambda: [c.item.title for c in cards(window, HistoryCard)] == ["s1"], timeout=3000)


def test_open_card_shows_transcript_page(window, qtbot):
    card = next(c for c in cards(window, HistoryCard) if c.item.title == "talk")
    qtbot.mouseClick(card, Qt.LeftButton)
    assert window.stack.currentIndex() == main_window.TRANSCRIPT
    assert "Hiring plan." in window.transcript_page.text.toPlainText()


def test_drop_file_and_folder_adds_jobs(window, tmp_path, monkeypatch):
    submitted = []
    monkeypatch.setattr(window.queue, "submit", lambda kind, title, argv: submitted.append((kind, title, argv)))
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"x")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(audio)), QUrl.fromLocalFile(str(inbox))])
    window.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier))
    assert [(k, t) for k, t, _ in submitted] == [("file", "a.m4a"), ("folder", "inbox")]
    assert submitted[0][2][3:5] == ["app", str(audio)]
    assert submitted[1][2][3:6] == ["app.batch", "--inbox", str(inbox)]


def test_job_card_shows_while_running_and_leaves_when_done(window, qtbot):
    job = window.queue.submit("file", "x.m4a", [sys.executable, "-c", "import time; time.sleep(1)"])
    assert [c.job.title for c in cards(window, JobCard)] == ["x.m4a"]
    qtbot.waitUntil(lambda: job.status == jobs.DONE, timeout=10000)
    qtbot.waitUntil(lambda: cards(window, JobCard) == [], timeout=3000)


def test_failed_job_card_stays(window, qtbot):
    job = window.queue.submit("file", "y.m4a", [sys.executable, "-c", "raise SystemExit(1)"])
    qtbot.waitUntil(lambda: job.status == jobs.FAILED, timeout=10000)
    assert [c.job.title for c in cards(window, JobCard)] == ["y.m4a"]


def test_record_without_installed_model_asks_for_one(window, monkeypatch):
    window.settings.model_id = "whisper-small"
    monkeypatch.setattr(window, "open_settings", lambda: None)
    window.toggle_recording()
    assert window.live is None and "model" in window.messages[0]


FAKE_LIVE = """
import json, sys
def say(**e): print(json.dumps(e), flush=True)
say(event="session", folder=sys.argv[1])
say(event="state", state="recording")
say(event="line", start=1.0, text="Them: Hello.")
say(event="draft", start=2.0, agreed="Me: Hi", tail="th", label="")
sys.stdin.read()
say(event="state", state="finishing")
say(event="state", state="done")
"""


def test_record_button_runs_live_and_stop_finishes(window, qtbot, tmp_path, monkeypatch):
    script = tmp_path / "fake_live.py"
    script.write_text(FAKE_LIVE)
    monkeypatch.setattr(commands, "live_start", lambda settings, models: [sys.executable, str(script), "/s"])
    qtbot.mouseClick(window.record, Qt.LeftButton)
    assert window.stack.currentIndex() == main_window.LIVE
    assert window.record.state == record_button.RECORDING
    qtbot.waitUntil(lambda: "Them: Hello." in window.live_page.text.toPlainText(), timeout=10000)
    qtbot.waitUntil(lambda: "Me: Hi th" in window.live_page.text.toPlainText(), timeout=10000)
    with qtbot.waitSignal(window.live.finished, timeout=10000):
        qtbot.mouseClick(window.record, Qt.LeftButton)
    assert window.record.state == record_button.IDLE
    assert window.stack.currentIndex() == main_window.LIST


def test_record_with_nothing_to_record_explains(window):
    window.settings.system_source = "none"
    window.settings.mic_enabled = False
    window.toggle_recording()
    assert window.live is None and "nothing to record" in window.messages[0]


def test_onboarding_continue_needs_installed_selected_model(qtbot, tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    settings = Settings()
    dialog = OnboardingDialog(settings, DownloadManager(models))
    qtbot.addWidget(dialog)
    assert not dialog.continue_button.isEnabled()
    (models / "whisper-tiny").mkdir()  # as a finished download leaves it
    dialog.on_downloaded("whisper-tiny")
    assert dialog.continue_button.isEnabled() and settings.model_id == "whisper-tiny"
