"""Live transcription of the 2x speed fixture in real time: accuracy and speed limits.

Opt in with `pytest -m bench` (about 2 min, loads the `base` model). Prints the full result.
"""

import pytest

from tests.bench_live import DEFAULT_AUDIO, bench

MAX_WER = 0.20
MAX_WRITE_DELAY_MEDIAN = 4.5
MAX_WRITE_DELAY_P90 = 8.0
MAX_END_LAG = 10.0


@pytest.mark.bench
def test_live_transcription_of_fast_speech(tmp_path):
    result = bench(DEFAULT_AUDIO, DEFAULT_AUDIO.with_suffix(".txt"), "base", tmp_path)
    print(result)
    assert result["wer"] <= MAX_WER
    assert result["write_delay"]["median"] <= MAX_WRITE_DELAY_MEDIAN
    assert result["write_delay"]["p90"] <= MAX_WRITE_DELAY_P90
    assert result["end_lag"] <= MAX_END_LAG
