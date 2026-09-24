// /stage — the window OBS captures. It only renders what the server says is on
// stage; keys (a presentation clicker sends PageDown/PageUp) become intents.
// Double-click toggles full screen.
//
// /stage?freeze=<item> renders a stopped capture exactly once, from its frozen
// JSON, and marks <body data-ready="1"> — the headless browser that writes
// live/captures/<item>.png waits for that (src/live/freeze.py).

import { api } from '/static/js/ui.js';
import { connectLive, bindKeys } from '/static/js/live.js';
import { createStage, applyTheme } from '/static/js/stage-render.js';

const host = document.getElementById('stage');
const params = new URLSearchParams(location.search);
const freezeId = params.get('freeze');

if (freezeId) renderFrozen(freezeId);
else runLive();

async function renderFrozen(id) {
  document.body.classList.add('freeze');
  const stage = createStage(host);
  const [data, frozen] = await Promise.all([api('/api/live'), api(`/api/live/captures/${encodeURIComponent(id)}`)]);
  applyTheme(data.plan);
  const ctx = { plan: data.plan, state: { timers: {}, blackout: false }, now: Date.now(), result: frozen.result, names: !!frozen.names };
  stage.render(frozen.item, ctx);
  await stage.ready();
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
      applyTheme(p);
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
      if (!it.slide_file || preloaded.has(it.slide_file)) return;
      preloaded.add(it.slide_file);
      new Image().src = `/api/sessions/${encodeURIComponent(plan.session.id)}/slides/${it.slide_file}`;
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
