"""Work file lookup and temp name parsing used by the web Stop / Continue / Delete buttons."""

from pathlib import Path

from app.stages.common.temp_files import LOG_PREFIX, name_from_log, work_files

NAME = "20261031-mira-Title [live]"


def touch(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"x")


def test_work_files_match_progress_audio_and_incomplete_downloads(tmp_path):
    touch(
        tmp_path,
        f"{NAME}.partial.jsonl", f"{NAME}.webm", f"{NAME}.webm.part", f"{NAME}.m4a.ytdl",
        f"{NAME} 2.webm", f"{NAME}. Other.webm", f"{NAME}.a.b", "other.webm",
    )

    assert [p.name for p in work_files(tmp_path, NAME)] == sorted(
        [f"{NAME}.partial.jsonl", f"{NAME}.webm", f"{NAME}.webm.part", f"{NAME}.m4a.ytdl"]
    )


def test_work_files_empty_for_no_name_or_no_folder(tmp_path):
    touch(tmp_path, ".webm")
    assert work_files(tmp_path, "") == []
    assert work_files(tmp_path / "missing", NAME) == []


def test_name_from_log_takes_last_complete_line():
    log = f"x\n{LOG_PREFIX}first\n  [00:00:01] a\r{LOG_PREFIX}{NAME}\n{LOG_PREFIX}cut"
    assert name_from_log(log) == NAME
    assert name_from_log("no name here\n") == ""
