// /stage — the window OBS captures. It only renders what the server says is on
// stage; keys (a presentation clicker sends PageDown/PageUp, Home/End jump to
// the first and last item) and a click (next) become intents. Double-click
// toggles full screen.
//
// /stage?freeze=<item> renders a stopped capture exactly once, from its frozen
// JSON, and marks <body data-ready="1"> — the headless browser that writes the
// session folder's live/captures/<item>.png waits for that (src/live/freeze.py);
// Results serves that picture from /api/sessions/<sid>/results/captures/<item>.png.

import { api } from '/static/js/ui.js';
import { connectLive, bindKeys, clickToAdvance } from '/static/js/live.js';
import { createStage, applyTheme } from '/static/js/stage-render.js';

// The letterbox around the scaled canvas is the theme's own background, which
// only the canvas can read (the theme scopes its variables under .stage-canvas).
function syncLetterbox() {
  const canvas = host.querySelector('.stage-canvas');
  if (canvas) document.body.style.background = getComputedStyle(canvas).backgroundColor;
}

const host = document.getElementById('stage');
const params = new URLSearchParams(location.search);
const freezeId = params.get('freeze');

if (freezeId) renderFrozen(freezeId);
else runLive();

async function renderFrozen(id) {
  document.body.classList.add('freeze');
  const stage = createStage(host);
  const [data, frozen] = await Promise.all([api('/api/live'), api(`/api/live/captures/${encodeURIComponent(id)}`)]);
  await applyTheme(data.plan); // the session font must be declared before the fonts are awaited
  syncLetterbox();
  const ctx = { plan: data.plan, state: { timers: {}, blackout: false }, now: Date.now(), result: frozen.result, names: !!frozen.names };
  stage.render(frozen.item, ctx);
  await stage.ready();
  void host.offsetHeight; // lay out first, so the fonts the item uses start loading
  await document.fonts.ready;
  stage.update(ctx);
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  document.body.dataset.ready = '1';
}

function runLive() {
  const stage = createStage(host);
  let plan = null;
  const preloaded = new Set();

  const live = connectLive('stage', {
    onPlan(p) {
      plan = p;
      applyTheme(p).then(syncLetterbox);
      draw();
    },
    onState() { draw(); },
  });

  function current() {
    if (!plan || !plan.active || !live.state) return null;
    return plan.run.items[live.state.index] || null;
  }

  function preload() {
    // The next two slides are fetched ahead so a click never shows a blank frame.
    const i = live.state ? live.state.index : 0;
    plan.run.items.slice(i + 1, i + 3).forEach((it) => {
      const file = it.slide_bg || it.slide_file; // the picture the stage will draw
      if (!file || preloaded.has(file)) return;
      preloaded.add(file);
      new Image().src = `/api/sessions/${encodeURIComponent(plan.session.id)}/slides/${file}`;
    });
  }

  function draw() {
    if (!plan || !live.state || live.state.plan_rev !== plan.rev) return;
    const ctx = { plan, state: live.state, now: live.now() };
    stage.render(current(), ctx);
    if (plan.active) preload();
    document.title = plan.active ? `Stage · ${plan.session.title}` : 'facilitation-suite · stage';
  }

  setInterval(() => {
    if (plan && live.state && live.state.plan_rev === plan.rev) stage.update({ plan, state: live.state, now: live.now() });
  }, 250);

  function sayHello() { live.hello(window.innerWidth, window.innerHeight); }
  window.addEventListener('resize', sayHello);
  sayHello();

  bindKeys(live);
  clickToAdvance(document, live, { wait: 280, cancelOn: 'dblclick' }); // a double-click is full screen, not two steps

  document.addEventListener('dblclick', () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(() => {});
  });

  let idle = null;
  document.addEventListener('mousemove', () => {
    document.body.classList.remove('idle');
    clearTimeout(idle);
    idle = setTimeout(() => document.body.classList.add('idle'), 2000);
  });
}
