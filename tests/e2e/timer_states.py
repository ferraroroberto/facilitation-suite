"""The stage timer's four states (#211) as colours, read off a stage drawn in the page.

``TIMER_STATES`` draws a break (the big clock) and an activity (the pill) with each timer state
— not started, running, paused, ended — on a stage canvas that wears whatever theme the page has
linked, and returns the computed colours. The same stage-render code the live stage and the plan
previews run, so what it reports is what the stage shows.
"""

from __future__ import annotations

import re

TIMER_STATES = """async () => {
  const { createStage } = await import('/static/js/stage-render.js');
  const timer = { enabled: true, seconds: 60, start: 'manual', show_on: 'stage', end: 'keep' };
  const states = {
    idle: null,
    running: { total: 60, elapsed: 0, running_since: 1000, done: false },
    paused: { total: 60, elapsed: 10, running_since: null, done: false },
    done: { total: 60, elapsed: 60, running_since: null, done: true },
  };
  const out = {};
  for (const [name, t] of Object.entries(states)) {
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;left:0;top:0;width:1920px;height:1080px;z-index:99';
    document.body.appendChild(host);
    const stage = createStage(host);
    const ctx = (id) => ({ plan: { session: { id: 'x' }, run: { language: 'en' } }, now: 1000, result: null,
                           state: { timers: t ? { [id]: t } : {} } });
    stage.render({ id: 'brk', kind: 'break', title: 'Break', profile: 'camera_pip', zone: null, font: {}, timer }, ctx('brk'));
    const clock = getComputedStyle(host.querySelector('[data-clock]')).color;
    stage.render({ id: 'act', kind: 'activity', type: null, title: 'Q', profile: 'camera_pip', zone: null, font: {}, timer }, ctx('act'));
    const pill = getComputedStyle(host.querySelector('[data-pill]'));
    out[name] = { clock, pill: pill.backgroundColor, ink: pill.color,
                  pillClass: host.querySelector('[data-pill]').className };
    host.remove();
  }
  return out;
}"""


def rgb(css: str) -> tuple[int, int, int]:
    """A computed CSS colour — ``rgb(r, g, b)`` or ``color(srgb r g b)`` (what a colour-mix gives) — as 0–255 ints."""
    nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", css)]
    if css.startswith("color("):
        return tuple(round(n * 255) for n in nums[:3])  # type: ignore[return-value]
    return tuple(round(n) for n in nums[:3])  # type: ignore[return-value]


WHITE, DARK = (255, 255, 255), (31, 31, 31)
# Roberto's four state colours (#211): the pill's fill and ink, the big clock's text. The paused
# clock is the yellow darkened by a third, so it can be read on the light stage.
DEFAULT_STATES = {
    "idle": {"pill": (31, 31, 31), "ink": WHITE, "clock": (31, 31, 31)},
    "running": {"pill": (0, 164, 78), "ink": WHITE, "clock": (0, 164, 78)},
    "paused": {"pill": (242, 183, 5), "ink": DARK, "clock": (165, 124, 3)},
    "done": {"pill": (196, 12, 12), "ink": WHITE, "clock": (196, 12, 12)},
}


def close(a: tuple[int, int, int], b: tuple[int, int, int], tol: int = 3) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def assert_states(got: dict, want: dict) -> None:
    """Every state's pill fill, pill ink and clock text is the wanted colour."""
    for name, exp in want.items():
        g = got[name]
        assert rgb(g["pill"]) == exp["pill"], (name, "pill", g)
        assert rgb(g["ink"]) == exp["ink"], (name, "ink", g)
        assert close(rgb(g["clock"]), exp["clock"]), (name, "clock", g)
        assert g["pillClass"].split()[-1] == name, (name, g)  # the pill carries its state's class
