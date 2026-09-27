// /presenter — the cockpit on the second monitor. It follows the live state
// like the stage does and sends the same intents (keys, buttons). What it adds:
// the next items by title, notes, the item timer controls and the
// presenter-only clocks (session vs plan, section time left).

import { icon } from '/static/_vendored/icons/icons.js';
import { switchEl, setSwitch } from '/static/_vendored/switch/switch.js';
import { api, esc, toast, currentTheme, setTheme } from '/static/js/ui.js';
import { confirmDialog } from '/static/js/dialogs.js';
import { connectLive, bindKeys, remaining, hms, timing, driftText } from '/static/js/live.js';
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
    if (msg.type === 'chat') {
      addChat(msg.messages);
      if (live.state && live.state.reader) live.state.reader.last_read_ms = live.now();
    }
    if (msg.type === 'reader_beat' && live.state && live.state.reader) live.state.reader.last_read_ms = msg.last_read_ms;
  },
  onConnection(online) {
    if (online && plan && plan.active) loadChat();
    if (plan && plan.active) drawChips();
  },
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
      `<button type="button" class="p-icon-btn" data-keys title="Keys" aria-label="Keyboard shortcuts" aria-expanded="false">${icon('keyboard')}</button>` +
      `<button type="button" class="p-icon-btn" data-blackout title="Blackout (B)" aria-label="Blackout">${icon('eye-off')}</button>` +
      `<button type="button" class="p-icon-btn" data-theme title="Toggle theme" aria-label="Toggle theme">${icon(currentTheme() === 'dark' ? 'sun' : 'moon')}</button>` +
      `<button type="button" class="p-icon-btn" data-close title="Close the live session" aria-label="Close the live session">${icon('x')}</button>` +
    `</header>` +
    `<div class="p-grid">` +
      card('p-now', '<span class="live-dot"></span>On stage now', 'what Zoom sees', '<span class="p-flag" data-flag hidden></span>') +
      card('p-next', 'Next', '') +
      `<div class="p-side">` +
        card('p-item', 'Timer', '') +
        card('p-chat', 'Zoom chat', '', '<span class="p-age small muted" data-age></span>') +
      `</div>` +
      card('p-notes', 'Notes', '') +
      card('p-timing', 'Timing', '', '<span class="chip" data-drift hidden></span>') +
    `</div>` +
    `<footer class="card p-strip">` +
      `<button type="button" class="p-nav" data-prev aria-label="Previous (←)">${icon('chevron-left')}</button>` +
      `<div class="p-thumbs" data-thumbs></div>` +
      `<button type="button" class="p-nav" data-next aria-label="Next (→)">${icon('chevron-right')}</button>` +
    `</footer>` +
    `<div class="card p-keys" data-keypop hidden><p class="overline">Keys · stage or presenter window</p><dl class="p-keylist"></dl></div>`;

  const nowHost = document.createElement('div');
  nowHost.className = 'stage-host p-stage';
  root.querySelector('.p-now [data-body]').appendChild(nowHost);
  nowStage = createStage(nowHost, { guides: true, blackout: false });

  const nextBody = root.querySelector('.p-next [data-body]');
  nextBody.innerHTML = `<div class="stage-host p-stage p-stage-next"></div><p class="p-caption small muted" data-caption></p>` +
    `<p class="overline p-then-label">Then</p><ol class="p-then" data-then></ol>`;
  nextStage = createStage(nextBody.querySelector('.p-stage-next'), { guides: true, blackout: false });

  root.querySelector('.p-keylist').innerHTML =
    [['→ · PageDown', 'next'], ['← · PageUp', 'previous'], ['B', 'blackout'], ['T', 'timer start / pause'], ['M', 'timer +1 min'], ['Space', 'capture start / stop'], ['Home', 'first item']]
      .map(([k, v]) => `<div><dt><kbd>${k}</kbd></dt><dd>${v}</dd></div>`).join('');
  const keysBtn = root.querySelector('[data-keys]');
  keysBtn.addEventListener('click', () => {
    const pop = root.querySelector('[data-keypop]');
    pop.hidden = !pop.hidden;
    keysBtn.setAttribute('aria-expanded', String(!pop.hidden));
  });
  buildChat();

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
  markChat();
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
  const cap = !!(cur && cur.capture);
  card.querySelector('.p-label').textContent = cap ? 'Capture' : 'Timer';
  card.querySelector('.p-meta').textContent = cur ? cur.title : '';
  const key = `${cur ? cur.id : ''}|${t ? 1 : 0}|${cur && cur.kind === 'activity' ? 1 : 0}`;
  if (body.dataset.key !== key) {
    body.dataset.key = key;
    body.innerHTML = '';
    if (cap) {
      body.insertAdjacentHTML('beforeend',
        `<div class="p-cap-status"><span class="p-cap-dot" data-capdot></span><span data-capstate></span></div>` +
        `<p class="small muted p-cap-counts" data-capcounts></p>` +
        `<button type="button" class="button-primary p-cap-btn" data-captoggle></button>`);
      body.querySelector('[data-captoggle]').addEventListener('click', () => live.send('capture_toggle'));
    }
    if (!t && !cap) {
      body.innerHTML = `<p class="muted">No timer on this item. Timers are decided item by item in the Plan tab.</p>`;
    } else if (t) {
      const starts = { manual: 'you start it', on_enter: 'starts when the item opens', with_capture: 'starts with the capture' }[t.start];
      const shows = { stage: 'on the stage', presenter: 'here only', both: 'stage and here' }[t.show_on];
      body.insertAdjacentHTML('beforeend',
        `<div class="p-timer${cap ? ' compact' : ''}"><span class="p-timer-clock" data-tclock></span><span class="p-timer-state small muted" data-tstate></span></div>` +
        `<p class="small muted">${esc(Math.round(t.seconds / 60 * 10) / 10)} min timer · ${esc(starts)} · shows ${esc(shows)}</p>` +
        `<div class="p-timer-actions">` +
        `<button type="button" class="${cap ? 'button-surface' : 'button-primary'}" data-ttoggle></button>` +
        `<button type="button" class="button-surface" data-tadd title="M">+1 min</button>` +
        `<button type="button" class="button-surface" data-treset title="Reset">${icon('rotate-ccw')} Reset</button></div>`);
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
  if (cap) drawCapture(body, s.capture);
  if (t) tickItemTimer(cur, s);
}

function drawCapture(body, c) {
  const status = c ? c.status : 'idle';
  const btn = body.querySelector('[data-captoggle]');
  const label = { idle: `${icon('play')} Start capture`, live: `${icon('square')} Stop capture`, stopped: `${icon('play')} Reopen capture` }[status];
  if (btn.dataset.status !== status) {
    btn.dataset.status = status;
    btn.innerHTML = `${label}<kbd>Space</kbd>`;
    btn.classList.toggle('live', status === 'live');
  }
  body.querySelector('[data-capdot]').className = `p-cap-dot ${status}`;
  body.querySelector('[data-capstate]').textContent = { idle: 'Not started — answers are not counted yet', live: 'Capturing answers', stopped: 'Stopped — the result is frozen' }[status];
  drawUnplaced(body, c);
  body.querySelector('[data-capcounts]').textContent = c && (c.answers || c.hidden)
    ? `${c.answers} answer${c.answers === 1 ? '' : 's'} from ${c.people} ${c.people === 1 ? 'person' : 'people'}${c.hidden ? ` · ${c.hidden} hidden` : ''}`
    : status === 'idle' ? 'Press Space when the question is on stage.' : 'No answers yet.';
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

const hm = (min) => `${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, '0')}`;

function drawTiming(cur, s) {
  const card = root.querySelector('.p-timing');
  const body = card.querySelector('[data-body]');
  const drift = card.querySelector('[data-drift]');
  const secs = plan.run.sections;
  const now = live.now();
  const t = timing(plan, s, cur, now);
  const dur = s.clock.duration_minutes;
  if (!t) {
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
  const elapsedMin = t.elapsed / 60;
  body.querySelector('[data-elapsed]').textContent = hms(t.elapsed);
  const sec = t.section;
  if (sec) {
    const inSec = t.inSection;
    const left = sec.minutes - inSec;
    body.querySelector('[data-secname]').textContent = sec.name;
    body.querySelector('[data-secleft]').innerHTML = left >= 0
      ? `Planned ${sec.minutes} min · <b>${clock(left * 60)}</b> left`
      : `Planned ${sec.minutes} min · <b class="over">over by ${clock(-left * 60)}</b>`;
    body.querySelector('[data-bar]').style.width = `${Math.min(100, sec.minutes ? (inSec / sec.minutes) * 100 : 100)}%`;
    body.querySelector('[data-bar]').classList.toggle('over', left < 0);
    drift.hidden = false;
    drift.className = 'chip ' + (t.drift >= 1 ? 'warn' : 'ok');
    drift.textContent = driftText(t.drift);
    const brk = secs.find((x) => x.planned_start > sec.planned_start && x.has_break);
    body.querySelector('[data-break]').textContent = brk
      ? `${brk.name} at ${hm(brk.planned_start)}, in ${Math.max(0, Math.round(brk.planned_start - elapsedMin))} min`
      : `Planned end at ${hm(plan.run.planned_minutes)}`;
  }
}

const READER_CHIP = {
  reading: ['ok', 'Zoom chat · reading'],
  simulating: ['accent', 'Zoom chat · simulating'],
  starting: ['warn', 'Zoom chat · starting'],
  window_not_found: ['warn', 'Zoom chat · pop out the chat'],
  stale: ['bad', 'Zoom chat · no answer'],
  error: ['bad', 'Zoom chat · error'],
  off: ['', 'Zoom chat · off', 'start-reader'],
};

function drawChips() {
  const box = root.querySelector('[data-chips]');
  if (!box || !live.state) return;
  const s = live.state;
  const chips = [];
  if (!live.online) chips.push(['bad', 'Server · reconnecting']);
  const r = s.reader || { state: 'off' };
  const rc = READER_CHIP[r.state] || ['bad', `Zoom chat · ${r.state}`];
  chips.push([rc[0], rc[1], rc[2], r.detail]);
  const o = s.obs || { state: 'off' };
  const profileLabel = { camera_strip: 'Camera strip', camera_pip: 'Camera PiP', screen_only: 'Screen only' }[o.profile] || '';
  if (o.state === 'connected') {
    chips.push(o.warning ? ['warn', `OBS · ${profileLabel || 'check scenes'}`, null, o.warning] : ['ok', `OBS · ${profileLabel ? 'profile ' + profileLabel : 'connected'}`, null, o.detail]);
  } else if (o.state === 'off') {
    chips.push(['', 'OBS · off', null, o.detail]);
  } else {
    chips.push(['bad', o.state === 'connecting' ? 'OBS · connecting' : 'OBS · not reachable', 'obs-retry', o.detail]);
  }
  const st = s.stages || [];
  if (st.length) chips.push(['ok', `Stage · ${st.length > 1 ? st.length + ' windows' : `${st[0].w}×${st[0].h}`}`]);
  else chips.push(['warn', 'Stage · not open', 'open-stage']);
  const la = s.last_action;
  if (la) {
    const ago = Math.max(0, Math.round((live.now() - la.at) / 1000));
    const deck = la.source === 'streamdeck' || la.source === 'stream-deck';
    if (ago < 3600) chips.push(['ok', `${deck ? 'Stream Deck' : 'Buttons'} · ${la.action.replace(/_/g, ' ')}`, null, `Last press ${ago} s ago (${la.source})`]);
  }
  if (s.write_error) chips.push(['bad', s.write_error]);
  const html = chips.map(([k, t, act, title]) => act
    ? `<button type="button" class="chip ${k}" data-act="${act}" title="${esc(title || '')}"><span class="dot"></span>${esc(t)}</button>`
    : `<span class="chip ${k}" title="${esc(title || '')}"><span class="dot"></span>${esc(t)}</span>`).join('');
  if (box.dataset.html !== html) {
    box.dataset.html = html;
    box.innerHTML = html;
    const open = box.querySelector('[data-act="open-stage"]');
    if (open) open.addEventListener('click', () => window.open('/stage', 'fs-stage', 'popup,width=1280,height=720'));
    const start = box.querySelector('[data-act="start-reader"]');
    if (start) start.addEventListener('click', startReader);
    const retry = box.querySelector('[data-act="obs-retry"]');
    if (retry) retry.addEventListener('click', () => api('/api/settings/obs/test', { method: 'POST' }).catch((e) => toast(e.message, 'error')));
  }
  drawChatFoot();
}

// ------------------------------------------------------------------------ chat

let chat = [];

function buildChat() {
  const body = root.querySelector('.p-chat [data-body]');
  body.innerHTML = `<ol class="p-msgs" data-msgs></ol><div class="p-chat-foot" data-chatfoot></div>`;
  body.querySelector('[data-msgs]').addEventListener('click', (e) => {
    const li = e.target.closest('.p-msg');
    if (!li || li.classList.contains('own')) return;
    live.send(li.classList.contains('hidden-msg') ? 'unhide_message' : 'hide_message', li.dataset.id);
  });
  chat = [];
  loadChat();
}

async function loadChat() {
  try {
    const data = await api('/api/chat/messages?since=0');
    const list = root.querySelector('[data-msgs]');
    if (!list) return;
    chat = [];
    list.innerHTML = '';
    addChat(data.messages);
  } catch (e) { /* the WS reconnect retries */ }
}

// Map answers the gazetteer could not place: suggestions, one click to fix.
const suggestions = new Map();
function drawUnplaced(body, c) {
  const list = (c && c.result && c.result.unplaced) || [];
  let box = body.querySelector('[data-unplaced]');
  if (!list.length) { if (box) box.remove(); return; }
  if (!box) {
    box = document.createElement('div');
    box.className = 'p-unplaced';
    box.dataset.unplaced = '';
    body.querySelector('[data-captoggle]').after(box);
    box.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-place]');
      if (!b) return;
      try {
        await api('/api/live/place', { method: 'POST', body: { message_id: Number(b.dataset.msg), geonameid: b.dataset.place } });
      } catch (err) { toast(err.message, 'error'); }
    });
    box.addEventListener('keydown', (e) => {
      const input = e.target.closest('input[data-msg]');
      if (!input || e.key !== 'Enter') return;
      e.preventDefault();
      loadSuggestions(Number(input.dataset.msg), input.value, box, true);
    });
  }
  const sig = JSON.stringify(list.map((u) => u.id));
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = `<p class="overline">${icon('map-pin')} Not on the map · ${list.length}</p>` + list.map((u) =>
    `<div class="p-unplaced-row"><div><b>${esc(u.sender)}</b> <span class="muted">“${esc(u.text)}”</span></div>` +
    `<div class="row-actions" data-sugg="${u.id}"></div>` +
    `<input class="input" data-msg="${u.id}" placeholder="Type the place, Enter" aria-label="Place for ${esc(u.sender)}"></div>`).join('');
  list.forEach((u) => loadSuggestions(u.id, u.text, box, false));
}

async function loadSuggestions(id, text, box, fresh) {
  const key = `${id}|${text}`;
  if (!suggestions.has(key) || fresh) {
    try { suggestions.set(key, (await api(`/api/geo/suggest?q=${encodeURIComponent(text)}`)).places); } catch (e) { suggestions.set(key, []); }
  }
  const row = box.querySelector(`[data-sugg="${id}"]`);
  if (!row) return;
  const places = suggestions.get(key).slice(0, 3);
  row.innerHTML = places.length
    ? places.map((p) => `<button type="button" class="button-surface" data-msg="${id}" data-place="${esc(p.geonameid)}">${esc(p.name)}, ${esc(p.country)}</button>`).join('')
    : '<span class="small muted">No close match — type the place below.</span>';
}

/** Mark each chat row: counted in the current capture, or hidden. Click toggles hidden. */
function markChat() {
  const list = root.querySelector('[data-msgs]');
  const s = live.state;
  if (!list || !s) return;
  const hidden = new Set(s.hidden || []);
  const wins = (s.capture && s.capture.windows) || [];
  const inWin = (m) => wins.some(([a, b]) => m.received_at >= a && (b == null || m.received_at < b));
  const sig = JSON.stringify([s.hidden, wins, chat.length]);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  const byId = new Map(chat.map((m) => [m.id, m]));
  list.querySelectorAll('.p-msg').forEach((li) => {
    const m = byId.get(Number(li.dataset.id));
    if (!m) return;
    const isHidden = hidden.has(m.id);
    li.classList.toggle('hidden-msg', isHidden);
    li.classList.toggle('counted', !isHidden && !m.own && inWin(m));
    li.title = m.own ? 'Your own message — never counted' : isHidden ? 'Hidden — click to count it again' : 'Click to hide it from the activity';
  });
}

function addChat(messages) {
  const list = root.querySelector('[data-msgs]');
  if (!list || !messages || !messages.length) return;
  const known = chat.length ? chat[chat.length - 1].id : 0;
  const fresh = messages.filter((m) => m.id > known);
  if (!fresh.length) return;
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  chat.push(...fresh);
  list.insertAdjacentHTML('beforeend', fresh.map((m) =>
    `<li class="p-msg${m.own ? ' own' : ''}" data-id="${m.id}"><div class="p-msg-top"><b>${esc(m.sender)}</b>` +
    `${m.own ? '<span class="chip">you</span>' : ''}${m.source === 'simulator' ? '<span class="chip accent">sim</span>' : ''}` +
    `<span class="p-msg-time">${esc(m.time)}</span></div><div class="p-msg-text">${esc(m.text) || '<i class="muted">(emoji only)</i>'}</div></li>`).join(''));
  // Keep a long session light: the list holds the latest 400 rows.
  while (list.children.length > 400) list.removeChild(list.firstChild);
  if (atBottom || fresh.length === chat.length) list.scrollTop = list.scrollHeight;
  root.querySelector('.p-chat .p-meta').textContent = `${chat.length} message${chat.length === 1 ? '' : 's'}`;
  drawChatFoot();
  markChat();
}

function drawChatFoot() {
  const foot = root.querySelector('[data-chatfoot]');
  if (!foot || !live.state) return;
  const r = live.state.reader || { state: 'off' };
  const running = r.state !== 'off';
  let hint = '';
  if (r.state === 'window_not_found') hint = 'In Zoom: Chat → … → Pop out. The reader only sees the popped-out chat.';
  else if (r.state === 'stale' || r.state === 'error') hint = r.detail || '';
  else if (!chat.length && r.state === 'off') hint = 'Start the reader to see the Zoom chat here, or simulate answers to rehearse.';
  const key = `${r.state}|${hint}`;
  if (foot.dataset.key === key) return;
  foot.dataset.key = key;
  foot.innerHTML = (hint ? `<p class="small muted p-chat-hint">${esc(hint)}</p>` : '') +
    `<div class="row-actions">` +
    (running && r.state !== 'simulating'
      ? `<button type="button" class="button-surface" data-reader="stop">${icon('square')} Stop reader</button>`
      : `<button type="button" class="button-surface" data-reader="start">${icon('play')} Start reader</button>`) +
    `<button type="button" class="button-surface" data-sim>${icon('wand-sparkles')} Simulate answers</button></div>`;
  const rb = foot.querySelector('[data-reader]');
  rb.addEventListener('click', () => (rb.dataset.reader === 'stop' ? stopReader() : startReader()));
  foot.querySelector('[data-sim]').addEventListener('click', simulateAnswers);
}

async function startReader() {
  try { await api('/api/chat/reader/start', { method: 'POST' }); } catch (e) { toast(e.message, 'error'); }
}
async function stopReader() {
  try { await api('/api/chat/reader/stop', { method: 'POST' }); } catch (e) { toast(e.message, 'error'); }
}
let activityTypes = null;
async function simulateAnswers() {
  // On an activity, the simulator answers with that type's sample answers.
  const s = live.state;
  const cur = s && plan ? items()[s.index] : null;
  let answers = [];
  if (cur && cur.kind === 'activity') {
    try {
      activityTypes = activityTypes || (await api('/api/activities')).types;
      answers = (activityTypes.find((x) => x.type === cur.type) || {}).samples || [];
    } catch (e) { answers = []; }
  }
  try {
    await api('/api/chat/simulate', { method: 'POST', body: { kind: 'random', count: 30, every_ms: 600, answers } });
    toast('Simulating 30 answers through the chat pipeline');
  } catch (e) { toast(e.message, 'error'); }
}

function tickChatAge() {
  const el = root.querySelector('[data-age]');
  if (!el || !live.state || !live.state.reader) return;
  const last = live.state.reader.last_read_ms;
  el.textContent = last ? `updated ${Math.max(0, (live.now() - last) / 1000).toFixed(1)} s ago` : '';
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
  tickChatAge();
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

// Space means "capture" on the presenter: a button clicked with the mouse must
// not keep the focus (Space would click it again). Keyboard focus is kept.
root.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (b && e.detail > 0) b.blur();
});
