// The quiz on the stage (#53) — one renderer for the three quiz types:
//
//   quiz_lobby   the QR code, the join link and PIN, the players popping in
//   quiz         the question (answers on coloured tiles with their shapes and
//                letters, a countdown from the server's deadline, the answered
//                count), then its reveal (distribution bars, the correct answer
//                marked with a check and a label — never by colour alone), then
//                the leaderboard (top 5, rank changes animated)
//   quiz_podium  3rd, then 2nd, then 1st — one place per Next (#89): the server's
//                podium_step says how many are shown; each rises as it is revealed
//
// Everything live comes from ctx.quiz, the server's snapshot for this item. The
// correct answer only exists there once the question has closed; it is never
// read from the item's options (the plan the stage gets carries it), so nothing
// can show it before the reveal. Without a game (the Plan tab's and the
// presenter's previews: ctx.preview) it draws samples — the question as it
// opens, and the sample players of the type's editor.json. The look is the
// session theme's (fonts, ink, chips); the four answer colours match the phones
// (a theme can move them with --st-quiz-1..4). prefers-reduced-motion: no
// pops, rises or slides.
//
// Long answers (#84, Kahoot allows 75 characters): the reveal's bars move up into
// the band a corner camera keeps free under the title (beside the camera), so the
// tiles keep room for three lines; a tile whose text still does not fit steps its
// size down (fitTiles). The lobby shows the names that fit in full rows, then "+N more".
//
// The camera (#88, #148): every phase stays out of the item's camera zone. A camera strip is
// outside the content area already; under a corner camera the title flows around the corner
// (stage.css floats a box of the camera's size in the head), the lobby and podium start
// below it, the question's status row and the reveal's bars rise into the band beside it
// (--qz-band, --st-head-right) and the leaderboard sits beside it. When the answers still do
// not fit at their smallest — a long question beside a camera strip — the question steps
// down too (fitPhase), so nothing is cut or runs off the stage.

import { esc } from '/static/js/ui.js';

const KEYS = ['A', 'B', 'C', 'D'];
// Answer n's shape — the same four as the phones (app/player/play.html): triangle, diamond, circle, square.
const SHAPES = {
  1: '<path d="M12 3 22.5 21h-21z"/>',
  2: '<path d="M12 1.5 22.5 12 12 22.5 1.5 12z"/>',
  3: '<circle cx="12" cy="12" r="10"/>',
  4: '<rect x="3" y="3" width="18" height="18" rx="1.5"/>',
};
const shape = (n) => `<svg class="qz-shape" viewBox="0 0 24 24" aria-hidden="true">${SHAPES[n]}</svg>`;
const ICON = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
const RING = 2 * Math.PI * 54; // the countdown ring's circumference (r = 54 in a 120 box)
const TOP = 5; // leaderboard rows
const MAX_NAMES = 60; // lobby names drawn at most; those that do not fit, and the rest, are "+N more"
const MIN_FIT = 0.6; // fitTiles steps the answers down to 60 % of their size, no further

// Every word the audience reads on the stage, in the session's language (ctx.lang, #91). The
// presenter's controls stay in English. tests/test_quiz_language.py checks both languages have
// every key this file uses (and the phone's table in app/player/static/play.js likewise).
const WORDS = {
  en: {
    question: (i, n) => `Question ${i} of ${n}`,
    answered: 'answered',
    players: (n) => `${n} player${n === 1 ? '' : 's'}`,
    join: 'Join on your phone',
    pin: 'Game PIN',
    waiting: 'Waiting for players…',
    leaderboard: 'Leaderboard',
    correct: 'Correct',
    more: (n) => `+${n} more`,
    no_url: 'Joining by phone is not set up',
    no_listener: 'Joining by phone is not available right now',
    starting: 'The quiz starts soon',
    no_players: 'No players',
    sample_url: 'your-link/play',
  },
  es: {
    question: (i, n) => `Pregunta ${i} de ${n}`,
    answered: 'han respondido',
    players: (n) => `${n} jugador${n === 1 ? '' : 'es'}`,
    join: 'Únete con tu móvil',
    pin: 'PIN del juego',
    waiting: 'Esperando jugadores…',
    leaderboard: 'Clasificación',
    correct: 'Correcta',
    more: (n) => `+${n} más`,
    no_url: 'La entrada con el móvil no está configurada',
    no_listener: 'Ahora mismo no se puede entrar con el móvil',
    starting: 'El quiz empieza pronto',
    no_players: 'Sin jugadores',
    sample_url: 'tu-enlace/play',
  },
};

const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

// ----------------------------------------------------------------- samples

// type → its editor.json samples (player names), fetched once, for previews only.
let samples = null;
let samplesLoading = null;
function sampleNames(type, redraw) {
  if (samples) return samples[type] || [];
  if (!samplesLoading) {
    samplesLoading = fetch('/api/activities')
      .then((r) => (r.ok ? r.json() : { types: [] }))
      .then((d) => { samples = Object.fromEntries((d.types || []).map((t) => [t.type, t.samples || []])); })
      .catch(() => { samples = {}; });
  }
  samplesLoading.then(redraw);
  return [];
}

function samplePlayers(type, redraw) {
  return sampleNames(type, redraw).map((name, i) => ({ id: `sample-${i}`, name }));
}

function sampleBoard(type, redraw) {
  return samplePlayers(type, redraw).map((p, i) => ({ ...p, rank: i + 1, score: 9150 - i * 780 - i * i * 45, last_points: 0 }));
}

// ------------------------------------------------------------------ drawing

const views = new WeakMap(); // body → { key, … } what is drawn there now

/** The quiz type's renderer: `render` of quiz/, quiz_lobby/ and quiz_podium/stage.js. */
export function draw(body, ctx, type) {
  const q = ctx.quiz && !ctx.quiz.error ? ctx.quiz : null;
  const w = WORDS[ctx.lang] || WORDS.en;
  const view = type === 'quiz_lobby' ? 'lobby' : type === 'quiz_podium' ? 'podium'
    : q && (q.phase === 'reveal' || q.phase === 'leaderboard') ? q.phase : 'question';
  const key = [view, q ? q.game_id : ctx.preview ? 'sample' : 'none', ctx.item.id].join('|');
  const prev = views.get(body);
  const fresh = !prev || prev.key !== key || !body.firstElementChild;
  const st = fresh ? { key } : prev;
  views.set(body, st);
  body.dataset.qzView = view; // the leaderboard takes the whole screen (stage.css hides the question)
  const redraw = () => { if (body.isConnected && views.get(body) === st) draw(body, ctx, type); };
  DRAW[view](body, { ctx, q, w, st, fresh, redraw });
}

export function render(body, result, ctx) {
  draw(body, ctx, 'quiz');
}

/** The item's answers that have a text: [{ n: 1–4, text }]. Only the answer texts — never `correct`. */
function answersOf(options) {
  const o = options || {};
  return [1, 2, 3, 4].map((n) => ({ n, text: String(o[`answer_${n}`] ?? '').trim() })).filter((a) => a.text);
}

function tile(a, mark, w) {
  return `<div class="qz-tile qz-a${a.n}${mark ? ` ${mark}` : ''}" data-choice="${a.n}">${shape(a.n)}` +
    `<span class="qz-key">${KEYS[a.n - 1]}</span><span class="qz-text">${esc(a.text)}</span>` +
    (mark === 'correct' ? `<span class="qz-mark" data-correct>${ICON('check')}${esc(w.correct)}</span>` : '') + '</div>';
}

const DRAW = { lobby, question, reveal, leaderboard, podium };

// -------------------------------------------------------------------- lobby

function lobby(body, { ctx, q, w, st, fresh, redraw }) {
  if (fresh) {
    body.innerHTML = '<div class="qz-lobby">' +
      '<div class="qz-qr" data-qz-qr></div>' +
      '<div class="qz-side">' +
      `<div class="qz-join" data-qz-join><p class="qz-label">${esc(w.join)}</p><p class="qz-url" data-qz-url></p>` +
      `<p class="qz-label">${esc(w.pin)}</p><p class="qz-pin" data-qz-pin></p></div>` +
      '<p class="qz-note" data-qz-note hidden></p>' +
      `<p class="qz-count">${ICON('users')}<span data-qz-count></span></p>` +
      '<ul class="qz-names" data-qz-names></ul>' +
      '</div></div>';
    st.shown = new Map(); // player id → its <li>
  }
  const sample = !q && ctx.preview;
  // Can a phone join now? A clear state when it cannot, instead of a QR that leads nowhere.
  const note = q ? (!q.listener ? w.no_listener : !q.join_url ? w.no_url : '') : sample ? '' : w.starting;
  const qr = body.querySelector('[data-qz-qr]');
  const want = q && !note ? `/api/quiz/qr.svg?pin=${encodeURIComponent(q.pin)}` : note ? 'off' : 'sample';
  if (st.qr !== want) {
    st.qr = want;
    qr.classList.toggle('off', want === 'off' || want === 'sample');
    qr.innerHTML = want === 'off' ? ICON('smartphone')
      : want === 'sample' ? `<span class="qz-qr-sample">QR</span>` : `<img alt="" src="${want}">`;
  }
  body.querySelector('[data-qz-join]').hidden = !!note;
  const noteEl = body.querySelector('[data-qz-note]');
  noteEl.hidden = !note;
  noteEl.innerHTML = note ? `${ICON('triangle-alert')}<span>${esc(note)}</span>` : '';
  const pin = q ? String(q.pin || '') : '123456';
  body.querySelector('[data-qz-pin]').textContent = pin.length === 6 ? `${pin.slice(0, 3)} ${pin.slice(3)}` : pin;
  body.querySelector('[data-qz-url]').textContent = q && q.join_url
    ? q.join_url.replace(/^https?:\/\//, '').replace(/\?.*$/, '') : w.sample_url;

  const players = q ? q.players || [] : sample ? samplePlayers('quiz_lobby', redraw) : [];
  body.querySelector('[data-qz-count]').textContent = players.length ? w.players(players.length) : w.waiting;
  const list = body.querySelector('[data-qz-names]');
  const visible = players.slice(0, MAX_NAMES);
  const ids = new Set(visible.map((p) => p.id));
  for (const [id, li] of st.shown) {
    if (!ids.has(id)) { li.remove(); st.shown.delete(id); } // kicked
  }
  const pop = !fresh && !reduced(); // names already there when the stage opens do not pop
  for (const p of visible) {
    let li = st.shown.get(p.id);
    if (!li) {
      li = document.createElement('li');
      li.className = `qz-player${pop ? ' pop' : ''}`;
      li.dataset.player = p.id;
      st.shown.set(p.id, li);
    }
    li.textContent = p.name;
    list.appendChild(li); // (re)appended in join order
  }
  st.total = players.length;
  st.w = w;
  fitNames(list, st.total, w);
  if (fresh) {
    // A stage opened on a lobby that already has players (#111) draws before the quiz's
    // stylesheet and fonts arrive: fit again once they have (the list takes its size then).
    const refit = () => { if (list.isConnected && views.get(body) === st) fitNames(list, st.total, st.w); };
    document.fonts?.ready.then(refit);
    if (typeof ResizeObserver === 'function') {
      const ro = new ResizeObserver(() => { if (list.isConnected) refit(); else ro.disconnect(); });
      ro.observe(list);
    }
  }
}

/**
 * Show the names that fit in the list's full rows, then a "+N more" chip for the
 * rest (the names not drawn and those that do not fit). Without a layout (a
 * hidden preview) or before stage.css has made the list the chips' offset parent,
 * there is nothing to measure: every drawn name stays.
 */
function fitNames(list, total, w) {
  const chips = [...list.querySelectorAll('.qz-player:not(.qz-more)')];
  let more = list.querySelector('.qz-more');
  let shown = chips.length;
  chips.forEach((li) => { li.hidden = false; });
  const place = () => {
    if (shown >= total) {
      if (more) more.remove();
      more = null;
      return;
    }
    if (!more) { more = document.createElement('li'); more.className = 'qz-player qz-more'; }
    more.textContent = w.more(total - shown);
    list.appendChild(more);
  };
  place();
  const probe = chips[0] || more;
  if (!probe || !list.clientHeight || probe.offsetParent !== list) return;
  const fits = (li) => !li || li.offsetTop + li.offsetHeight <= list.clientHeight;
  while (shown > 0 && !fits(more || chips[shown - 1])) {
    chips[--shown].hidden = true;
    place();
  }
}

// ----------------------------------------------------------------- question

function question(body, { ctx, q, w, st, fresh }) {
  const answers = answersOf(ctx.options);
  if (fresh) {
    body.innerHTML = '<div class="qz-q">' +
      '<div class="qz-status"><span class="qz-chip" data-qz-index hidden></span><span class="qz-spacer"></span>' +
      '<span class="qz-answered" data-qz-answered hidden></span>' +
      '<div class="qz-clock" data-qz-clock hidden><svg viewBox="0 0 120 120" aria-hidden="true">' +
      `<circle class="qz-track" cx="60" cy="60" r="54"/><circle class="qz-ring" cx="60" cy="60" r="54" stroke-dasharray="${RING}"/></svg>` +
      '<span class="qz-secs" data-qz-secs></span></div></div>' +
      `<div class="qz-answers n${answers.length}">${answers.map((a) => tile(a, '', w)).join('')}</div>` +
      '</div>';
    st.clock = body.querySelector('[data-qz-clock]');
    const root = body.firstElementChild;
    fitPhase(body, root);
    document.fonts?.ready.then(() => { if (root.isConnected) fitPhase(body, root); });
  }
  const index = body.querySelector('[data-qz-index]');
  index.hidden = !(q && q.question_index != null);
  if (!index.hidden) index.textContent = w.question(q.question_index + 1, q.question_count);
  const answered = body.querySelector('[data-qz-answered]');
  answered.hidden = !q;
  if (q) answered.innerHTML = `${ICON('check')}<b>${q.answered_count}</b> / ${q.player_count} ${esc(w.answered)}`;

  // The countdown: the server's deadline, on this screen's clock corrected by the server's (ctx.now).
  st.limit = Number(q && q.time_limit) || Number((ctx.options || {}).time_limit) || 20;
  st.deadline = q && q.deadline_ms ? q.deadline_ms : null;
  st.offset = (Number(ctx.now) || Date.now()) - Date.now();
  st.clock.hidden = !(st.deadline || ctx.preview); // a question outside any game has no clock
  tick(st);
  if (st.deadline && !st.timer) st.timer = setInterval(() => tick(st), 100);
}

function tick(st) {
  const el = st.clock;
  if (!el.isConnected) {
    clearInterval(st.timer);
    st.timer = null;
    return;
  }
  const limitMs = st.limit * 1000;
  const left = st.deadline ? Math.max(0, Math.min(limitMs, st.deadline - (Date.now() + st.offset))) : limitMs;
  el.dataset.leftMs = String(Math.round(left));
  const secs = String(Math.ceil(left / 1000));
  const secsEl = el.querySelector('[data-qz-secs]');
  secsEl.textContent = secs;
  secsEl.classList.toggle('qz-secs-3', secs.length >= 3); // 120/240 s limits (Kahoot allows both, #93)
  el.querySelector('.qz-ring').style.strokeDashoffset = String(RING * (1 - left / limitMs));
  el.classList.toggle('low', !!st.deadline && left <= 5000);
}

// ------------------------------------------------------------------- reveal

function reveal(body, { ctx, q, w, fresh }) {
  const answers = answersOf(ctx.options);
  const dist = q.distribution || [];
  const correct = new Set(q.correct || []);
  const top = Math.max(1, ...answers.map((a) => dist[a.n - 1] || 0));
  const mark = (a) => (correct.has(a.n) ? 'correct' : 'wrong');
  body.innerHTML = `<div class="qz-reveal${fresh ? '' : ' qz-still'}">` +
    '<div class="qz-bars">' + answers.map((a) => {
      const count = dist[a.n - 1] || 0;
      return `<div class="qz-bar qz-a${a.n} ${mark(a)}" data-bar="${a.n}" style="--h:${count / top}">` +
        `<span class="qz-bar-count">${correct.has(a.n) ? ICON('check') : ''}${count}</span>` +
        `<div class="qz-bar-fill">${shape(a.n)}</div></div>`;
    }).join('') + '</div>' +
    `<div class="qz-answers n${answers.length}">${answers.map((a) => tile(a, mark(a), w)).join('')}</div>` +
    '</div>';
  const root = body.firstElementChild;
  fitPhase(body, root);
  document.fonts?.ready.then(() => { if (root.isConnected) fitPhase(body, root); });
}

/**
 * Lay a question or its reveal out in what the stage leaves it (#148): measure the band
 * beside a corner camera (--qz-band; .qz-beside while there is one), then fit the answers
 * (fitTiles). Only when they still do not fit at their smallest does the question above
 * them step down, to MIN_FIT of its own size, re-measuring at each step: a question whose
 * answers fit keeps its size.
 */
function fitPhase(body, root) {
  const title = body.parentElement && body.parentElement.querySelector(':scope > .st-head .st-question');
  if (title) {
    if (title.dataset.qzSize === undefined) title.dataset.qzSize = title.style.fontSize;
    title.style.fontSize = title.dataset.qzSize; // the item's own size: every fit starts from it
  }
  const step = () => {
    const band = bandOf(body);
    root.style.setProperty('--qz-band', `${band}px`);
    root.classList.toggle('qz-beside', band > 0);
    return fitTiles(root);
  };
  let fits = step();
  const base = title ? parseFloat(getComputedStyle(title).fontSize) || 0 : 0;
  let size = base;
  while (!fits && size > base * MIN_FIT) {
    size = Math.max(base * MIN_FIT, size * 0.94);
    title.style.fontSize = `${size.toFixed(1)}px`;
    fits = step();
  }
}

/**
 * The band a corner camera keeps free under the title (--st-head-min holds the
 * head down to the camera's bottom edge): the head's height below its own text.
 * 0 without a corner camera, or before layout.
 */
function bandOf(body) {
  const head = body.parentElement && body.parentElement.querySelector(':scope > .st-head');
  if (!head) return 0;
  let bottom = head.offsetTop;
  for (const el of head.children) {
    if (el.offsetParent) bottom = Math.max(bottom, el.offsetTop + el.offsetHeight);
  }
  return Math.max(0, head.offsetTop + head.offsetHeight - bottom);
}

/**
 * Step the answers' size down (to MIN_FIT of the theme's) until every tile shows
 * all of its text and, on the reveal, the tiles fit under the bars. Answers that
 * fit keep their size, so a question or a reveal of short answers is unchanged.
 * True when they fit (or there is no layout to measure).
 */
function fitTiles(root) {
  if (!root) return true;
  const answers = root.querySelector('.qz-answers');
  root.style.removeProperty('--qz-fs');
  if (!answers || !answers.firstElementChild || !root.clientHeight) return true; // not laid out: nothing to measure
  const tiles = [...answers.children];
  const base = parseFloat(getComputedStyle(tiles[0]).fontSize) || 48;
  // A tile's text fits inside its padding, the same test as the e2e stories' CLIPPED, and breaks
  // no word in the middle unless it must (#148): a narrow column, beside a camera strip, steps the
  // answers down instead of leaving their lines on the tile's edges or splitting "Observation-s".
  const broken = (text) => {
    text.style.overflowWrap = 'normal';
    const wide = text.scrollWidth > text.clientWidth + 1;
    text.style.overflowWrap = '';
    return wide;
  };
  const over = () => answers.offsetTop + answers.offsetHeight > root.clientHeight + 1 ||
    tiles.some((t) => {
      const text = t.querySelector('.qz-text');
      return t.scrollHeight > t.clientHeight + 1 || text.offsetHeight > t.clientHeight + 1 || broken(text);
    });
  let size = base;
  while (over() && size > base * MIN_FIT) {
    size = Math.max(base * MIN_FIT, size * 0.94);
    root.style.setProperty('--qz-fs', `${size.toFixed(1)}px`);
  }
  return !over();
}

// -------------------------------------------------------------- leaderboard

function leaderboard(body, { q, w, fresh }) {
  const board = q.leaderboard || [];
  // Where each player stood before this question: the score without its points (ties keep today's order).
  const before = [...board].sort((a, b) => (b.score - b.last_points) - (a.score - a.last_points) || a.rank - b.rank);
  const was = new Map(before.map((p, i) => [p.id, i + 1]));
  const rows = board.slice(0, TOP);
  body.innerHTML = `<div class="qz-board${fresh ? '' : ' qz-still'}">` +
    `<h2 class="qz-board-title">${esc(w.leaderboard)}</h2>` +
    `<ol class="qz-rows" style="--rows:${Math.max(1, rows.length)}">` + rows.map((p) => {
      const from = Math.min(was.get(p.id) || p.rank, TOP + 1);
      return `<li class="qz-row${p.rank === 1 ? ' first' : ''}" data-player="${esc(p.id)}" data-from="${from}" ` +
        `style="--to:${p.rank};--from:${from};--o0:${from > TOP ? 0 : 1}">` +
        `<span class="qz-rank">${p.rank}</span><span class="qz-who">${esc(p.name)}</span>` +
        `<span class="qz-gain">${p.last_points ? `+${p.last_points}` : ''}</span><span class="qz-score">${p.score}</span></li>`;
    }).join('') + '</ol>' +
    (board.length ? '' : `<p class="qz-empty">${esc(w.no_players)}</p>`) + '</div>';
}

// ------------------------------------------------------------------- podium

// Every place is laid out from the start (2nd, 1st, 3rd, so none moves when another
// appears); a place not revealed yet is invisible. A place revealed since the last
// draw rises (.rise); those already shown when the podium is drawn afresh (a stage
// reloaded mid-podium), or redrawn (a player removed), stand still.
function podium(body, { ctx, q, w, st, fresh, redraw }) {
  const board = q ? q.leaderboard || [] : ctx.preview ? sampleBoard('quiz_podium', redraw) : [];
  const places = [2, 1, 3].map((rank) => board.find((p) => p.rank === rank)).filter(Boolean);
  const count = places.length;
  // A preview (no game) shows every place; a game shows as many as the server's step, 3rd first.
  const step = q ? Math.min(Number(q.podium_step) || 0, count) : count;
  const shown = new Set(places.filter((p) => p.rank > count - step).map((p) => p.rank));
  const sig = JSON.stringify([places.map((p) => [p.id, p.name, p.score]), board.length, !!(q || ctx.preview)]);
  if (fresh || st.sig !== sig) {
    body.innerHTML = '<div class="qz-podium">' + places.map((p) =>
      `<div class="qz-step p${p.rank}" data-rank="${p.rank}"><div class="qz-top">` +
      (p.rank === 1 ? ICON('crown') : '') +
      `<span class="qz-pname">${esc(p.name)}</span><span class="qz-pscore">${p.score}</span></div>` +
      `<div class="qz-block"><span>${p.rank}</span></div></div>`).join('') +
      (board.length ? '' : `<p class="qz-empty">${esc(q || ctx.preview ? w.no_players : w.starting)}</p>`) + '</div>';
    if (fresh) st.shown = shown; // drawn afresh: what is shown already stands still
    st.sig = sig;
  }
  for (const el of body.querySelectorAll('.qz-step')) {
    const rank = Number(el.dataset.rank);
    const on = shown.has(rank);
    el.classList.toggle('shown', on);
    el.setAttribute('aria-hidden', String(!on));
    if (!on) el.classList.remove('rise');
    else if (!st.shown.has(rank)) el.classList.add('rise'); // revealed by this step
  }
  st.shown = shown;
}
