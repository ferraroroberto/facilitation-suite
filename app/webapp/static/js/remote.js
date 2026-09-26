// /remote — the phone in the facilitator's hand (epic §3.2): what is on stage,
// what comes next, the session clock against the plan, and the same intents as
// the keyboard — next/previous, capture, timer, blackout — as big buttons.
// Chat reads the live Zoom chat (tap a message to hide it); Groups shows the
// breakout rooms to read out. From another device it needs the pairing link
// made in Settings on the PC (app/webapp/auth.py).

import { icon } from '/static/_vendored/icons/icons.js';
import { initNavTabs } from '/static/_vendored/nav/nav-tabs.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, oneLine, toast, currentTheme, setTheme, APP } from '/static/js/ui.js';
import { connectLive, remaining, hms, timing, driftText } from '/static/js/live.js';
import { createStage, applyTheme, clock } from '/static/js/stage-render.js';

const livePane = document.getElementById('paneLive');
const chatPane = document.getElementById('paneChat');
const groupsPane = document.getElementById('paneGroups');
let live = null;
let plan = null;
let preview = null;
let shellFor = null;
let chat = [];
let groupsRound = 'pairs';
const READER_TEXT = {
  reading: 'reading', simulating: 'simulating', starting: 'starting', window_not_found: 'pop out the chat on the PC',
  stale: 'no answer from the reader', error: 'reader error', off: 'reader off',
};

// The pairing link carries ?token=: the server has set its cookie by now, so
// the token leaves the address bar (and any screenshot or shared URL).
if (new URLSearchParams(location.search).has('token')) history.replaceState(null, '', '/remote');

initNavTabs({ storageKey: APP + '.remote.tab', onChange: (tab) => { if (tab === 'groups') loadGroups(); } });

async function start() {
  try {
    await api('/api/live');
  } catch (e) {
    if (e.status === 401) { unpaired(e.code); return; }
  }
  live = connectLive('remote', {
    onPlan(p) {
      plan = p;
      applyTheme(p);
      shellFor = null;
      drawLive();
      loadChat();
      loadGroups();
    },
    onState() { drawLive(); drawChat(); },
    onMessage(msg) {
      if (msg.type === 'error') toast(msg.message, 'error');
      if (msg.type === 'chat') { chat = chat.concat(msg.messages); drawChat(); }
    },
    onConnection(online) {
      document.body.classList.toggle('offline', !online);
      if (online) loadChat();
    },
  });
  setInterval(tick, 500);
}

function unpaired(code) {
  document.querySelector('.tabs').hidden = true;
  livePane.innerHTML = headHtml();
  livePane.querySelector('[data-r-sub]').textContent = 'Not paired';
  livePane.querySelector('[data-theme]').remove();
  const card = document.createElement('div');
  card.className = 'card';
  card.appendChild(emptyStateEl('smartphone', code === 'remote_off'
    ? 'The phone remote is off. On the PC: Settings → Phone remote → Make the phone link, then open it on this phone.'
    : 'This phone is not paired yet. On the PC: Settings → Phone remote → Copy the link, then open it on this phone.'));
  livePane.appendChild(card);
}

// ---- Live ------------------------------------------------------------------------

const items = () => (plan && plan.run ? plan.run.items : []);

function headHtml() {
  return `<div class="card home-head remote-head"><span class="home-title">${icon('presentation')}` +
    '<span class="remote-titles"><span class="home-title-text">Live</span><span class="remote-sub" data-r-sub></span></span></span>' +
    `<button type="button" class="home-toggle" data-theme aria-label="Toggle theme" title="Toggle theme">${icon(currentTheme() === 'dark' ? 'sun' : 'moon')}</button></div>`;
}

function buildShell() {
  const key = plan && plan.active ? `${plan.session.id}:${plan.rev}` : 'idle';
  if (shellFor === key) return;
  shellFor = key;
  preview = null;
  livePane.innerHTML = headHtml();
  livePane.querySelector('[data-theme]').addEventListener('click', (e) => {
    setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    e.currentTarget.innerHTML = icon(currentTheme() === 'dark' ? 'sun' : 'moon');
  });
  if (!plan || !plan.active) {
    const card = document.createElement('div');
    card.className = 'card';
    card.appendChild(emptyStateEl('presentation', 'No session is live. Start it from the presenter on the PC.'));
    livePane.appendChild(card);
    return;
  }
  livePane.insertAdjacentHTML('beforeend',
    '<div class="card r-now"><div class="stage-host r-stage"></div>' +
      '<div class="r-caption"><b data-r-title></b><span class="r-count" data-r-count></span><span class="r-timer" data-r-timer></span></div></div>' +
    '<div class="card r-next"><div class="grow"><div class="r-label" data-r-nextlabel></div><div class="r-next-title" data-r-next></div></div>' +
      '<div class="r-clock"><b data-r-elapsed></b><span data-r-drift></span></div></div>' +
    `<button type="button" class="button-primary r-capture" data-r-capture hidden></button>` +
    '<div class="r-nav">' +
      `<button type="button" class="button-tint" data-r-prev>${icon('chevron-left')} Previous</button>` +
      `<button type="button" class="button-tint" data-r-nextbtn>Next ${icon('chevron-right')}</button></div>` +
    '<div class="r-more">' +
      `<button type="button" class="button-surface" data-r-ttoggle hidden></button>` +
      `<button type="button" class="button-surface" data-r-tplus hidden>${icon('plus')} 1 min</button>` +
      `<button type="button" class="button-surface" data-r-blackout>${icon('eye-off')} Blackout</button></div>` +
    '<p class="small muted r-foot">The presenter on the PC does the same; this is for when you stand up or walk away from the keyboard.</p>');
  preview = createStage(livePane.querySelector('.r-stage'), { guides: false, blackout: true });
  const on = (sel, action) => livePane.querySelector(sel).addEventListener('click', () => live.send(action));
  on('[data-r-capture]', 'capture_toggle');
  on('[data-r-prev]', 'prev');
  on('[data-r-nextbtn]', 'next');
  on('[data-r-ttoggle]', 'timer_toggle');
  on('[data-r-tplus]', 'timer_add_minute');
  on('[data-r-blackout]', 'blackout');
}

function drawLive() {
  buildShell();
  const s = live && live.state;
  const sub = livePane.querySelector('[data-r-sub]');
  if (!plan || !plan.active || !s || s.plan_rev !== plan.rev) {
    if (sub) sub.textContent = plan && plan.active ? 'Connecting…' : 'Not live';
    return;
  }
  const cur = items()[s.index] || null;
  const nxt = items()[s.index + 1] || null;
  const cap = s.capture && cur && s.capture.item_id === cur.id ? s.capture : null;
  preview.render(cur, { plan, state: s, now: live.now() });
  const where = cur ? `${cur.kind === 'slide' ? 'Slide' : 'Item'} ${s.index + 1} of ${s.count}` : '';
  sub.textContent = [where, cap && cap.status === 'live' ? 'capturing' : '', s.blackout ? 'blackout' : ''].filter(Boolean).join(' · ');
  livePane.querySelector('[data-r-title]').textContent = cur ? oneLine(cur.title) : '';
  livePane.querySelector('[data-r-count]').textContent = cap ? `· ${cap.answers} answer${cap.answers === 1 ? '' : 's'}` : '';
  livePane.querySelector('[data-r-nextlabel]').textContent = nxt ? `Next · ${s.index + 2}` : 'Next';
  livePane.querySelector('[data-r-next]').textContent = nxt ? oneLine(nxt.title) : 'End of the session';

  const capBtn = livePane.querySelector('[data-r-capture]');
  capBtn.hidden = !(cur && cur.capture);
  if (cur && cur.capture) {
    const status = cap ? cap.status : 'idle';
    const [glyph, label] = status === 'live' ? ['square', 'Stop capture'] : status === 'stopped' ? ['refresh-cw', 'Reopen capture'] : ['play', 'Start capture'];
    const html = `${icon(glyph)} ${label}`;
    if (capBtn.dataset.html !== html) { capBtn.dataset.html = html; capBtn.innerHTML = html; }
    capBtn.classList.toggle('live', status === 'live');
  }
  livePane.querySelector('[data-r-prev]').disabled = s.index <= 0;
  livePane.querySelector('[data-r-nextbtn]').disabled = s.index >= s.count - 1;
  const hasTimer = !!(cur && cur.timer);
  livePane.querySelector('[data-r-ttoggle]').hidden = !hasTimer;
  livePane.querySelector('[data-r-tplus]').hidden = !hasTimer;
  livePane.querySelector('[data-r-blackout]').classList.toggle('on', !!s.blackout);
  tick();
}

/** The parts that move every half second: item timer, session clock, drift. */
function tick() {
  const s = live && live.state;
  if (!preview || !s || !plan || !plan.active || s.plan_rev !== plan.rev) return;
  const cur = items()[s.index] || null;
  const now = live.now();
  const tEl = livePane.querySelector('[data-r-timer]');
  const ts = cur && cur.timer ? s.timers[cur.id] : null;
  if (cur && cur.timer) {
    const running = ts && ts.running_since != null;
    tEl.textContent = clock(ts ? remaining(ts, now) : cur.timer.seconds);
    tEl.classList.toggle('running', !!running);
    tEl.classList.toggle('done', !!(ts && ts.done));
    const btn = livePane.querySelector('[data-r-ttoggle]');
    const html = running ? `${icon('pause')} Pause` : `${icon('play')} ${ts && !ts.done ? 'Resume' : 'Timer'}`;
    if (btn.dataset.html !== html) { btn.dataset.html = html; btn.innerHTML = html; }
  } else {
    tEl.textContent = '';
  }
  const t = timing(plan, s, cur, now);
  livePane.querySelector('[data-r-elapsed]').textContent = t ? hms(t.elapsed) : '--:--';
  const drift = livePane.querySelector('[data-r-drift]');
  drift.textContent = t ? driftText(t.drift) : 'clock not started';
  drift.className = t && t.drift >= 1 ? 'over' : '';
}

// ---- Chat ------------------------------------------------------------------------

async function loadChat() {
  try {
    const r = await api('/api/chat/messages');
    chat = r.messages;
  } catch (e) { chat = []; }
  drawChat();
}

function drawChat() {
  const hidden = new Set((live && live.state && live.state.hidden) || []);
  const reader = live && live.state && live.state.reader;
  chatPane.innerHTML = `<div class="card home-head"><span class="home-title">${icon('message-square')}<span class="home-title-text">Zoom chat</span></span>` +
    `<span class="status">${esc(reader ? READER_TEXT[reader.state] || reader.state : '')}</span></div>`;
  const shown = chat.filter((m) => !m.own).slice(-80).reverse();
  const card = document.createElement('div');
  card.className = 'card list-card';
  if (!shown.length) {
    card.appendChild(emptyStateEl('message-square', 'No messages yet.'));
  } else {
    card.innerHTML = '<p class="small muted r-chat-hint">Tap a message to hide it from the activity; tap again to count it back.</p>' +
      shown.map((m) => `<button type="button" class="r-msg${hidden.has(m.id) ? ' hidden-msg' : ''}" data-id="${m.id}">` +
        `<span class="r-msg-head"><b>${esc(m.sender)}</b><span class="muted small">${esc(m.time)}</span></span>` +
        `<span class="r-msg-text">${esc(m.text)}</span></button>`).join('');
    card.addEventListener('click', (e) => {
      const b = e.target.closest('[data-id]');
      if (!b || !live) return;
      live.send(b.classList.contains('hidden-msg') ? 'unhide_message' : 'hide_message', b.dataset.id);
    });
  }
  chatPane.appendChild(card);
}

// ---- Groups ----------------------------------------------------------------------

async function loadGroups() {
  groupsPane.innerHTML = `<div class="card home-head"><span class="home-title">${icon('shuffle')}<span class="home-title-text">Breakout rooms</span></span></div>`;
  const card = document.createElement('div');
  card.className = 'card';
  groupsPane.appendChild(card);
  if (!plan || !plan.active) { card.appendChild(emptyStateEl('shuffle', 'Rooms show here while a session is live.')); return; }
  let data;
  try {
    data = await api(`/api/sessions/${plan.session.id}/groups`);
  } catch (e) { card.appendChild(emptyStateEl('triangle-alert', e.message)); return; }
  if (!data.rounds) { card.appendChild(emptyStateEl('shuffle', 'Not shuffled yet — shuffle in the Groups tab on the PC.')); return; }
  const tabs = document.createElement('div');
  tabs.className = 'range-tabs';
  tabs.innerHTML = ['pairs', 'g4a', 'g4b'].map((k) =>
    `<button type="button" class="range-tab${k === groupsRound ? ' active' : ''}" data-round="${k}">${esc(data.labels[k])}</button>`).join('');
  tabs.addEventListener('click', (e) => {
    const b = e.target.closest('[data-round]');
    if (b) { groupsRound = b.dataset.round; loadGroups(); }
  });
  card.appendChild(tabs);
  card.insertAdjacentHTML('beforeend', `<ol class="r-rooms">${data.rounds[groupsRound].map((g, i) =>
    `<li><b>Room ${i + 1}</b><span>${g.map(esc).join(', ')}</span></li>`).join('')}</ol>`);
}

start();
