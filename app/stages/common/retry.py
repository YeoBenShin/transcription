"""Run a function again after an exception, with exponential backoff."""

import time
from typing import Callable, TypeVar

T = TypeVar("T")

ATTEMPTS = 3
FIRST_DELAY = 2.0


class GaveUp(RuntimeError):
    pass


def retry(action: Callable[[], T], attempts: int = ATTEMPTS, first_delay: float = FIRST_DELAY) -> T:
    """Call action up to `attempts` times. Wait first_delay, then 2x, 4x ... between attempts.

    Only `Exception` is caught, so KeyboardInterrupt stops at once.
    Raise GaveUp, chained to the last error, when all attempts fail.
    """
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as error:
            print(f"\nattempt {attempt}/{attempts} failed ({type(error).__name__}: {error})")
            if attempt == attempts:
                raise GaveUp(f"failed after {attempts} attempts: {type(error).__name__}: {error}") from error
            delay = first_delay * 2 ** (attempt - 1)
            print(f"retrying in {delay:g}s")
            time.sleep(delay)
    raise AssertionError("unreachable")
