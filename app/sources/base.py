"""Interface that every input type implements."""

from typing import Optional, Protocol

from app.models import Options, Transcript


class Source(Protocol):
    name: str

    def matches(self, raw: str) -> bool:
        """Return True if this source can handle the raw user input."""
        ...

    def get_transcript(self, raw: str, options: Options) -> Optional[Transcript]:
        """Run the stages for this input type. Return None if no transcript was produced."""
        ...
