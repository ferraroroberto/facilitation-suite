// /presenter — the cockpit on the second monitor. It follows the live state
// like the stage does and sends the same intents (keys, buttons). What it adds:
// the next items by title, notes, the item timer controls and the
// presenter-only clocks (session vs plan, section time left).

import { icon } from '/static/_vendored/icons/icons.js';
import { setSwitch } from '/static/_vendored/switch/switch.js';
import { api, esc, oneLine, toast, currentTheme, toggleTheme, followAppearance, fmtMinutes, switchRow } from '/static/js/ui.js';
import { confirmDialog } from '/static/js/dialogs.js';
import { connectLive, bindKeys, clickToAdvance, neighbour, remaining, hms, timing, driftText } from '/static/js/live.js';
import { createStage, applyTheme, clock } from '/static/js/stage-render.js';
import { initUsageLog } from '/static/_vendored/usage-log/usage-log.js';
import { isQuiz, buildQuiz, drawQuiz, tickQuiz } from '/static/js/presenter-quiz.js';

// The cockpit is one view; the stage and the phone remote never load the usage log.
initUsageLog().trackView('live');

const root = document.getElementById('presenter');
const KIND_LABEL = { slide: 'slide', break: 'break', breakout: 'breakout' };
const KIND_ICON = { break: 'coffee', breakout: 'door-open' };
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
  onState(s) {
    followAppearance(s.appearance);
    if (plan && plan.active) draw();
  },
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

const quizHost = { live, copyText, countOwnSwitch };

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
      `<div class="p-titles"><div class="p-title">${esc(plan.session.title)}</div><div class="p-sub" data-sub></div></div>` +
      `<div class="p-chips" data-chips></div>` +
      `<span class="icon-button-group">` +
      `<button type="button" class="icon-button" data-keys data-track="live.keys" title="Keys" aria-label="Keyboard shortcuts" aria-expanded="false">${icon('keyboard')}</button>` +
      `<button type="button" class="icon-button" data-blackout data-track="live.blackout" title="Blackout (B)" aria-label="Blackout" aria-pressed="false">${icon('eye-off')}</button>` +
      `<button type="button" class="icon-button" data-theme-toggle data-track="header.theme" title="Toggle theme" aria-label="Toggle theme">${icon(currentTheme() === 'dark' ? 'sun' : 'moon')}</button>` +
      `<button type="button" class="icon-button" data-reset data-track="live.reset-session" title="Start the session over" aria-label="Start the session over">${icon('rotate-ccw')}</button>` +
      `<button type="button" class="icon-button" data-close data-track="live.close-session" title="Close the live session" aria-label="Close the live session">${icon('x')}</button></span>` +
    `</header>` +
    // Four columns on a wide screen: what is on stage, its notes and the session clocks ·
    // what comes next and the music · the item's controls (quiz, capture, timer) · the Zoom chat.
    // Narrower, the columns dissolve (display: contents) and the cards stack in their own order.
    `<div class="p-grid">` +
      `<div class="p-col p-col-stage">` +
        card('p-now', '<span class="live-dot"></span>On stage now', 'what Zoom sees', '<span class="p-flag" data-flag hidden></span>') +
        card('p-notes', 'Notes', '') +
        card('p-timing', 'Timing', '', '<span class="chip" data-drift hidden></span>') +
      `</div>` +
      `<div class="p-col p-col-next">` +
        card('p-next', 'Next', '') +
        card('p-music', `${icon('music')} Music`, '') +
      `</div>` +
      `<div class="p-col p-side">` +
        card('p-item', 'Timer', '') +
      `</div>` +
      `<div class="p-col p-col-chat">` +
        card('p-chat', 'Zoom chat', '', '<span class="p-age small muted" data-age></span>') +
      `</div>` +
    `</div>` +
    `<footer class="card p-strip">` +
      `<button type="button" class="icon-button" data-prev data-track="live.prev" aria-label="Previous (←)">${icon('chevron-left')}</button>` +
      `<div class="p-thumbs" data-thumbs></div>` +
      `<button type="button" class="icon-button" data-next data-track="live.next" aria-label="Next (→)">${icon('chevron-right')}</button>` +
    `</footer>` +
    `<div class="card p-keys" data-keypop hidden><p class="overline">Keys · stage or presenter window</p><dl class="p-keylist"></dl></div>`;

  const nowHost = document.createElement('div');
  nowHost.className = 'stage-host p-stage';
  root.querySelector('.p-now [data-body]').appendChild(nowHost);
  nowStage = createStage(nowHost, { guides: true, blackout: false });
  nowStage.el.addEventListener('wc-left-out', () => drawLeftOut(root));
  nowHost.title = 'Click to go on';
  clickToAdvance(nowHost, live);

  const nextBody = root.querySelector('.p-next [data-body]');
  nextBody.innerHTML = `<div class="p-next-view"><div class="stage-host p-stage p-stage-next"></div></div><p class="p-caption small muted" data-caption></p>` +
    `<p class="overline p-then-label">Then</p><ol class="p-then" data-then></ol>`;
  nextStage = createStage(nextBody.querySelector('.p-stage-next'), { guides: true, blackout: false });

  root.querySelector('.p-keylist').innerHTML =
    [['→ · PageDown', 'next — or click the stage'], ['← · PageUp', 'previous'], ['B', 'blackout'], ['T', 'timer start / pause'], ['M', 'timer +1 min'], ['Space', 'capture start / stop — else the timer'], ['Home · End', 'first · last item']]
      .map(([k, v]) => `<div><dt><kbd>${k}</kbd></dt><dd>${v}</dd></div>`).join('');
  const keysBtn = root.querySelector('[data-keys]');
  keysBtn.addEventListener('click', () => {
    const pop = root.querySelector('[data-keypop]');
    pop.hidden = !pop.hidden;
    keysBtn.setAttribute('aria-expanded', String(!pop.hidden));
  });
  buildChat();
  buildMusic();

  root.querySelector('[data-prev]').addEventListener('click', () => live.send('prev'));
  root.querySelector('[data-next]').addEventListener('click', () => live.send('next'));
  root.querySelector('[data-blackout]').addEventListener('click', () => live.send('blackout'));
  root.querySelector('[data-theme-toggle]').addEventListener('click', toggleTheme); // every screen, not just this one
  root.querySelector('[data-reset]').addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: 'Start the session over?',
      message: 'Back to the first item, with no clocks, timers, captures or chat. Nothing is deleted: the run so far is kept in the session folder as live-<date>-<time>.',
      actionLabel: 'Start over', danger: true,
    });
    if (ok) live.send('session_reset');
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
      : `<span class="p-thumb-text">${KIND_ICON[it.kind] ? icon(KIND_ICON[it.kind]) : ''}${esc(oneLine(it.title))}</span>`;
    const mark = it.hidden ? `<span class="p-thumb-hidden" aria-hidden="true">${icon('eye-off')}</span>` : '';
    const label = esc(oneLine(it.title)) + (it.hidden ? ' (hidden)' : '');
    return `<button type="button" class="p-thumb${it.hidden ? ' hidden-item' : ''}" data-track="live.goto" data-goto="${i + 1}" title="${label}" aria-label="${i + 1}. ${label}"><span class="p-thumb-img">${inner}${mark}</span><span class="p-thumb-n">${i + 1}</span></button>`;
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
  if ((it.kind === 'break' || it.kind === 'breakout') && it.timer) return `${it.kind} · ${Math.round(it.timer.seconds / 60)} min`;
  return KIND_LABEL[it.kind] || it.kind;
}

function draw() {
  const s = live.state;
  // A snapshot from before the latest plan (e.g. right after going live) is stale.
  if (!s || !plan || !plan.active || s.plan_rev !== plan.rev) return;
  const cur = items()[s.index] || null;
  const nxt = neighbour(items(), s.index, 1);
  const ctx = { plan, state: s, now: live.now() };
  nowStage.render(cur, ctx);
  nextStage.render(nxt, Object.assign({}, ctx, { state: Object.assign({}, s, { blackout: false }) }));

  root.querySelector('[data-sub]').textContent = cur
    ? `Presenter · ${cur.kind === 'slide' ? 'Slide' : 'Item'} ${s.index + 1} of ${s.count}${cur.hidden ? ' (hidden)' : ''} · ${oneLine(cur.title)}` : 'Presenter';
  const flag = root.querySelector('[data-flag]');
  flag.hidden = !s.blackout;
  flag.textContent = s.blackout ? 'Blackout — the stage is black' : '';
  root.querySelector('[data-blackout]').setAttribute('aria-pressed', String(!!s.blackout));

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
  root.querySelector('[data-prev]').disabled = !neighbour(items(), s.index, -1);
  root.querySelector('[data-next]').disabled = !nxt;
  drawItemCard(cur, s);
  drawMusic(cur, s);
  drawTiming(cur, s);
  drawChips();
  markChat();
}

function drawNext(s, nxt) {
  const card = root.querySelector('.p-next');
  card.querySelector('.p-meta').textContent = nxt ? `${nxt.index + 1} · ${oneLine(nxt.title)}` : 'end of the session';
  card.querySelector('[data-caption]').textContent = nxt
    ? (nxt.kind === 'activity' ? `${nxt.type_label || 'Activity'}${nxt.capture ? ' · starts when you press Space' : ''}` : kindLabel(nxt))
    : '';
  const then = nxt ? items().slice(nxt.index + 1).filter((it) => !it.hidden).slice(0, 4) : [];
  card.querySelector('[data-then]').innerHTML = then.map((it) =>
    `<li><span class="p-then-n">${it.index + 1}</span><span class="p-then-t">${esc(oneLine(it.title))}</span><span class="p-then-k">${esc(kindLabel(it))}</span></li>`).join('') ||
    '<li class="muted small">Nothing after this.</li>';
}

function drawNotes(cur) {
  const card = root.querySelector('.p-notes');
  const body = card.querySelector('[data-body]');
  if (!cur) { body.innerHTML = ''; return; }
  card.querySelector('.p-meta').textContent = cur.notes_own ? 'from the plan' : cur.kind === 'slide' ? 'from your PowerPoint' : cur.kind === 'activity' ? 'this activity' : '';
  // Notes written in the plan first; else a slide's PowerPoint notes, an activity's question, the title.
  const text = cur.notes || (cur.kind === 'activity' ? oneLine(cur.question) : cur.kind === 'slide' ? '' : oneLine(cur.title));
  let html = text ? `<div class="p-notes-text">${esc(text)}</div>`
    : `<p class="muted">${cur.kind === 'slide' ? 'No speaker notes on this slide.' : 'No notes — add them in the Plan tab.'}</p>`;
  if (cur.kind === 'activity' && cur.chat_prompt) {
    html += `<div class="p-prompt"><span class="muted">Prompt to paste in chat:</span> <span class="p-prompt-text">“${esc(cur.chat_prompt)}”</span>` +
      `<button type="button" class="button-surface" data-copy data-track="live.copy-prompt">${icon('copy')} Copy prompt</button></div>`;
  }
  body.innerHTML = html;
  const copy = body.querySelector('[data-copy]');
  if (copy) copy.addEventListener('click', () => copyText(cur.chat_prompt));
}

async function copyText(text, done = 'Prompt copied — paste it in the Zoom chat') {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
  } catch (e) {
    toast('Could not copy — select the text instead', 'error');
  }
}

function drawItemCard(cur, s) {
  const card = root.querySelector('.p-item');
  const body = card.querySelector('[data-body]');
  const t = cur && cur.timer;
  const cap = !!(cur && cur.capture);
  const quiz = isQuiz(cur);
  card.querySelector('.p-label').textContent = cap ? 'Capture' : quiz ? 'Quiz' : 'Timer';
  card.querySelector('.p-meta').textContent = cur ? oneLine(cur.title) : '';
  const key = `${cur ? cur.id : ''}|${t ? 1 : 0}|${cur && cur.kind === 'activity' ? 1 : 0}`;
  if (body.dataset.key !== key) {
    body.dataset.key = key;
    body.innerHTML = '';
    if (cap) {
      body.insertAdjacentHTML('beforeend',
        `<div class="p-cap-status"><span class="p-cap-dot" data-capdot></span><span data-capstate></span></div>` +
        `<p class="small muted p-cap-counts" data-capcounts></p>` +
        `<p class="small p-cap-counts p-cap-left" data-capleft hidden></p>` +
        `<button type="button" class="button-primary p-cap-btn" data-captoggle data-track="live.capture-toggle"></button>`);
      body.querySelector('[data-captoggle]').addEventListener('click', () => live.send('capture_toggle'));
    }
    if (quiz) buildQuiz(body, quizHost);
    if (!t && !cap && !quiz) {
      body.innerHTML = `<p class="muted">No timer on this item. Timers are decided item by item in the Plan tab.</p>`;
    } else if (t) {
      const starts = { manual: 'you start it', on_enter: 'starts when the item opens', with_capture: 'starts with the capture' }[t.start];
      const shows = { stage: 'on the stage', presenter: 'here only', both: 'stage and here' }[t.show_on];
      body.insertAdjacentHTML('beforeend',
        `<div class="p-timer${cap ? ' compact' : ''}"><span class="p-timer-clock" data-tclock></span><span class="p-timer-state small muted" data-tstate></span></div>` +
        `<p class="small muted">${esc(Math.round(t.seconds / 60 * 10) / 10)} min timer · ${esc(starts)} · shows ${esc(shows)}</p>` +
        `<div class="p-timer-actions">` +
        `<button type="button" class="${cap ? 'button-surface' : 'button-primary'}" data-ttoggle data-track="live.timer-toggle"></button>` +
        `<button type="button" class="button-surface" data-tadd data-track="live.timer-add" title="M">+1 min</button>` +
        `<button type="button" class="button-surface" data-treset data-track="live.timer-reset" title="Reset">${icon('rotate-ccw')} Reset</button></div>`);
      body.querySelector('[data-ttoggle]').addEventListener('click', () => live.send('timer_toggle'));
      body.querySelector('[data-tadd]').addEventListener('click', () => live.send('timer_add_minute'));
      body.querySelector('[data-treset]').addEventListener('click', () => live.send('timer_reset'));
    }
    if (cur && cur.kind === 'activity') {
      const row = switchRow(!!s.names, 'Show names on stage', () => live.send('names_toggle'));
      row.className += ' p-switch';
      row.firstElementChild.dataset.names = '';
      body.appendChild(row);
    }
    if (cap) {
      body.appendChild(countOwnSwitch(!!s.count_own, 'Count my own messages (rehearsal)'));
    }
  }
  const sw = body.querySelector('[data-names]');
  if (sw) setSwitch(sw, !!s.names);
  const own = body.querySelector('[data-count-own]');
  if (own) setSwitch(own, !!s.count_own);
  if (cap) drawCapture(body, s.capture);
  if (quiz) drawQuiz(body, s.quiz, live);
  if (t) tickItemTimer(cur, s);
}

/** The same "count my own chat / rehearsal" switch, shown during a capture and during a quiz. */
function countOwnSwitch(on, label) {
  const row = switchRow(on, label, () => live.send('capture_count_own'));
  row.className += ' p-switch';
  row.firstElementChild.dataset.countOwn = '';
  return row;
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
  drawLeftOut(body);
  body.querySelector('[data-capcounts]').textContent = c && (c.answers || c.hidden)
    ? `${c.answers} answer${c.answers === 1 ? '' : 's'} from ${c.people} ${c.people === 1 ? 'person' : 'people'}${c.hidden ? ` · ${c.hidden} hidden` : ''}`
    : status === 'idle' ? 'Press Space when the question is on stage.' : 'No answers yet.';
}

/** The word cloud's entries the stage has no room for (the plug-in counts them by the preview of what Zoom sees). */
function drawLeftOut(scope) {
  const note = scope.querySelector('[data-capleft]');
  if (!note) return;
  const wc = nowStage && nowStage.el.querySelector('.wc');
  const left = wc ? Number(wc.dataset.leftOut) || 0 : 0;
  note.hidden = !left;
  note.textContent = left ? `${left} ${left === 1 ? 'entry' : 'entries'} not shown on the stage` : '';
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
  tc.classList.toggle('paused', !!(ts && !running && !ts.done));
  card.querySelector('[data-tstate]').textContent = !ts ? 'not started' : ts.done ? 'time is up' : running ? 'running' : 'paused';
  const btn = card.querySelector('[data-ttoggle]');
  const label = running ? `${icon('pause')} Pause` : ts && !ts.done ? `${icon('play')} Resume` : `${icon('play')} Start timer`;
  if (btn.dataset.label !== label) { btn.dataset.label = label; btn.innerHTML = `${label}<kbd>T</kbd>`; }
}

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
      // Compact: the clock and its one button share a row.
      body.innerHTML = `<div class="p-clock"><span class="p-clock-big">00:00</span><span class="p-clock-of">of ${hms(dur * 60)}</span>` +
        `<button type="button" class="button-primary p-clock-start" data-cstart data-track="live.clock-start">${icon('clock')} Start the session clock</button></div>` +
        `<p class="small muted">The session clock starts when you say so; the section clock follows it.</p>`;
      body.querySelector('[data-cstart]').addEventListener('click', () => live.send('clock_start'));
    }
    return;
  }
  if (body.dataset.mode !== 'run') {
    body.dataset.mode = 'run';
    body.innerHTML = `<div class="p-clock"><span class="p-clock-big" data-elapsed></span><span class="p-clock-of">of ${hms(dur * 60)}</span>` +
      `<button type="button" class="button-ghost p-clock-reset" data-creset data-track="live.clock-reset">${icon('rotate-ccw')} Reset clock</button></div>` +
      `<div class="p-sec"><span class="p-sec-name" data-secname></span><span class="p-sec-left small" data-secleft></span></div>` +
      `<div class="p-bar"><span data-bar></span></div><div class="p-break small muted" data-break></div>`;
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
      ? `${brk.name} at ${fmtMinutes(brk.planned_start)}, in ${Math.max(0, Math.round(brk.planned_start - elapsedMin))} min`
      : `Planned end at ${fmtMinutes(plan.run.planned_minutes)}`;
  }
}

const READER_CHIP = {
  reading: ['ok', 'reading'],
  simulating: ['accent', 'simulating'],
  starting: ['warn', 'starting'],
  window_not_found: ['warn', 'pop out the chat'],
  stale: ['bad', 'no answer'],
  error: ['bad', 'error'],
  off: ['', 'off', 'start-reader'],
};
// The reader's source: its last heartbeat says Teams or Zoom (the simulator counts as Zoom);
// a stopped reader shows the source chosen on the switch, the one it starts on.
const isTeams = (r) => (r.state === 'off' || !r.source ? r.chosen : r.source) === 'teams';

function drawChips() {
  const box = root.querySelector('[data-chips]');
  if (!box || !live.state) return;
  const s = live.state;
  const chips = [];
  if (!live.online) chips.push(['bad', 'Server · reconnecting']);
  const r = s.reader || { state: 'off' };
  const rc = READER_CHIP[r.state] || ['bad', r.state];
  const what = isTeams(r) && r.state === 'window_not_found' ? 'open the chat' : rc[1];
  chips.push([rc[0], `${isTeams(r) ? 'Teams' : 'Zoom'} chat · ${what}`, rc[2], r.detail]);
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
  const mu = s.music;
  if (mu && (mu.tracks.length || mu.spotify || mu.state !== 'idle')) {
    const label = mu.track ? mu.track.label : '';
    // Links to Settings → Music (the Spotify account, the default fades) in the app's own window.
    const settings = 'href:/#settings/music';
    chips.push({ playing: ['ok', `Music · ${label}`, settings, mu.owner ? 'With the item timer' : 'Played by hand'],
      paused: ['warn', 'Music · paused', settings, label], error: ['bad', 'Music · error', settings, mu.detail] }[mu.state] ||
      ['', 'Music · idle', settings, 'Nothing playing']);
  }
  const qz = s.quiz;
  if (qz && qz.pin) {
    const pin = `${qz.pin.slice(0, 3)} ${qz.pin.slice(3)}`;
    const players = `${qz.player_count} player${qz.player_count === 1 ? '' : 's'}`;
    if (!qz.listener) chips.push(['bad', `Quiz · PIN ${pin} · players can't join`, null, 'The quiz player listener is off (quiz.public_port busy or 0) — see the log.']);
    else if (!qz.join_url) chips.push(['warn', `Quiz · PIN ${pin} · public URL not configured`, null, 'Set quiz.public_url in config/config.json (the public player address, e.g. https://quiz.<domain>), then restart.']);
    else chips.push(['ok', `Quiz · PIN ${pin} · ${players}`, null, qz.join_url]);
  }
  if (s.count_own) chips.push(['warn', 'Counting your messages', null, 'Rehearsal: your own chat messages count as answers. Turn it off before a live session.']);
  if (s.write_error) chips.push(['bad', s.write_error]);
  const html = chips.map(([k, t, act, title]) => act && act.startsWith('href:')
    ? `<a class="chip ${k}" href="${esc(act.slice(5))}" target="fs-app" title="${esc(title || '')}"><span class="dot"></span>${esc(t)}</a>`
    : act
      ? `<button type="button" class="chip ${k}" data-track="live.chip" data-act="${act}" title="${esc(title || '')}"><span class="dot"></span>${esc(t)}</button>`
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

// ----------------------------------------------------------------------- music

// The presenter's own music controls. Play starts the picked track by hand
// (no timer touches it); the item's own music is picked when an item with
// music comes on stage. The latest command wins (src/music/service.py).
let musicPickFor = null; // the item the picker was last pre-selected for

// ``state.music.cue`` names the file (e.g. "quiz-lobby"); the card shows its short name.
const cueLabel = (cue) => cue.replace(/^quiz-/, '');

function buildMusic() {
  const body = root.querySelector('.p-music [data-body]');
  body.innerHTML =
    // What is playing and its volume share the first row.
    `<div class="p-music-now"><span class="p-cap-dot" data-mdot></span><span class="p-music-title" data-mnow></span>` +
    `<label class="p-music-vol">${icon('volume-2')}<input type="range" min="0" max="100" step="5" data-mvol aria-label="Music volume"><output class="small" data-mvolout></output></label></div>` +
    `<select class="select-native" data-mpick aria-label="Track to play"></select>` +
    `<div class="p-music-link" data-mlinkrow hidden><input class="input" data-mlink placeholder="Paste a Spotify link" aria-label="Spotify link">` +
    `<button type="button" class="button-surface" data-mlinkplay>${icon('play')} Play link</button></div>` +
    `<div class="p-timer-actions">` +
    `<button type="button" class="button-primary" data-mtoggle data-track="live.music-toggle"></button>` +
    `<button type="button" class="icon-button" data-mprev data-track="live.music-prev" title="Previous track (Spotify)" aria-label="Previous track">${icon('skip-back')}</button>` +
    `<button type="button" class="icon-button" data-mnext data-track="live.music-next" title="Next track (Spotify)" aria-label="Next track">${icon('skip-forward')}</button>` +
    `<button type="button" class="button-surface" data-mstop data-track="live.music-stop" title="Stop (fades out)">${icon('square')} Stop</button>` +
    `<button type="button" class="button-surface" data-mfade data-track="live.music-fade" title="Fade out slowly, then stop">${icon('volume-x')} Fade out</button></div>` +
    `<p class="small muted p-music-hint" data-mhint></p>`;
  musicPickFor = null;
  const pick = body.querySelector('[data-mpick]');
  body.querySelector('[data-mtoggle]').addEventListener('click', () => {
    const m = live.state && live.state.music;
    if (m && (m.state === 'playing' || m.state === 'paused')) live.send('music_toggle');
    else if (pick.value) live.send('music_play', pick.value);
  });
  body.querySelector('[data-mprev]').addEventListener('click', () => live.send('music_prev'));
  body.querySelector('[data-mnext]').addEventListener('click', () => live.send('music_next'));
  body.querySelector('[data-mstop]').addEventListener('click', () => live.send('music_stop'));
  body.querySelector('[data-mfade]').addEventListener('click', () => live.send('music_fade_out'));
  const link = body.querySelector('[data-mlink]');
  const playLink = () => { if (link.value.trim()) live.send('music_play', link.value.trim()); };
  body.querySelector('[data-mlinkplay]').addEventListener('click', playLink);
  link.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); playLink(); } });
  const vol = body.querySelector('[data-mvol]');
  vol.addEventListener('input', () => { body.querySelector('[data-mvolout]').textContent = `${vol.value}%`; });
  vol.addEventListener('change', () => live.send('music_volume', vol.value));
}

function drawMusic(cur, s) {
  const card = root.querySelector('.p-music');
  const m = s.music;
  const show = !!(m && (m.tracks.length || m.spotify || m.state !== 'idle'));
  card.hidden = !show;
  if (!show) return;
  const body = card.querySelector('[data-body]');
  const pick = body.querySelector('[data-mpick]');
  pick.hidden = !m.tracks.length;
  body.querySelector('[data-mlinkrow]').hidden = !m.spotify;
  const sig = JSON.stringify(m.tracks.map((t) => [t.n, t.label]));
  if (pick.dataset.sig !== sig) {
    pick.dataset.sig = sig;
    pick.innerHTML = m.tracks.map((t) => `<option value="${t.n}">${esc(t.label)}${t.items.length ? '' : ' · audio/'}</option>`).join('');
    musicPickFor = null;
  }
  if (cur && musicPickFor !== cur.id) {
    musicPickFor = cur.id;
    const own = m.tracks.find((t) => t.items.includes(cur.id));
    if (own) pick.value = String(own.n);
  }
  const playing = m.state === 'playing';
  const paused = m.state === 'paused';
  body.querySelector('[data-mdot]').className = `p-cap-dot ${playing ? 'live' : paused ? 'paused' : m.state === 'error' ? 'error' : ''}`;
  body.querySelector('[data-mnow]').textContent = m.track ? `${m.track.label}${paused ? ' · paused' : ''}` : m.state === 'error' ? 'Music error' : 'Nothing playing';
  const btn = body.querySelector('[data-mtoggle]');
  const label = playing ? `${icon('pause')} Pause` : paused ? `${icon('play')} Resume` : `${icon('play')} Play`;
  if (btn.dataset.label !== label) { btn.dataset.label = label; btn.innerHTML = label; }
  btn.disabled = !playing && !paused && !m.tracks.length;
  const skippable = playing && !!m.track && m.track.kind === 'spotify'; // a file is one track
  body.querySelector('[data-mprev]').disabled = !skippable;
  body.querySelector('[data-mnext]').disabled = !skippable;
  body.querySelector('[data-mstop]').disabled = !playing && !paused;
  body.querySelector('[data-mfade]').disabled = !playing;
  const vol = body.querySelector('[data-mvol]');
  if (document.activeElement !== vol) {
    vol.value = String(m.volume);
    body.querySelector('[data-mvolout]').textContent = `${m.volume}%`;
  }
  const owner = m.owner && items().find((it) => it.id === m.owner);
  const hint = body.querySelector('[data-mhint]');
  hint.textContent = m.state === 'error' ? m.detail
    : m.cue ? `Quiz cue: ${cueLabel(m.cue)}`  // the quiz's phase sound is playing, not the presenter's pick
      : owner ? `Follows the timer of “${oneLine(owner.title)}”.`
        : playing || paused ? 'Played by hand — no timer touches it.' : m.tracks.length ? 'Pick a track and press Play, or let an item’s timer start its music.' : 'Paste a Spotify link to play it.';
  hint.classList.toggle('warn', m.state === 'error');
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
  if (r.state === 'window_not_found') {
    hint = isTeams(r)
      ? 'In Teams: open the meeting chat. In a browser, keep the Teams tab the active tab of its window.'
      : 'In Zoom: Chat → … → Pop out. The reader only sees the popped-out chat.';
  } else if (r.state === 'stale' || r.state === 'error') hint = r.detail || '';
  else if (!chat.length && r.state === 'off') hint = `Start the reader to see the ${isTeams(r) ? 'Teams' : 'Zoom'} chat here, or simulate answers to rehearse.`;
  const chosen = r.chosen === 'teams' ? 'teams' : 'zoom';
  root.querySelector('.p-chat .p-label').textContent = `${isTeams(r) ? 'Teams' : 'Zoom'} chat`;
  const key = `${r.state}|${hint}|${chosen}`;
  if (foot.dataset.key === key) return;
  foot.dataset.key = key;
  foot.innerHTML = (hint ? `<p class="small muted p-chat-hint">${esc(hint)}</p>` : '') +
    `<div class="segmented" role="group" aria-label="Chat source">${[['zoom', 'Zoom'], ['teams', 'Teams']].map(([v, l]) =>
      `<button type="button" class="segmented-item" data-track="live.reader-source" aria-pressed="${v === chosen}" data-source="${v}">${l}</button>`).join('')}</div>` +
    `<div class="row-actions">` +
    (running && r.state !== 'simulating'
      ? `<button type="button" class="button-surface" data-reader="stop" data-track="live.reader-toggle">${icon('square')} Stop reader</button>`
      : `<button type="button" class="button-surface" data-reader="start" data-track="live.reader-toggle">${icon('play')} Start reader</button>`) +
    `<button type="button" class="button-surface" data-sim data-track="live.simulate">${icon('wand-sparkles')} Simulate answers</button></div>`;
  const rb = foot.querySelector('[data-reader]');
  rb.addEventListener('click', () => (rb.dataset.reader === 'stop' ? stopReader() : startReader()));
  foot.querySelector('[data-sim]').addEventListener('click', simulateAnswers);
  foot.querySelectorAll('[data-source]').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.source !== chosen) chooseSource(b.dataset.source, foot);
  }));
}

// Zoom · Teams: saved for the next session too; a running reader restarts on it.
async function chooseSource(source, foot) {
  const name = source === 'teams' ? 'Teams' : 'Zoom';
  foot.querySelectorAll('[data-source]').forEach((b) => { b.disabled = true; });
  try {
    const res = await api('/api/chat/reader/source', { method: 'PUT', body: { source } });
    toast(res.restarted ? `Reading the ${name} chat now` : `${name} chat chosen — the reader reads it when it starts`);
  } catch (e) {
    toast(e.message, 'error');
    foot.dataset.key = '';
    drawChatFoot();
  }
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
  if (cur && cur.kind === 'activity' && cur.type === 'quiz') {
    // A quiz question: twelve simulated people each type one random letter of its answers (the chat fallback).
    const o = cur.options || {};
    const letters = ['A', 'B', 'C', 'D'].filter((_, i) => String(o[`answer_${i + 1}`] ?? '').trim());
    answers = Array.from({ length: 12 }, () => letters[Math.floor(Math.random() * letters.length)]).filter(Boolean);
    try {
      await api('/api/chat/simulate', { method: 'POST', body: { kind: 'list', every_ms: 600, answers } });
      toast(`Simulating ${answers.length} chat answers (${letters.join(', ')})`);
    } catch (e) { toast(e.message, 'error'); }
    return;
  }
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
  if (isQuiz(cur)) tickQuiz(root.querySelector('.p-item [data-body]'), s.quiz, live);
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

bindKeys(live);

// Space means "capture" (or "timer") on the presenter: a button clicked with the mouse must
// not keep the focus (Space would click it again). Keyboard focus is kept.
root.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (b && e.detail > 0) b.blur();
});
