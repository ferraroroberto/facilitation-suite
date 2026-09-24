// /presenter — the cockpit on the second monitor. It follows the live state
// like the stage does and sends the same intents (keys, buttons). What it adds:
// the next items by title, notes, the item timer controls and the
// presenter-only clocks (session vs plan, section time left).

import { icon } from '/static/_vendored/icons/icons.js';
import { switchEl, setSwitch } from '/static/_vendored/switch/switch.js';
import { api, esc, toast, currentTheme, setTheme } from '/static/js/ui.js';
import { confirmDialog } from '/static/js/dialogs.js';
import { connectLive, bindKeys, remaining } from '/static/js/live.js';
import { createStage, applyTheme, clock } from '/static/js/stage-render.js';

const root = document.getElementById('presenter');
const KIND_LABEL = { slide: 'slide', break: 'break' };
let plan = null;
let nowStage = null;
let nextStage = null;
let shellFor = null; // the session id the shell was built for
let lastIndex = -1;

const live = connectLive('presenter', {
  onPlan(p) {
    plan = p;
    applyTheme(p);
    if (!p.active) { renderPicker(); return; }
    buildShell();
    draw();
  },
  onState() { if (plan && plan.active) draw(); },
  onMessage(msg) {
    if (msg.type === 'error') toast(msg.message, 'error');
    if (msg.type === 'chime') chime();
  },
  onConnection() { if (plan && plan.active) drawChips(); },
});

// ------------------------------------------------------------------ activation

async function activateFromUrl() {
  const sid = new URLSearchParams(location.search).get('session');
  if (!sid) return;
  try {
    await api('/api/live/activate', { method: 'POST', body: { session: sid } });
  } catch (e) {
    toast(e.message, 'error');
  }
  history.replaceState(null, '', '/presenter');
}
activateFromUrl();

async function renderPicker() {
  shellFor = null;
  root.className = 'presenter picker';
  root.innerHTML = `<div class="card p-picker"><div class="card-head"><h1 class="card-title">${icon('presentation')} Go live</h1></div>` +
    `<p class="muted">Pick the session to present. The stage window follows it.</p><div class="list" data-list><p class="muted small">Loading…</p></div>` +
    `<p class="small"><a href="/">Back to the app</a></p></div>`;
  try {
    const data = await api('/api/sessions');
    const list = root.querySelector('[data-list]');
    if (!list) return;
    list.innerHTML = data.sessions.length ? '' : '<p class="muted">No sessions yet — create one in the app.</p>';
    data.sessions.forEach((s) => {
      const row = document.createElement('div');
      row.className = 'list-row';
      row.innerHTML = `<div class="grow"><div class="row-title">${esc(s.title || s.name)}</div><div class="row-meta">${esc(s.workshop || '')}</div></div>`;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'button-tint';
      b.innerHTML = `${icon('play')} Go live`;
      b.addEventListener('click', () => api('/api/live/activate', { method: 'POST', body: { session: s.id } }).catch((e) => toast(e.message, 'error')));
      row.appendChild(b);
      list.appendChild(row);
    });
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ----------------------------------------------------------------------- shell

function card(cls, label, meta, extra = '') {
  return `<section class="card p-card ${cls}"><div class="p-head"><span class="p-label">${label}</span>` +
    `<span class="p-meta">${meta}</span>${extra}</div><div class="p-body" data-body></div></section>`;
}

function buildShell() {
  if (shellFor === plan.session.id + ':' + plan.rev && root.querySelector('.p-grid')) return;
  shellFor = plan.session.id + ':' + plan.rev;
  lastIndex = -1;
  root.className = 'presenter';
  root.innerHTML =
    `<header class="p-top">` +
      `<img class="p-brand" src="/static/icons/icon-192.png" alt="">` +
      `<div class="p-titles"><div class="p-title">${esc(plan.session.title)}</div><div class="p-sub" data-sub></div></div>` +
      `<div class="p-chips" data-chips></div>` +
      `<button type="button" class="p-icon-btn" data-blackout title="Blackout (B)" aria-label="Blackout">${icon('eye-off')}</button>` +
      `<button type="button" class="p-icon-btn" data-theme title="Toggle theme" aria-label="Toggle theme">${icon(currentTheme() === 'dark' ? 'sun' : 'moon')}</button>` +
      `<button type="button" class="p-icon-btn" data-close title="Close the live session" aria-label="Close the live session">${icon('x')}</button>` +
    `</header>` +
    `<div class="p-grid">` +
      card('p-now', '<span class="live-dot"></span>On stage now', 'what Zoom sees', '<span class="p-flag" data-flag hidden></span>') +
      card('p-next', 'Next', '') +
      `<div class="p-side">` +
        card('p-item', 'Timer', '') +
        card('p-keys', 'Keys', 'stage or presenter window') +
      `</div>` +
      card('p-notes', 'Notes', '') +
      card('p-timing', 'Timing', '', '<span class="chip" data-drift hidden></span>') +
    `</div>` +
    `<footer class="card p-strip">` +
      `<button type="button" class="p-nav" data-prev aria-label="Previous (←)">${icon('chevron-left')}</button>` +
      `<div class="p-thumbs" data-thumbs></div>` +
      `<button type="button" class="p-nav" data-next aria-label="Next (→)">${icon('chevron-right')}</button>` +
    `</footer>`;

  const nowHost = document.createElement('div');
  nowHost.className = 'stage-host p-stage';
  root.querySelector('.p-now [data-body]').appendChild(nowHost);
  nowStage = createStage(nowHost, { guides: true, blackout: false });

  const nextBody = root.querySelector('.p-next [data-body]');
  nextBody.innerHTML = `<div class="stage-host p-stage p-stage-next"></div><p class="p-caption small muted" data-caption></p>` +
    `<p class="overline p-then-label">Then</p><ol class="p-then" data-then></ol>`;
  nextStage = createStage(nextBody.querySelector('.p-stage-next'), { guides: true, blackout: false });

  root.querySelector('.p-keys [data-body]').innerHTML =
    `<dl class="p-keylist">` +
    [['→ · PageDown', 'next'], ['← · PageUp', 'previous'], ['B', 'blackout'], ['T', 'timer start / pause'], ['M', 'timer +1 min'], ['Space', 'capture start / stop']]
      .map(([k, v]) => `<div><dt><kbd>${k}</kbd></dt><dd>${v}</dd></div>`).join('') + `</dl>`;

  root.querySelector('[data-prev]').addEventListener('click', () => live.send('prev'));
  root.querySelector('[data-next]').addEventListener('click', () => live.send('next'));
  root.querySelector('[data-blackout]').addEventListener('click', () => live.send('blackout'));
  root.querySelector('[data-theme]').addEventListener('click', (e) => {
    setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    e.currentTarget.innerHTML = icon(currentTheme() === 'dark' ? 'sun' : 'moon');
  });
  root.querySelector('[data-close]').addEventListener('click', async () => {
    const ok = await confirmDialog({ title: 'Close the live session?', message: 'The stage goes blank. Position, clocks and timers are kept in the session folder and come back when you go live again.', actionLabel: 'Close' });
    if (ok) api('/api/live/deactivate', { method: 'POST' }).catch((e) => toast(e.message, 'error'));
  });
  buildThumbs();
}

function buildThumbs() {
  const box = root.querySelector('[data-thumbs]');
  box.innerHTML = plan.run.items.map((it, i) => {
    const inner = it.slide_file
      ? `<img alt="" loading="lazy" src="/api/sessions/${encodeURIComponent(plan.session.id)}/slides/${esc(it.slide_file)}">`
      : `<span class="p-thumb-text">${it.kind === 'break' ? icon('coffee') : ''}${esc(it.title)}</span>`;
    return `<button type="button" class="p-thumb" data-goto="${i + 1}" title="${esc(it.title)}"><span class="p-thumb-img">${inner}</span><span class="p-thumb-n">${i + 1}</span></button>`;
  }).join('');
  box.addEventListener('click', (e) => {
    const b = e.target.closest('[data-goto]');
    if (b) live.send('goto', b.dataset.goto);
  });
}

// ------------------------------------------------------------------------ draw

const items = () => plan.run.items;

function kindLabel(it) {
  if (it.kind === 'activity') return (it.type_label || 'activity').toLowerCase();
  if (it.kind === 'break' && it.timer) return `break · ${Math.round(it.timer.seconds / 60)} min`;
  return KIND_LABEL[it.kind] || it.kind;
}

function draw() {
  const s = live.state;
  // A snapshot from before the latest plan (e.g. right after going live) is stale.
  if (!s || !plan || !plan.active || s.plan_rev !== plan.rev) return;
  const cur = items()[s.index] || null;
  const nxt = items()[s.index + 1] || null;
  const ctx = { plan, state: s, now: live.now() };
  nowStage.render(cur, ctx);
  nextStage.render(nxt, Object.assign({}, ctx, { state: Object.assign({}, s, { blackout: false }) }));

  root.querySelector('[data-sub]').textContent = cur
    ? `Presenter · ${cur.kind === 'slide' ? 'Slide' : 'Item'} ${s.index + 1} of ${s.count} · ${cur.title}` : 'Presenter';
  const flag = root.querySelector('[data-flag]');
  flag.hidden = !s.blackout;
  flag.textContent = s.blackout ? 'Blackout — the stage is black' : '';
  root.querySelector('[data-blackout]').classList.toggle('on', !!s.blackout);

  if (s.index !== lastIndex) {
    lastIndex = s.index;
    drawNext(s, nxt);
    drawNotes(cur);
    root.querySelectorAll('.p-thumb').forEach((b, i) => b.classList.toggle('current', i === s.index));
    // Scroll the strip itself (scrollIntoView would also scroll the page on a narrow screen).
    const strip = root.querySelector('[data-thumbs]');
    const curThumb = root.querySelector('.p-thumb.current');
    if (curThumb) strip.scrollTo({ left: curThumb.offsetLeft - strip.offsetLeft - (strip.clientWidth - curThumb.offsetWidth) / 2, behavior: 'smooth' });
  }
  root.querySelector('[data-prev]').disabled = s.index <= 0;
  root.querySelector('[data-next]').disabled = s.index >= s.count - 1;
  drawItemCard(cur, s);
  drawTiming(cur, s);
  drawChips();
}

function drawNext(s, nxt) {
  const card = root.querySelector('.p-next');
  card.querySelector('.p-meta').textContent = nxt ? `${s.index + 2} · ${nxt.title}` : 'end of the session';
  card.querySelector('[data-caption]').textContent = nxt
    ? (nxt.kind === 'activity' ? `${nxt.type_label || 'Activity'}${nxt.capture ? ' · starts when you press Space' : ''}` : kindLabel(nxt))
    : '';
  const then = items().slice(s.index + 2, s.index + 5);
  card.querySelector('[data-then]').innerHTML = then.map((it) =>
    `<li><span class="p-then-n">${it.index + 1}</span><span class="p-then-t">${esc(it.title)}</span><span class="p-then-k">${esc(kindLabel(it))}</span></li>`).join('') ||
    '<li class="muted small">Nothing after this.</li>';
}

function drawNotes(cur) {
  const card = root.querySelector('.p-notes');
  const body = card.querySelector('[data-body]');
  if (!cur) { body.innerHTML = ''; return; }
  card.querySelector('.p-meta').textContent = cur.kind === 'slide' ? 'from your PowerPoint' : cur.kind === 'activity' ? 'this activity' : '';
  let html = '';
  if (cur.kind === 'slide') {
    html = cur.notes ? `<div class="p-notes-text">${esc(cur.notes)}</div>` : '<p class="muted">No speaker notes on this slide.</p>';
  } else if (cur.kind === 'activity') {
    html = cur.question ? `<div class="p-notes-text">${esc(cur.question)}</div>` : '';
  } else {
    html = `<div class="p-notes-text">${esc(cur.title)}</div>`;
  }
  if (cur.kind === 'activity' && cur.chat_prompt) {
    html += `<div class="p-prompt"><span class="muted">Prompt to paste in chat:</span> <span class="p-prompt-text">“${esc(cur.chat_prompt)}”</span>` +
      `<button type="button" class="button-surface" data-copy>${icon('copy')} Copy prompt</button></div>`;
  }
  body.innerHTML = html;
  const copy = body.querySelector('[data-copy]');
  if (copy) copy.addEventListener('click', () => copyText(cur.chat_prompt));
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Prompt copied — paste it in the Zoom chat');
  } catch (e) {
    toast('Could not copy — select the text instead', 'error');
  }
}

function drawItemCard(cur, s) {
  const card = root.querySelector('.p-item');
  const body = card.querySelector('[data-body]');
  const t = cur && cur.timer;
  card.querySelector('.p-meta').textContent = cur ? cur.title : '';
  const key = `${cur ? cur.id : ''}|${t ? 1 : 0}|${cur && cur.kind === 'activity' ? 1 : 0}`;
  if (body.dataset.key !== key) {
    body.dataset.key = key;
    if (!t) {
      body.innerHTML = `<p class="muted">No timer on this item. Timers are decided item by item in the Plan tab.</p>`;
    } else {
      const starts = { manual: 'you start it', on_enter: 'starts when the item opens', with_capture: 'starts with the capture' }[t.start];
      const shows = { stage: 'on the stage', presenter: 'here only', both: 'stage and here' }[t.show_on];
      body.innerHTML =
        `<div class="p-timer"><span class="p-timer-clock" data-tclock></span><span class="p-timer-state small muted" data-tstate></span></div>` +
        `<p class="small muted">${esc(Math.round(t.seconds / 60 * 10) / 10)} min · ${esc(starts)} · shows ${esc(shows)}</p>` +
        `<div class="p-timer-actions">` +
        `<button type="button" class="button-primary" data-ttoggle></button>` +
        `<button type="button" class="button-surface" data-tadd title="M">+1 min</button>` +
        `<button type="button" class="button-surface" data-treset title="Reset">${icon('rotate-ccw')} Reset</button></div>`;
      body.querySelector('[data-ttoggle]').addEventListener('click', () => live.send('timer_toggle'));
      body.querySelector('[data-tadd]').addEventListener('click', () => live.send('timer_add_minute'));
      body.querySelector('[data-treset]').addEventListener('click', () => live.send('timer_reset'));
    }
    if (cur && cur.kind === 'activity') {
      const line = document.createElement('div');
      line.className = 'switch-line p-names';
      const sw = switchEl(!!s.names, { label: 'Show names on stage', onToggle: () => live.send('names_toggle') });
      sw.dataset.names = '';
      line.append(sw, Object.assign(document.createElement('span'), { textContent: 'Show names on stage' }));
      body.appendChild(line);
    }
  }
  const sw = body.querySelector('[data-names]');
  if (sw) setSwitch(sw, !!s.names);
  if (t) tickItemTimer(cur, s);
}

function tickItemTimer(cur, s) {
  const card = root.querySelector('.p-item');
  const tc = card.querySelector('[data-tclock]');
  if (!tc) return;
  const ts = s.timers[cur.id];
  const left = ts ? remaining(ts, live.now()) : cur.timer.seconds;
  tc.textContent = clock(left);
  tc.classList.toggle('done', !!(ts && ts.done));
  const running = ts && ts.running_since != null;
  card.querySelector('[data-tstate]').textContent = !ts ? 'not started' : ts.done ? 'time is up' : running ? 'running' : 'paused';
  const btn = card.querySelector('[data-ttoggle]');
  const label = running ? `${icon('pause')} Pause` : ts && !ts.done ? `${icon('play')} Resume` : `${icon('play')} Start timer`;
  if (btn.dataset.label !== label) { btn.dataset.label = label; btn.innerHTML = `${label}<kbd>T</kbd>`; }
}

function hms(sec) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${String(m).padStart(2, '0')}:${ss}`;
}
const hm = (min) => `${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, '0')}`;

function drawTiming(cur, s) {
  const card = root.querySelector('.p-timing');
  const body = card.querySelector('[data-body]');
  const drift = card.querySelector('[data-drift]');
  const secs = plan.run.sections;
  const sec = cur ? secs.find((x) => x.id === cur.section_id) : null;
  const started = s.clock.started_at;
  const dur = s.clock.duration_minutes;
  if (!started) {
    drift.hidden = true;
    if (body.dataset.mode !== 'idle') {
      body.dataset.mode = 'idle';
      body.innerHTML = `<div class="p-clock"><span class="p-clock-big">00:00</span><span class="p-clock-of">of ${hms(dur * 60)}</span></div>` +
        `<p class="small muted">The session clock starts when you say so; the section clock follows it.</p>` +
        `<button type="button" class="button-primary p-clock-start" data-cstart>${icon('clock')} Start the session clock</button>`;
      body.querySelector('[data-cstart]').addEventListener('click', () => live.send('clock_start'));
    }
    return;
  }
  if (body.dataset.mode !== 'run') {
    body.dataset.mode = 'run';
    body.innerHTML = `<div class="p-clock"><span class="p-clock-big" data-elapsed></span><span class="p-clock-of">of ${hms(dur * 60)}</span></div>` +
      `<div class="p-sec-name" data-secname></div><div class="p-sec-left small" data-secleft></div>` +
      `<div class="p-bar"><span data-bar></span></div><div class="p-break small muted" data-break></div>` +
      `<button type="button" class="button-ghost p-clock-reset" data-creset>${icon('rotate-ccw')} Reset clock</button>`;
    body.querySelector('[data-creset]').addEventListener('click', async () => {
      const ok = await confirmDialog({ title: 'Reset the session clock?', message: 'The session and section clocks go back to zero.', actionLabel: 'Reset' });
      if (ok) live.send('clock_reset');
    });
  }
  const now = live.now();
  const elapsedMin = (now - started) / 60000;
  body.querySelector('[data-elapsed]').textContent = hms((now - started) / 1000);
  if (sec) {
    const entered = s.section_entered[sec.id] || now;
    const inSec = (now - entered) / 60000;
    const left = sec.minutes - inSec;
    body.querySelector('[data-secname]').textContent = sec.name;
    body.querySelector('[data-secleft]').innerHTML = left >= 0
      ? `Planned ${sec.minutes} min · <b>${clock(left * 60)}</b> left`
      : `Planned ${sec.minutes} min · <b class="over">over by ${clock(-left * 60)}</b>`;
    body.querySelector('[data-bar]').style.width = `${Math.min(100, sec.minutes ? (inSec / sec.minutes) * 100 : 100)}%`;
    body.querySelector('[data-bar]').classList.toggle('over', left < 0);
    const d = (entered - started) / 60000 - sec.planned_start + Math.max(0, inSec - sec.minutes);
    drift.hidden = false;
    const r = Math.round(d);
    drift.className = 'chip ' + (r >= 1 ? 'warn' : r <= -1 ? 'ok' : 'ok');
    drift.textContent = r >= 1 ? `+${r} min` : r <= -1 ? `−${-r} min` : 'on time';
    const brk = secs.find((x) => x.planned_start > sec.planned_start && x.has_break);
    body.querySelector('[data-break]').textContent = brk
      ? `${brk.name} at ${hm(brk.planned_start)}, in ${Math.max(0, Math.round(brk.planned_start - elapsedMin))} min`
      : `Planned end at ${hm(plan.run.planned_minutes)}`;
  }
}

function drawChips() {
  const box = root.querySelector('[data-chips]');
  if (!box || !live.state) return;
  const s = live.state;
  const chips = [];
  if (!live.online) chips.push(['bad', 'Server · reconnecting']);
  const st = s.stages || [];
  if (st.length) chips.push(['ok', `Stage · ${st.length > 1 ? st.length + ' windows' : `${st[0].w}×${st[0].h}`}`]);
  else chips.push(['warn', 'Stage · not open', 'open-stage']);
  if (s.write_error) chips.push(['bad', s.write_error]);
  const html = chips.map(([k, t, act]) => act
    ? `<button type="button" class="chip ${k}" data-act="${act}"><span class="dot"></span>${esc(t)}</button>`
    : `<span class="chip ${k}"><span class="dot"></span>${esc(t)}</span>`).join('');
  if (box.dataset.html !== html) {
    box.dataset.html = html;
    box.innerHTML = html;
    const open = box.querySelector('[data-act="open-stage"]');
    if (open) open.addEventListener('click', () => window.open('/stage', 'fs-stage', 'popup,width=1280,height=720'));
  }
}

// Clocks tick locally between snapshots.
setInterval(() => {
  const s = live.state;
  if (!s || !plan || !plan.active || !nowStage || s.plan_rev !== plan.rev) return;
  const cur = items()[s.index];
  const ctx = { plan, state: s, now: live.now() };
  nowStage.update(ctx);
  if (cur && cur.timer) tickItemTimer(cur, s);
  drawTiming(cur, s);
}, 250);

// ----------------------------------------------------------------------- chime

let audio = null;
function chime() {
  try {
    audio = audio || new AudioContext();
    [0, 0.18].forEach((delay, i) => {
      const o = audio.createOscillator();
      const g = audio.createGain();
      o.frequency.value = i ? 1320 : 880;
      g.gain.setValueAtTime(0.0001, audio.currentTime + delay);
      g.gain.exponentialRampToValueAtTime(0.3, audio.currentTime + delay + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + delay + 0.6);
      o.connect(g).connect(audio.destination);
      o.start(audio.currentTime + delay);
      o.stop(audio.currentTime + delay + 0.65);
    });
  } catch (e) { /* no audio device */ }
}

bindKeys(live, { Home: () => live.send('goto', '1') });
