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

**Sound cues** (``cue(name)``, the quiz's phase sounds, #53): a short file
from the session's ``audio/`` folder (``audio/<name>.mp3|ogg|wav|flac``;
silent when there is none). A cue **never stomps real music**: while an
item's music or ad-hoc music is playing or paused, the cue is skipped (and
logged), and that music carries on untouched. A cue only replaces nothing or
another cue (crossfading over it), and ``end_cue()`` fades out only a cue.
Any other command (a timer's music, the presenter's play or stop) replaces a
cue like any track: the cue is forgotten. The presenter's pause, stop and
volume act on a cue as on any track.

States (the presenter's chip): ``idle``, ``playing``, ``paused``, ``error``
(the detail says why). ``output_device()`` (#102) is a separate, lightweight
check — whether this PC has an audio output device at all — used by the
quiz's Sounds chip alongside its cue-file inventory (``src/quiz/cues.py``).

**After a restart** (#95): what plays or is paused — its source, position,
volume, fades and owner (the item whose timer drives it, or none: ad hoc) — is
the ``music`` section of ``live/state.json``, written with every change. When
the session comes back after a restart (not after the presenter's Close), the
music comes back too, near its position (the time the server was down counts
as played, like the timer's):

- an item's music resumes, fading in, only while that item is still on stage
  and its timer — if it started one — is running; with the timer paused it
  comes back paused (the timer's resume resumes it); anything else (another
  item on stage, the timer reset or ended) stays silent;
- ad-hoc music resumes, fading in, if it was playing, or comes back paused;
- a sound cue is never saved.

Music that comes back paused holds its position without a track loaded; its
resume starts it there. Spotify keeps its own position in the desktop app, so
its resume carries on from wherever the app is.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from src.live.actions import Action, register
from src.live.hub import LiveHub, now_ms
from src.music.backend import Backend, EventSink, MusicError, Track
from src.music.file_backend import FileBackend
from src.music.library import audio_files, cue_file, resolve
from src.music.spotify import SpotifyBackend, is_link, normalize
from src.music.spotify import label as spotify_label

logger = logging.getLogger(__name__)

DEFAULT_VOLUME = 80
DEFAULT_FADE_S = 2.0
FADE_OUT_LONG_S = 8.0  # music_fade_out: the slow "wind down" (end of a break)
RESET_FADE_S = 1.0
STATE_KEY = "music"  # its section of live/state.json
CUE_FADE_IN_S = 0.2  # a cue starts on its beat (a short ramp only, no click)
CUE_FADE_OUT_S = 1.0

BackendFactory = Callable[[EventSink], Backend]


class MusicService:
    def __init__(self, live: LiveHub, backends: Optional[dict[str, BackendFactory]] = None) -> None:
        self.live = live
        factories = backends if backends is not None else {"file": FileBackend, "spotify": SpotifyBackend}
        self.backends: dict[str, Backend] = {kind: make(self._from_backend) for kind, make in factories.items()}
        self.state = "idle"
        self.detail = ""
        self.track: Optional[Track] = None  # what plays or is paused (or the last one, for the toggle)
        self.owner: Optional[str] = None  # the item whose timer drives it; None = ad hoc
        self.cue_name: Optional[str] = None  # what plays is this sound cue (see cue())
        self.volume = DEFAULT_VOLUME
        self.fade_in_s = self.fade_out_s = DEFAULT_FADE_S
        self.sounding = False  # the backend confirmed sound is coming out
        self.play_id = 0
        self.backend: Optional[Backend] = None
        # The position (s) at ``_pos_at`` (epoch ms, None while paused) — kept here for every backend.
        self._pos_s, self._pos_at = 0.0, None
        self._held = False  # paused after a restart: nothing loaded in the backend, resume starts at _pos_s
        self._tracks: tuple[Any, list[dict[str, Any]]] = (None, [])
        live.extra_state.append(self.state_fields)
        live.persisted[STATE_KEY] = self.saved
        live.timer_listeners.append(self._guard(self._on_timer))
        live.timer_end_listeners.append(self._guard(lambda item, end: self._on_timer_end(item)))
        live.item_listeners.append(self._guard(self._on_item))
        live.session_listeners.append(self._guard(self._on_session))
        live.reset_listeners.append(self._guard(lambda: self._halt(RESET_FADE_S)))
        register(Action("music_toggle", "Music play/pause", lambda h, a: self.toggle()))
        register(Action("music_stop", "Music stop (fades out)", lambda h, a: self.stop()))
        register(Action("music_fade_out", "Music slow fade-out", lambda h, a: self.stop(FADE_OUT_LONG_S)))
        register(Action("music_next", "Music next track", lambda h, a: self.skip("next")))
        register(Action("music_prev", "Music previous track", lambda h, a: self.skip("previous")))
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
            "state": self.state, "detail": self.detail, "owner": self.owner, "cue": self.cue_name,
            "volume": self.volume,
            "track": {"kind": self.track.kind, "label": self.track.label} if self.track and self.state != "idle" else None,
            "sounding": self.sounding,
            "backend": self.backend.snapshot() if self.backend is not None and self.state != "idle" else None,
            "tracks": [{k: v for k, v in t.items() if k != "music"} for t in self.tracks()],
            "spotify": self._spotify_configured(),
        }}

    def _spotify_configured(self) -> bool:
        backend = self.backends.get("spotify")
        return bool(backend is not None and getattr(backend, "configured", lambda: False)())

    def spotify_status(self) -> tuple[str, str]:
        """The readiness check: (state, detail) — ``ok`` only when the token works and the desktop app is visible."""
        backend = self.backends.get("spotify")
        if backend is None or not hasattr(backend, "status"):
            return "unknown", "Spotify playback is not set up in this app"
        return backend.status()

    def spotify_account(self, *, fresh: bool = False) -> dict[str, Any]:
        """Settings → Music: the account's state (asks Spotify, cached 30 s unless ``fresh``), the
        device it plays on and when that was checked. Never the client id or a token."""
        backend = self.backends.get("spotify")
        if backend is None or not hasattr(backend, "status"):
            return {"state": "unknown", "detail": "Spotify playback is not set up in this app", "device": "", "checked_at": None}
        state, detail = backend.status(fresh=fresh)
        return {"state": state, "detail": detail, "device": getattr(backend, "status_device", ""),
                "checked_at": getattr(backend, "checked_at", None)}

    def spotify_login_saved(self) -> None:
        """A new Spotify login is in ``.env``: forget the old token and answers, tell the pages."""
        backend = self.backends.get("spotify")
        if backend is not None and hasattr(backend, "forget_login"):
            backend.forget_login()
        if self.live.loop is not None:
            self.live.loop.call_soon_threadsafe(self.live.push_state)

    def output_device(self) -> tuple[str, str]:
        """Whether this PC has an audio output device (#102): ``ok`` | ``none`` | ``unknown``.

        Only enumerates devices (miniaudio, a few ms) — never opens one, so this
        never makes a sound. ``unknown`` when the enumeration itself fails; never
        reported as ``ok``."""
        try:
            import miniaudio

            names = [d.get("name", "") for d in miniaudio.Devices().get_playbacks()]
        except Exception as exc:  # noqa: BLE001 — a query failure is "unknown", never "no device"
            logger.warning("⚠️ music: the output-device check failed (%s)", exc)
            return "unknown", f"Could not check for an audio output device ({exc})"
        if not names:
            return "none", "No audio output device on this PC — are the speakers or headset connected?"
        return "ok", names[0]

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
        self.live.commit()

    def stop(self, fade_s: Optional[float] = None) -> None:
        self._stop(self.fade_out_s if fade_s is None else fade_s)
        self.live.commit()

    def skip(self, direction: str) -> None:
        """The next or previous track of the playing Spotify playlist, album or artist (#191).

        Only while it plays: a file is a single track, and a paused Spotify would start playing
        on a skip. Spotify's own rule applies to previous: past the first seconds of a track it
        restarts that track."""
        if self.state != "playing" or self.track is None or self.backend is None:
            raise MusicError(409, "nothing_playing", "Nothing is playing — start the music first")
        if self.track.kind != "spotify" or not hasattr(self.backend, "skip"):
            raise MusicError(409, "single_track", "Next and previous track work on Spotify music; a file is one track")
        self.backend.skip(direction, self.volume)

    def set_volume(self, volume: int) -> None:
        self.volume = volume
        if self.backend is not None and self.state in ("playing", "paused"):
            self.backend.set_volume(volume)
        self.live.commit()

    def play_pick(self, pick: str) -> None:
        """The presenter's picker: a track by its number in ``tracks()``, or a pasted Spotify link."""
        if is_link(pick):
            self._start(self._track("spotify", pick, False), self.volume, DEFAULT_FADE_S, DEFAULT_FADE_S, owner=None)
            self.live.commit()
            return
        row = next((t for t in self.tracks() if str(t["n"]) == pick.strip()), None)
        if row is None:
            raise MusicError(404, "no_track", f"No track {pick!r} in this session")
        if row["music"] is not None:
            self._play_music(row["music"], owner=None)
        else:
            self._start(self._track("file", row["ref"], False), self.volume, DEFAULT_FADE_S, DEFAULT_FADE_S, owner=None)
        self.live.commit()

    # ---------------------------------------------------------------- cues

    def cue(self, name: str, *, loop: bool = False) -> bool:
        """Play the sound cue ``audio/<name>.*`` once (``loop``: until replaced or ended); True if it plays.

        Silent (False) when no session is live, the file is not there, or an item's or the
        presenter's music is playing or paused — a cue never stomps real music. The caller's
        change pushes the state (no push here)."""
        if self.live.folder is None:
            return False
        path = cue_file(self.live.folder, name)
        if path is None:
            logger.debug("music: no %s cue in audio/ — silent", name)
            return False
        if self.state in ("playing", "paused") and self.cue_name is None:
            logger.info("ℹ️ music: cue %s skipped — %s music is %s", name, "the item's" if self.owner else "ad-hoc",
                        self.state)
            return False
        self._start(Track("file", str(path), path.name, loop), self.volume, CUE_FADE_IN_S, CUE_FADE_OUT_S, owner=None,
                    cue=name)
        return True

    def end_cue(self, fade_s: float = CUE_FADE_OUT_S) -> None:
        """Fade out a cue that is still playing (or paused); any other music is left alone."""
        if self.cue_name is not None and self.state in ("playing", "paused"):
            self._stop(fade_s)

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

    def _on_session(self, sid: Optional[str]) -> None:
        self._halt(RESET_FADE_S)
        saved = self.live.restored.get(STATE_KEY) if sid else None
        if isinstance(saved, dict):
            self._restore(saved)

    # ------------------------------------------------------------- restart

    def position(self) -> float:
        """Seconds into the track (this service's own clock: every backend, no thread read)."""
        if self._pos_at is None:
            return self._pos_s
        return self._pos_s + max(0, now_ms() - self._pos_at) / 1000

    def saved(self) -> Optional[dict[str, Any]]:
        """The ``music`` section of ``live/state.json``: what plays or is paused (a cue never)."""
        if self.state not in ("playing", "paused") or self.track is None or self.cue_name is not None:
            return None
        ref = self.track.ref
        if self.track.kind == "file" and self.live.folder is not None:
            try:  # session-relative, like the plan's own music paths
                ref = Path(ref).relative_to(self.live.folder).as_posix()
            except ValueError:
                pass
        return {"kind": self.track.kind, "ref": ref, "loop": self.track.loop, "state": self.state,
                "position_s": round(self.position(), 2), "at_ms": now_ms(), "volume": self.volume,
                "fade_in_s": self.fade_in_s, "fade_out_s": self.fade_out_s, "owner": self.owner}

    def _restore(self, saved: dict[str, Any]) -> None:
        """The session came back after a restart: its music comes back as the module docstring says."""
        owner, was = saved.get("owner"), saved.get("state")
        at = saved.get("at_ms")
        down_s = max(0, now_ms() - at) / 1000 if was == "playing" and isinstance(at, int) else 0.0
        position = float(saved.get("position_s") or 0) + down_s
        play = was == "playing"
        if owner:
            cur, timer = self.live.current(), self.live.timers.get(owner)
            if cur is None or cur["id"] != owner or (timer is not None and timer.done):
                logger.info("ℹ️ music: %s's music not resumed — %s", owner,
                            "its timer ended" if cur and cur["id"] == owner else "its item is no longer on stage")
                return
            if timer is not None and timer.running_since is None:
                play = False  # the timer is paused: so is its music
        track = self._track(str(saved.get("kind")), str(saved.get("ref") or ""), bool(saved.get("loop")))
        volume = int(saved.get("volume", self.volume))
        fade_in, fade_out = float(saved.get("fade_in_s") or DEFAULT_FADE_S), float(saved.get("fade_out_s") or DEFAULT_FADE_S)
        if play:
            self._start(track, volume, fade_in, fade_out, owner, start_s=position)
            logger.info("ℹ️ music: resumed %s after a restart at %.1f s (%s)", track.label, position,
                        f"with {owner}" if owner else "ad hoc")
            return
        backend = self.backends.get(track.kind)
        if backend is None:
            raise MusicError(409, "no_backend", f"{track.kind.capitalize()} playback is not set up in this app")
        self.backend, self.track, self.owner, self.cue_name = backend, track, owner, None
        self.volume, self.fade_in_s, self.fade_out_s = volume, fade_in, fade_out
        self.state, self.detail, self.sounding, self._held = "paused", "", False, True
        self._pos_s, self._pos_at = position, None
        logger.info("ℹ️ music: %s back paused after a restart at %.1f s (%s)", track.label, position,
                    f"with {owner}" if owner else "ad hoc")

    # ------------------------------------------------------------ transitions

    def _track(self, source: str, ref: str, loop: bool) -> Track:
        if source == "file":
            if self.live.folder is None:
                raise MusicError(409, "not_live", "No session is live")
            path = resolve(self.live.folder, ref)
            if not path.is_file():
                raise MusicError(404, "missing_audio", f"{ref} is not in the session folder — pick it again in the Plan tab")
            return Track("file", str(path), _label("file", ref), loop)
        uri = normalize(ref)
        return Track("spotify", uri, spotify_label(uri))

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

    def _start(self, track: Track, volume: int, fade_in_s: float, fade_out_s: float, owner: Optional[str],
               cue: Optional[str] = None, start_s: float = 0.0) -> None:
        backend = self.backends.get(track.kind)
        if backend is None:
            raise MusicError(409, "no_backend", f"{track.kind.capitalize()} playback is not set up in this app")
        if (self.backend is not None and self.backend is not backend and self.state in ("playing", "paused")
                and not self._held):
            self.backend.stop(min(fade_in_s, DEFAULT_FADE_S))
        self.play_id += 1
        backend.play(self.play_id, track, volume, fade_in_s, start_s=start_s)
        self.backend, self.track, self.owner, self.cue_name = backend, track, owner, cue
        self.volume, self.fade_in_s, self.fade_out_s = volume, fade_in_s, fade_out_s
        self.state, self.detail, self.sounding, self._held = "playing", "", False, False
        self._pos_s, self._pos_at = start_s, now_ms()
        logger.info("ℹ️ music: play %s (%s, volume %d, fade in %.1f s, %s)", track.label, track.kind, volume,
                    fade_in_s, f"with {owner}" if owner else f"cue {cue}" if cue else "by hand")
        self.live.event("music_play", track=track.label, kind=track.kind, owner=owner, **({"cue": cue} if cue else {}))

    def _pause(self, fade_s: float) -> None:
        if self.state != "playing" or self.backend is None:
            return
        self.backend.pause(fade_s)
        self.state = "paused"
        self._pos_s, self._pos_at = self.position(), None
        logger.info("ℹ️ music: pause %s (fade out %.1f s)", self.track.label if self.track else "", fade_s)
        self.live.event("music_pause")

    def _resume(self, fade_s: float) -> None:
        if self.state != "paused" or self.backend is None or self.track is None:
            return
        if self._held:  # back paused after a restart: nothing loaded yet — start it where it was
            self._start(self.track, self.volume, fade_s, self.fade_out_s, self.owner, start_s=self._pos_s)
            return
        self.backend.resume(self.volume, fade_s)
        self.state = "playing"
        self._pos_at = now_ms()
        logger.info("ℹ️ music: resume %s (fade in %.1f s)", self.track.label if self.track else "", fade_s)
        self.live.event("music_resume")

    def _stop(self, fade_s: float) -> None:
        if self.state in ("playing", "paused") and self.backend is not None:
            if not self._held:  # held after a restart: nothing plays in the backend
                self.backend.stop(fade_s)
            logger.info("ℹ️ music: stop %s (fade out %.1f s)", self.track.label if self.track else "", fade_s)
            self.live.event("music_stop")
        self.state, self.detail, self.owner, self.sounding, self.cue_name = "idle", "", None, False, None
        self._held = False

    def _fail(self, detail: str) -> None:
        logger.warning("⚠️ music: %s", detail)
        self.state, self.detail, self.owner, self.sounding, self.cue_name = "error", detail, None, False, None
        self._held = False
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
            self.state, self.owner, self.sounding, self.cue_name = "idle", None, False, None
            self.live.event("music_end")
        elif event == "error":
            self._fail(detail)
        else:
            return
        self.live.commit()

    def close(self) -> None:
        for b in self.backends.values():
            b.close()


def _label(source: str, ref: str) -> str:
    if source == "file":
        return Path(ref).name
    return spotify_label(normalize(ref)) if is_link(ref) else "Spotify (not a valid link)"


def _volume(arg: Optional[str]) -> int:
    try:
        v = int(str(arg))
    except (TypeError, ValueError) as exc:
        raise MusicError(422, "bad_argument", "volume must be a number from 0 to 100") from exc
    if not 0 <= v <= 100:
        raise MusicError(422, "bad_argument", "volume must be a number from 0 to 100")
    return v
