"""Qt widgets in tests draw off screen, so no window opens."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
