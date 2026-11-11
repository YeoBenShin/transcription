"""Output file naming, header line, name conflicts and temp cleanup, through pipeline.run."""

from datetime import date
from pathlib import Path

import pytest

from app import pipeline
from app.models import Line, Options, Transcript, output_name
from app.stages.common.transcribe import partial_path

NAME = "20261031-mira-Creating a C HTTP Server"


class FakeSource:
    """Source that matches 'fake:<id>' and returns fixed lines under NAME."""

    name = "fake"

    def __init__(self, text: str = "hello", audio: bool = False):
        self.text = text
        self.audio = audio

    def matches(self, raw: str) -> bool:
        return raw.startswith("fake:")

    def get_transcript(self, raw: str, options: Options) -> Transcript:
        source_id = raw[len("fake:"):]
        downloaded = None
        if self.audio:
            options.temp_dir.mkdir(parents=True, exist_ok=True)
            downloaded = options.temp_dir / f"{NAME}.webm"
            downloaded.write_bytes(b"audio")
            partial_path(options.temp_dir, NAME).write_text("{}\n")
        return Transcript(source_id, [Line(0.0, self.text)], "fake", NAME, downloaded)


@pytest.fixture
def options(tmp_path: Path) -> Options:
    return Options(outdir=tmp_path / "out")


def run(monkeypatch, options: Options, source_id: str, text: str = "hello", audio: bool = False) -> Path:
    monkeypatch.setattr(pipeline, "SOURCES", [FakeSource(text, audio)])
    [path] = pipeline.run(f"fake:{source_id}", options).paths
    return path


def txt_files(options: Options):
    return sorted(p.name for p in options.outdir.glob("*.txt"))


# --- header line and first write ---

def test_new_name_writes_name_txt_with_header(monkeypatch, options):
    path = run(monkeypatch, options, "AAAAAAAAAAA")

    assert path == options.outdir / f"{NAME}.txt"
    assert path.read_text() == f"{NAME}-AAAAAAAAAAA\n\n[00:00:00] hello\n"


# --- rerun of the same input ---

def test_rerun_same_input_replaces_its_file(monkeypatch, options):
    run(monkeypatch, options, "AAAAAAAAAAA", text="first")
    path = run(monkeypatch, options, "AAAAAAAAAAA", text="second")

    assert path == options.outdir / f"{NAME}.txt"
    assert "second" in path.read_text()
    assert txt_files(options) == [f"{NAME}.txt"]


# --- conflict: other input, same name ---

def test_other_input_same_name_writes_name_dash_id(monkeypatch, options):
    first = run(monkeypatch, options, "AAAAAAAAAAA", text="first")
    before = first.read_text()
    second = run(monkeypatch, options, "BBBBBBBBBBB", text="second")

    assert second == options.outdir / f"{NAME}-BBBBBBBBBBB.txt"
    assert second.read_text() == f"{NAME}-BBBBBBBBBBB\n\n[00:00:00] second\n"
    assert first.read_text() == before


def test_rerun_of_conflicting_input_replaces_its_dash_id_file(monkeypatch, options):
    run(monkeypatch, options, "AAAAAAAAAAA", text="first")
    run(monkeypatch, options, "BBBBBBBBBBB", text="second")
    path = run(monkeypatch, options, "BBBBBBBBBBB", text="second again")

    assert path == options.outdir / f"{NAME}-BBBBBBBBBBB.txt"
    assert "second again" in path.read_text()
    assert txt_files(options) == [f"{NAME}-BBBBBBBBBBB.txt", f"{NAME}.txt"]


def test_first_input_rerun_after_conflict_keeps_short_name(monkeypatch, options):
    run(monkeypatch, options, "AAAAAAAAAAA", text="first")
    run(monkeypatch, options, "BBBBBBBBBBB", text="second")
    path = run(monkeypatch, options, "AAAAAAAAAAA", text="first again")

    assert path == options.outdir / f"{NAME}.txt"
    assert "first again" in path.read_text()
    assert "second" in (options.outdir / f"{NAME}-BBBBBBBBBBB.txt").read_text()
    assert len(txt_files(options)) == 2


def test_three_inputs_same_name_each_get_own_file(monkeypatch, options):
    for source_id in ("AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"):
        run(monkeypatch, options, source_id, text=source_id)

    assert txt_files(options) == [
        f"{NAME}-BBBBBBBBBBB.txt", f"{NAME}-CCCCCCCCCCC.txt", f"{NAME}.txt",
    ]


@pytest.mark.parametrize(
    "existing",
    [
        "[00:00:00] file with no header line\n",  # file written before the header line existed
        "",  # empty file
        f"{NAME}-AAAAAAAAAAA-extra\n\nx\n",  # header that only starts with the expected one
    ],
)
def test_existing_file_with_other_first_line_is_kept(monkeypatch, options, existing):
    options.outdir.mkdir(parents=True)
    (options.outdir / f"{NAME}.txt").write_text(existing)

    path = run(monkeypatch, options, "AAAAAAAAAAA")

    assert path == options.outdir / f"{NAME}-AAAAAAAAAAA.txt"
    assert (options.outdir / f"{NAME}.txt").read_text() == existing


def test_conflict_file_name_has_unsafe_id_characters_removed(monkeypatch, options):
    run(monkeypatch, options, "AAAAAAAAAAA")
    path = run(monkeypatch, options, "rec: take 2?")

    assert path.name == f"{NAME}-rec take 2.txt"
    assert path.read_text().startswith(f"{NAME}-rec: take 2?\n")


# --- temp folder cleanup ---

def test_temp_files_and_empty_temp_folder_removed(monkeypatch, options):
    run(monkeypatch, options, "AAAAAAAAAAA", audio=True)

    assert not options.temp_dir.exists()


def test_temp_folder_kept_when_other_work_is_in_it(monkeypatch, options):
    options.temp_dir.mkdir(parents=True)
    other = options.temp_dir / "20200101-Other-Title.partial.jsonl"
    other.write_text("{}\n")

    run(monkeypatch, options, "AAAAAAAAAAA", audio=True)

    assert sorted(p.name for p in options.temp_dir.iterdir()) == [other.name]


# --- output_name ---

@pytest.mark.parametrize(
    "published, author, title, source_id, expected",
    [
        (date(2026, 10, 31), "mira", "C Server", "id", "20261031-mira-C Server"),
        (None, "mira", "C Server", "id", "00000000-mira-C Server"),
        (date(2026, 10, 31), None, "C Server", "id", "20261031-Unknown-C Server"),
        (date(2026, 10, 31), "  ", "C Server", "id", "20261031-Unknown-C Server"),
        (date(2026, 10, 31), "mira", None, "JT9JI_SMHk4", "20261031-mira-JT9JI_SMHk4"),
        (date(2026, 10, 31), "a/b", 'x:y?"z"', "id", "20261031-a b-x y z"),
    ],
)
def test_output_name(published, author, title, source_id, expected):
    assert output_name(published, author, title, source_id) == expected
