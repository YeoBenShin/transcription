"""Entry point of the packaged app.

No arguments: open the main window. `[-u] -m <module> [args]`: run that module as `python -m` does.
The app starts its child processes as `<executable> -m <module> ...`. From source the executable is
Python; in the packaged app it is this entry point, so the same command works in both.
"""

import multiprocessing
import runpy
import sys
from typing import List, Optional


def open_window() -> int:
    from desktop.app import main as gui_main

    return gui_main()


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    rest = argv[1:]
    if rest[:1] == ["-u"]:
        rest = rest[1:]
    if rest[:1] == ["-m"] and len(rest) >= 2:
        module = rest[1]
        sys.argv = [module, *rest[2:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return 0
    return open_window()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
