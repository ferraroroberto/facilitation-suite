# Item timer state

An item's timer has four modes: idle, running, paused and done. They are declared once, in `src/live/timer_mode.py`, and derived by one function, `timer_mode`. Every reader asks it; none re-derives the mode from `running_since` or `done`. The convention behind this is project-scaffolding's `docs/implicit-state-machines.md`; the machine is declared in `.fleet.toml` as `item-timer`.

## Why it is declared

The mode was never named. `TimerState` is three plain fields (`elapsed`, `running_since`, `done`) and "never started" is a missing entry in `LiveHub.timers`. The stage, the presenter cockpit, the phone remote, the music restore and the hub's own guards each worked the mode out from those fields in their own way, in two languages, with their own wording. The mode also drives the capture window (a running timer opens the capture, a reset stops it) and the item's music, so two readers that disagreed spread the disagreement. The fields also allowed a combination that means nothing: a timer that is done and still counting.

## The modes

| Mode | Observation | Meaning |
|---|---|---|
| `idle` | no entry in `LiveHub.timers` | never started, or reset |
| `running` | entry, not done, `running_since` set | counting down; the end is scheduled |
| `paused` | entry, not done, `running_since` empty | started and stopped with time left |
| `done` | entry, `done` set, `running_since` empty | reached 00:00; nothing is counting |

Impossible, and refused by `timer_mode` with `InconsistentTimer` rather than mapped to a neighbour: `done` with `running_since` set, and a negative `total`.

## Transitions

| From | Event | To |
|---|---|---|
| idle | start (manual, on enter, with the capture) | running |
| running | pause, or the capture stopping by hand | paused |
| paused | start / resume | running |
| running | reaches 00:00 | done |
| done | start, or one more minute | running (a fresh run; the extra minute is a fresh 60 s) |
| paused, running | one more minute | unchanged mode, `total` + 60 s |
| any | reset | idle (the entry is removed) |

## Who reads it

- **`src/live/hub.py`** — the guards in `_timer_start`, `_timer_pause`, `_timer_end`, `start_timer_for`, `pause_timer_for`, `timer_toggle`, `timer_add_minute` and the on-enter start in `goto` call `timer_mode`.
- **`src/music/service.py`** — the restore after a restart: a done timer drops its music (unless it keeps playing), a paused one restores it paused.
- **The snapshot and `live/state.json`** — `TimerState.as_dict` adds `mode` beside `total`, `elapsed`, `running_since` and `done`. The old fields stay and come from the same state, so a file written before the field existed still loads. An item with no entry has no key; the clients read that as `idle`.
- **The views** — `timerMode(t)` in `app/webapp/static/js/live.js` returns `t.mode`, or `idle` for no entry. The stage (`stage-render.js`), the presenter cockpit (`presenter.js`) and the phone remote (`remote.js`) use it; the label, colour and button wording per mode stay in each view. The remaining-seconds maths (`remaining`) still uses `total`, `elapsed` and `running_since`; that is a quantity, not a mode.

## Restoring from `live/state.json`

`restore_timer` reads each timer back and normalises what the file should never hold, logging each case at info level: a done timer that is still counting comes back done (the end won; its elapsed time is its total), and a negative `total` is dropped, so the item starts from its configured length. A running timer has its end rescheduled.

## Proof

`tests/test_timer_mode.py` holds the table (one row per reachable observation, plus the impossible ones and the absent entry), the snapshot `mode` at every step through the real hub, and the restore of a running, a paused, a done and an inconsistent timer from a state file. The test carrying `state-machine-proof: item-timer` is the one that shows an illegal combination is refused.

## Adding a mode

Add it to `TimerMode` with its meaning in `MEANINGS`, teach `timer_mode` the observation, then handle it in each reader above. A reader never gets its own flag for it.
