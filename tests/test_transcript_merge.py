"""transcript_merge: read app-format transcripts, merge by timestamp, write one transcript."""

from pathlib import Path

import pytest

from app.models import Line
from transcript_merge.cli import main, parse_source
from transcript_merge.merge import BadTranscript, Source, default_name, merge, read_lines


def write(path: Path, header: str, rows) -> Path:
    path.write_text((f"{header}\n\n" if header else "") + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_read_lines_skips_header_and_blank_lines(tmp_path):
    path = write(tmp_path / "a.txt", "20261120-X-a-local", ["[00:00:01] one", "", "[01:02:03] two"])
    assert read_lines(path) == [Line(1, "one"), Line(3723, "two")]


def test_read_lines_without_header(tmp_path):
    path = write(tmp_path / "a.txt", "", ["[00:00:05] five"])
    assert read_lines(path) == [Line(5, "five")]


def test_line_with_no_timestamp_continues_the_line_before(tmp_path):
    path = write(tmp_path / "a.txt", "h", ["[00:00:01] first part", "second part"])
    assert read_lines(path) == [Line(1, "first part second part")]


def test_text_before_first_timestamp_is_an_error(tmp_path):
    path = write(tmp_path / "a.txt", "h", ["", "loose text", "[00:00:01] x"])
    with pytest.raises(BadTranscript, match="a.txt:4"):
        read_lines(path)


def test_merge_sorts_by_time_and_adds_labels(tmp_path):
    them = write(tmp_path / "s.txt", "h", ["[00:00:00] hello", "[00:00:10] question?"])
    me = write(tmp_path / "m.txt", "h", ["[00:00:04] hi", "[00:00:12] answer."])
    assert merge([Source(them, "Them"), Source(me, "Me")]) == [
        Line(0, "Them: hello"), Line(4, "Me: hi"), Line(10, "Them: question?"), Line(12, "Me: answer."),
    ]


def test_same_time_keeps_source_order_then_file_order(tmp_path):
    a = write(tmp_path / "a.txt", "h", ["[00:00:05] a1", "[00:00:05] a2"])
    b = write(tmp_path / "b.txt", "h", ["[00:00:05] b1"])
    assert [l.text for l in merge([Source(b), Source(a)])] == ["b1", "a1", "a2"]


def test_default_name_is_common_start_of_names(tmp_path):
    sources = [Source(tmp_path / "20261120-X-meet.system.txt"), Source(tmp_path / "20261120-X-meet.mic.txt")]
    assert default_name(sources) == "20261120-X-meet"
    assert default_name([Source(tmp_path / "a.txt"), Source(tmp_path / "b.txt")]) is None


def test_parse_source_label_and_path(tmp_path):
    assert parse_source("Me=x/m.txt") == Source(Path("x/m.txt"), "Me")
    assert parse_source("x/m.txt") == Source(Path("x/m.txt"))
    odd = write(tmp_path / "a=b.txt", "h", ["[00:00:00] x"])
    assert parse_source(str(odd)) == Source(odd)


def test_cli_writes_merged_transcript_next_to_first_input(tmp_path, capsys):
    them = write(tmp_path / "meet.system.txt", "meet.system-local", ["[00:00:00] hello"])
    me = write(tmp_path / "meet.mic.txt", "meet.mic-local", ["[00:00:02] hi"])

    assert main([f"Them={them}", f"Me={me}"]) == 0

    assert (tmp_path / "meet.txt").read_text() == "meet-merged\n\n[00:00:00] Them: hello\n[00:00:02] Me: hi\n"
    assert "2 lines from 2 transcripts" in capsys.readouterr().out


def test_cli_does_not_replace_another_transcript_with_the_same_name(tmp_path):
    them = write(tmp_path / "meet.system.txt", "h", ["[00:00:00] hello"])
    me = write(tmp_path / "meet.mic.txt", "h", ["[00:00:02] hi"])
    live = write(tmp_path / "meet.txt", "meet-live", ["[00:00:00] live text"])

    assert main([f"Them={them}", f"Me={me}"]) == 0

    assert live.read_text().startswith("meet-live")
    assert (tmp_path / "meet-merged.txt").read_text().startswith("meet-merged")


def test_cli_name_and_outdir_options(tmp_path):
    a = write(tmp_path / "a.txt", "h", ["[00:00:00] x"])
    out = tmp_path / "out"
    assert main([str(a), "--name", "call", "--outdir", str(out)]) == 0
    assert (out / "call.txt").exists()


def test_cli_missing_file_fails(tmp_path, capsys):
    assert main([f"Me={tmp_path / 'none.txt'}"]) == 1
    assert "no such file" in capsys.readouterr().err
