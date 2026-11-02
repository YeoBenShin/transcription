"""Input types. The router tries them in SOURCES order and uses the first that matches."""

from typing import List

from app.sources.base import Source
from app.sources.local_file import LocalFileSource
from app.sources.youtube import YouTubeSource

SOURCES: List[Source] = [
    LocalFileSource(),
    YouTubeSource(),
]
