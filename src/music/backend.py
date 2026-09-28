"""What a music backend is: one source of sound behind one interface.

``MusicService`` (``service.py``) decides *when* music plays; a backend only
makes the sound. Every method returns at once — a backend does its work on
its own thread and never blocks the server's event loop. What happens later
(the file ended, the device failed) comes back through the ``on_event``
callback the service hands it, from that thread; the service hops it onto
the loop.

Volumes are 0–100 (the plan's and the presenter's scale); fades are in
seconds, ``0`` = at once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from src.errors import DomainError


class MusicError(DomainError):
    """A music command that cannot run (no track, a file that is not there or cannot be decoded)."""

# (event, play_id, detail): "playing" (sound is coming out), "ended" (a file
# reached its end without loop), "error" (the message says what).
EventSink = Callable[[str, int, str], None]


@dataclass(frozen=True)
class Track:
    """One thing to play: a file (``ref`` = its absolute path) or a Spotify URI."""

    kind: str  # "file" | "spotify"
    ref: str
    label: str
    loop: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Backend(Protocol):
    kind: str

    def play(self, play_id: int, track: Track, volume: int, fade_in_s: float) -> None:
        """Start ``track`` from its beginning, fading in to ``volume``; whatever played before stops."""

    def pause(self, fade_s: float) -> None:
        """Fade out, then hold the position."""

    def resume(self, volume: int, fade_s: float) -> None:
        """Carry on from where it paused, fading in to ``volume``."""

    def stop(self, fade_s: float) -> None:
        """Fade out, then forget the track."""

    def set_volume(self, volume: int) -> None:
        """Change the level while playing (a short ramp, no click)."""

    def fade(self, to: int, seconds: float) -> None:
        """Ramp the level to ``to`` over ``seconds`` and keep playing."""

    def snapshot(self) -> dict[str, Any]:
        """What the presenter shows about the backend itself (device, position)."""

    def close(self) -> None:
        """The server is stopping: silence now and release the device."""
