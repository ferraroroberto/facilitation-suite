"""Music: an item's track or playlist, synced to its timer, plus the presenter's own controls.

Wired to the live hub like ``CaptureService``: it hears timers start, pause,
reset and end, and items change, and it registers its actions.

**Timer sync** (an item with ``music``):

- the timer starts or resumes → the music fades in and plays (``start:
  with_timer``; music that item already started and paused resumes);
- the timer pauses → it fades out and pauses;
- the timer is reset or reaches 00:00 → it fades out and stops;
- ``start: on_enter`` plays when the item comes on stage; ``manual`` only
  from the presenter;
- leaving the item follows its ``on_leave``: ``fade_out`` (stop) or
  ``keep_playing``.

**Who wins** between the item's music and the presenter's: *the latest
command wins*, and an item's timer and leaving only act on music **that item
started itself** (its ``owner``). Pressing play in the presenter's Music
panel (or ``music_toggle`` with nothing playing) starts ad-hoc music that no
timer touches; a later timer start of an item with ``with_timer`` music
replaces it again. The same track already playing is adopted, not restarted.
Pausing, resuming or changing the volume by hand keeps the owner; stopping
clears it.

States (the presenter's chip): ``idle``, ``playing``, ``paused``, ``error``
(the detail says why). Nothing of it is saved: a restarted server starts
silent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from src.live.actions import Action, register
from src.live.hub import LiveHub
from src.music.backend import Backend, EventSink, MusicError, Track
from src.music.file_backend import FileBackend
from src.music.library import audio_files, resolve

logger = logging.getLogger(__name__)

DEFAULT_VOLUME = 80
DEFAULT_FADE_S = 2.0
FADE_OUT_LONG_S = 8.0  # music_fade_out: the slow "wind down" (end of a break)
RESET_FADE_S = 1.0

BackendFactory = Callable[[EventSink], Backend]


class MusicService:
    def __init__(self, live: LiveHub, backends: Optional[dict[str, BackendFactory]] = None) -> None:
        self.live = live
        factories = backends if backends is not None else {"file": FileBackend}
        self.backends: dict[str, Backend] = {kind: make(self._from_backend) for kind, make in factories.items()}
        self.state = "idle"
        self.detail = ""
        self.track: Optional[Track] = None  # what plays or is paused (or the last one, for the toggle)
        self.owner: Optional[str] = None  # the item whose timer drives it; None = ad hoc
        self.volume = DEFAULT_VOLUME
        self.fade_in_s = self.fade_out_s = DEFAULT_FADE_S
        self.sounding = False  # the backend confirmed sound is coming out
        self.play_id = 0
        self.backend: Optional[Backend] = None
        self._tracks: tuple[Any, list[dict[str, Any]]] = (None, [])
        live.extra_state.append(self.state_fields)
        live.timer_listeners.append(self._guard(self._on_timer))
        live.timer_end_listeners.append(self._guard(lambda item, end: self._on_timer_end(item)))
        live.item_listeners.append(self._guard(self._on_item))
        live.session_listeners.append(self._guard(lambda sid: self._halt(RESET_FADE_S)))
        live.reset_listeners.append(self._guard(lambda: self._halt(RESET_FADE_S)))
        register(Action("music_toggle", "Music play/pause", lambda h, a: self.toggle()))
        register(Action("music_stop", "Music stop (fades out)", lambda h, a: self.stop()))
        register(Action("music_fade_out", "Music slow fade-out", lambda h, a: self.stop(FADE_OUT_LONG_S)))
        register(Action("music_volume", "Music volume", lambda h, a: self.set_volume(_volume(a)), arg="n"))
        register(Action("music_play", "Play a track", lambda h, a: self.play_pick(str(a)), arg="track", stream_deck=False))

    # ------------------------------------------------------------- queries

    def tracks(self) -> list[dict[str, Any]]:
        """The session's tracks for the presenter's picker: every item's music, then the rest of ``audio/``.

        Rebuilt when the plan changes (one listing of ``audio/`` per plan revision)."""
        key = (self.live.session_id, self.live.plan_rev)
        if self._tracks[0] == key:
            return self._tracks[1]
        out: list[dict[str, Any]] = []
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for it in self.live.items:
            m = it.get("music")
            if not m:
                continue
            ref = m["path"] if m["source"] == "file" else m["uri"]
            if not ref:
                continue
            row = seen.get((m["source"], ref))
            if row is None:
                row = {"n": len(out) + 1, "kind": m["source"], "ref": ref, "label": _label(m["source"], ref),
                       "items": [], "music": m}
                seen[(m["source"], ref)] = row
                out.append(row)
            row["items"].append(it["id"])
        if self.live.folder is not None:
            for rel in audio_files(self.live.folder):
                if ("file", rel) not in seen:
                    out.append({"n": len(out) + 1, "kind": "file", "ref": rel, "label": _label("file", rel), "items": [], "music": None})
        self._tracks = (key, out)
        return out

    def state_fields(self) -> dict[str, Any]:
        return {"music": {
            "state": self.state, "detail": self.detail, "owner": self.owner, "volume": self.volume,
            "track": {"kind": self.track.kind, "label": self.track.label} if self.track and self.state != "idle" else None,
            "sounding": self.sounding,
            "backend": self.backend.snapshot() if self.backend is not None and self.state != "idle" else None,
            "tracks": [{k: v for k, v in t.items() if k != "music"} for t in self.tracks()],
        }}

    # ------------------------------------------------------------- actions

    def toggle(self) -> None:
        """Play/pause by hand. With nothing playing: the item's music, else the last track."""
        if self.state == "playing":
            self._pause(self.fade_out_s)
        elif self.state == "paused":
            self._resume(self.fade_in_s)
        else:
            cur = self.live.current()
            if cur and cur.get("music"):
                self._play_music(cur["music"], owner=None)
            elif self.track is not None:
                self._start(self.track, self.volume, self.fade_in_s, self.fade_out_s, owner=None)
            else:
                raise MusicError(409, "no_music", "No music on this item — pick a track in the presenter's Music card")
        self.live.push_state()

    def stop(self, fade_s: Optional[float] = None) -> None:
        self._stop(self.fade_out_s if fade_s is None else fade_s)
        self.live.push_state()

    def set_volume(self, volume: int) -> None:
        self.volume = volume
        if self.backend is not None and self.state in ("playing", "paused"):
            self.backend.set_volume(volume)
        self.live.push_state()

    def play_pick(self, pick: str) -> None:
        """The presenter's picker: a track by its number in ``tracks()``."""
        row = next((t for t in self.tracks() if str(t["n"]) == pick.strip()), None)
        if row is None:
            raise MusicError(404, "no_track", f"No track {pick!r} in this session")
        if row["music"] is not None:
            self._play_music(row["music"], owner=None)
        else:
            self._start(self._track("file", row["ref"], False), self.volume, DEFAULT_FADE_S, DEFAULT_FADE_S, owner=None)
        self.live.push_state()

    # --------------------------------------------------------------- hooks

    def _guard(self, fn: Callable[..., None]) -> Callable[..., None]:
        """Hub listeners never raise: a music problem must not stop the deck moving."""
        def run(*args: Any) -> None:
            try:
                fn(*args)
            except MusicError as exc:
                self._fail(str(exc))
            except Exception as exc:  # noqa: BLE001 — logged, shown on the chip, the deck carries on
                logger.exception("❌ music: %s failed", getattr(fn, "__name__", "listener"))
                self._fail(f"Music failed ({exc})")
        return run

    def _on_timer(self, event: str, item: dict[str, Any]) -> None:
        m, iid = item.get("music"), item["id"]
        owned = self.owner == iid
        if event == "start":
            if owned and self.state == "paused":
                self._resume((m or {}).get("fade_in_s", self.fade_in_s))
            elif m and m["start"] == "with_timer" and not (owned and self.state == "playing"):
                self._play_music(m, owner=iid)
        elif event == "pause" and owned and self.state == "playing":
            self._pause((m or {}).get("fade_out_s", self.fade_out_s))
        elif event == "reset" and owned:
            self._stop((m or {}).get("fade_out_s", self.fade_out_s))

    def _on_timer_end(self, item: dict[str, Any]) -> None:
        if self.owner == item["id"]:
            self._stop((item.get("music") or {}).get("fade_out_s", self.fade_out_s))

    def _on_item(self, prev: Optional[dict[str, Any]], cur: dict[str, Any]) -> None:
        if prev and self.owner == prev["id"]:
            pm = prev.get("music") or {}
            if pm.get("on_leave", "fade_out") == "fade_out":
                self._stop(pm.get("fade_out_s", self.fade_out_s))
        m = cur.get("music")
        if m and m["start"] == "on_enter" and not (self.owner == cur["id"] and self.state == "playing"):
            self._play_music(m, owner=cur["id"])

    def _halt(self, fade_s: float) -> None:
        """The session went live, closed or started over: silence, and a fresh track list."""
        self._stop(fade_s)
        self.track = None
        self._tracks = (None, [])

    # ------------------------------------------------------------ transitions

    def _track(self, source: str, ref: str, loop: bool) -> Track:
        if source == "file":
            if self.live.folder is None:
                raise MusicError(409, "not_live", "No session is live")
            path = resolve(self.live.folder, ref)
            if not path.is_file():
                raise MusicError(404, "missing_audio", f"{ref} is not in the session folder — pick it again in the Plan tab")
            return Track("file", str(path), _label("file", ref), loop)
        return Track("spotify", ref, _label("spotify", ref))

    def _play_music(self, m: dict[str, Any], owner: Optional[str]) -> None:
        ref = m["path"] if m["source"] == "file" else m["uri"]
        track = self._track(m["source"], ref, bool(m.get("loop")))
        if self.track == track and self.state in ("playing", "paused"):
            # Already this track: adopt it (the timer now drives it), don't start it over.
            self.owner = owner
            self.fade_in_s, self.fade_out_s = float(m["fade_in_s"]), float(m["fade_out_s"])
            if self.state == "paused":
                self._resume(self.fade_in_s)
            return
        self._start(track, int(m["volume"]), float(m["fade_in_s"]), float(m["fade_out_s"]), owner)

    def _start(self, track: Track, volume: int, fade_in_s: float, fade_out_s: float, owner: Optional[str]) -> None:
        backend = self.backends.get(track.kind)
        if backend is None:
            raise MusicError(409, "no_backend", f"{track.kind.capitalize()} playback is not set up in this app")
        if self.backend is not None and self.backend is not backend and self.state in ("playing", "paused"):
            self.backend.stop(min(fade_in_s, DEFAULT_FADE_S))
        self.play_id += 1
        backend.play(self.play_id, track, volume, fade_in_s)
        self.backend, self.track, self.owner = backend, track, owner
        self.volume, self.fade_in_s, self.fade_out_s = volume, fade_in_s, fade_out_s
        self.state, self.detail, self.sounding = "playing", "", False
        logger.info("ℹ️ music: play %s (%s, volume %d, fade in %.1f s, %s)", track.label, track.kind, volume,
                    fade_in_s, f"with {owner}" if owner else "by hand")
        self.live.event("music_play", track=track.label, kind=track.kind, owner=owner)

    def _pause(self, fade_s: float) -> None:
        if self.state != "playing" or self.backend is None:
            return
        self.backend.pause(fade_s)
        self.state = "paused"
        logger.info("ℹ️ music: pause %s (fade out %.1f s)", self.track.label if self.track else "", fade_s)
        self.live.event("music_pause")

    def _resume(self, fade_s: float) -> None:
        if self.state != "paused" or self.backend is None:
            return
        self.backend.resume(self.volume, fade_s)
        self.state = "playing"
        logger.info("ℹ️ music: resume %s (fade in %.1f s)", self.track.label if self.track else "", fade_s)
        self.live.event("music_resume")

    def _stop(self, fade_s: float) -> None:
        if self.state in ("playing", "paused") and self.backend is not None:
            self.backend.stop(fade_s)
            logger.info("ℹ️ music: stop %s (fade out %.1f s)", self.track.label if self.track else "", fade_s)
            self.live.event("music_stop")
        self.state, self.detail, self.owner, self.sounding = "idle", "", None, False

    def _fail(self, detail: str) -> None:
        logger.warning("⚠️ music: %s", detail)
        self.state, self.detail, self.owner, self.sounding = "error", detail, None, False
        self.live.event("music_error", detail=detail)

    # ------------------------------------------------------------- backends

    def _from_backend(self, event: str, play_id: int, detail: str) -> None:
        """A backend's thread reports; the state changes on the event loop."""
        loop = self.live.loop
        if loop is None:
            self._backend_event(event, play_id, detail)
        else:
            loop.call_soon_threadsafe(self._backend_event, event, play_id, detail)

    def _backend_event(self, event: str, play_id: int, detail: str) -> None:
        if play_id != self.play_id:
            return  # about a track that was replaced since
        if event == "playing":
            self.sounding = True
            logger.info("✅ music: sound is coming out — %s", detail)
        elif event == "ended" and self.state == "playing":
            logger.info("ℹ️ music: %s ended", detail)
            self.state, self.owner, self.sounding = "idle", None, False
            self.live.event("music_end")
        elif event == "error":
            self._fail(detail)
        else:
            return
        self.live.push_state()

    def close(self) -> None:
        for b in self.backends.values():
            b.close()


def _label(source: str, ref: str) -> str:
    if source == "file":
        return Path(ref).name
    return "Spotify " + (ref.split("spotify:")[-1].split(":")[0] if ref.startswith("spotify:") else "link")


def _volume(arg: Optional[str]) -> int:
    try:
        v = int(str(arg))
    except (TypeError, ValueError) as exc:
        raise MusicError(422, "bad_argument", "volume must be a number from 0 to 100") from exc
    if not 0 <= v <= 100:
        raise MusicError(422, "bad_argument", "volume must be a number from 0 to 100")
    return v
