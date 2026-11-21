"""Available features: the coarse blocks of the suite a machine can switch off, and whether each can run here.

A block is one part that sits on one outside dependency (the meeting app's chat window, OBS, the local LLM hub,
the audio output, the Cloudflare tunnel, a tailnet certificate). Each is on by default; ``config.json``'s
``features`` object holds this machine's choice and is never committed. A block switched off hides its parts, stops
its background work and answers its API with one plain sentence; a block whose dependency is missing here stays
switchable but is reported unavailable, with the reason.

Two blocks had an on/off key of their own before this object existed: ``obs.enabled`` and ``reader.enabled``. A
config that still carries one of them and no ``features`` value for that block reads it as that block's choice, so
an existing ``config.json`` keeps meaning what it meant without being rewritten.

At import time this module loads nothing from the app, so the config, the services and the routers can all ask it.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

OFF_SENTENCE = "Switched off in Settings, Available features"


@dataclass(frozen=True)
class Block:
    """One feature block: its key in ``config.json``, its Settings title, what it covers, and where a change lands."""

    key: str
    title: str
    covers: str
    # None when a change applies at once; else the part that must restart for it to apply.
    restart: Optional[str] = None


BLOCKS: tuple[Block, ...] = (
    Block("chat_reader", "Chat reader",
          "Reading the Zoom, Teams or Meet chat window while a session is live, its source switch and its readiness test."),
    Block("obs", "OBS scene switching",
          "Switching OBS to each item's scene, the OBS chip and the OBS settings."),
    Block("copilot", "Live copilot",
          "Recording and transcribing the call through the local LLM hub, the hints, the overlay window and the recordings."),
    Block("music", "Music",
          "Item music, the presenter's Music card, the quiz sounds and the Spotify account."),
    Block("quiz_public", "Public quiz link",
          "The quiz player's public listener, the public-link check and the tray's Cloudflare tunnel. The quiz itself still runs.",
          restart="the tray"),
    Block("remote", "Phone remote",
          "The phone remote page, its pairing link and every request from another device."),
)

KEYS: tuple[str, ...] = tuple(b.key for b in BLOCKS)
DEFAULTS: dict[str, bool] = {key: True for key in KEYS}
# The older per-part switches a config may still carry: block key -> (config section, key).
LEGACY: dict[str, tuple[str, str]] = {"obs": ("obs", "enabled"), "chat_reader": ("reader", "enabled")}


def resolve(raw: Mapping[str, Any]) -> dict[str, bool]:
    """Every block's choice from a raw ``config.json`` object: ``features`` first, then a legacy switch, else on."""
    chosen = raw.get("features") if isinstance(raw.get("features"), dict) else {}
    out = dict(DEFAULTS)
    for key in KEYS:
        if isinstance(chosen.get(key), bool):
            out[key] = chosen[key]
        elif key in LEGACY:
            section, name = LEGACY[key]
            old = raw.get(section, {}).get(name) if isinstance(raw.get(section), dict) else None
            if isinstance(old, bool):
                out[key] = old
    return out


def enabled(cfg: Any, key: str) -> bool:
    """Whether block ``key`` is on in a loaded ``AppConfig`` (its ``features`` is already resolved)."""
    if key not in DEFAULTS:
        raise KeyError(key)
    return bool(cfg.features.get(key, True))


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _missing(key: str, cfg: Any) -> Optional[str]:
    """Why block ``key`` cannot run on this machine, or None. Looks only; it imports and opens nothing."""
    if key == "chat_reader":
        if sys.platform != "win32":
            return "the chat reader needs Windows"
        return None if _installed("comtypes") else "comtypes isn't installed"
    if key == "obs":
        return None if _installed("obsws_python") else "obsws-python isn't installed"
    if key in ("copilot", "music"):
        return None if _installed("miniaudio") else "miniaudio isn't installed"
    if key == "quiz_public":
        return _quiz_public_missing(cfg)
    if key == "remote":
        from src.certs import cert_paths

        return None if cert_paths() else "no tailnet certificate in webapp/certificates (scripts/gen_tailscale_cert.py)"
    raise KeyError(key)


def _quiz_public_missing(cfg: Any) -> Optional[str]:
    if not cfg.quiz.public_port:
        return "quiz.public_port is 0 in config.json"
    from src import tunnel

    if not Path(tunnel.config_path()).is_file():
        return "no webapp/cloudflared.yml (copy webapp/cloudflared.sample.yml)"
    return None if shutil.which("cloudflared") else "cloudflared isn't on PATH"


def status(cfg: Any) -> list[dict[str, Any]]:
    """Every block as Settings shows it: ``enabled``, ``available`` and, when it cannot run here, the ``reason``."""
    out = []
    for block in BLOCKS:
        reason = _missing(block.key, cfg)
        out.append({"key": block.key, "title": block.title, "covers": block.covers, "enabled": enabled(cfg, block.key),
                    "available": reason is None, "reason": reason, "restart": block.restart})
    return out


def off_keys(cfg: Any) -> list[str]:
    """The blocks switched off, for the pages that hide their parts (in the live snapshot)."""
    return [key for key in KEYS if not enabled(cfg, key)]
