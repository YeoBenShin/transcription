"""Find decoder loops in text: an n-gram that repeats again and again, or text that compresses too well."""

import zlib
from typing import List, Optional

MAX_GRAM = 8  # longest n-gram that is checked
MIN_COPIES = 3  # an n-gram this many times in a row is a loop
COMPRESSION_RATIO = 2.4  # text that zlib makes this many times smaller is a loop


def key(text: str) -> str:
    """Word text in lower case, letters and digits only."""
    return "".join(c for c in text.lower() if c.isalnum())


def loop_cut(words: List[str]) -> Optional[int]:
    """Index where the first loop repeats: the end of the first copy of an n-gram that comes
    MIN_COPIES or more times in a row. None if there is no loop.
    """
    keys = [key(w) for w in words]
    for i in range(len(keys)):
        for n in range(1, MAX_GRAM + 1):
            gram = keys[i:i + n]
            if len(gram) < n or not all(gram):
                break
            copies = 1
            while keys[i + copies * n:i + (copies + 1) * n] == gram:
                copies += 1
            if copies >= MIN_COPIES:
                return i + n
    return None


def compression_ratio(text: str) -> float:
    data = text.encode("utf-8")
    return len(data) / len(zlib.compress(data)) if data else 0.0


def has_loop(text: str) -> bool:
    """True if the text has a repeated n-gram (loop_cut) or compresses more than COMPRESSION_RATIO."""
    return loop_cut(text.split()) is not None or compression_ratio(text) > COMPRESSION_RATIO
