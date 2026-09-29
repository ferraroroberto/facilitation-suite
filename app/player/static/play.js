// The quiz player page (#52). The server owns the game; this page renders the phone's own
// view (QuizService.player_view) and sends taps. Reliability rules (#34 §4):
// - identity (player_id + secret) lives in localStorage: a reload, a locked phone or a
//   network switch resumes the same player;
// - an answer is retried with backoff until the server acks it, and "Locked in" shows only
//   on that ack ("accepted" / "duplicate"); "too_late" says so;
// - live updates come over a WebSocket; while it is down, or after it dropped twice within
//   30 s, the page polls every second (and keeps trying the socket). `?transport=poll`
//   forces polling (a dev toggle).

const STORE = 'facilitation-suite.play';
const THEME = 'facilitation-suite.play.theme';
const POLL_MS = 1000;
const FLAKY_WINDOW_MS = 30000;
// Every word the phone says, in the game's language (#91): the view's `lang` once joined, the
// live session's (/play/api/ping) before. play.html's own words carry data-t="<key>" (and
// data-t-placeholder / data-t-label). tests/test_quiz_language.py checks both languages have
// every key used here and there. The texts the facilitator wrote (questions, answers) are as typed.
const TEXT = {
  en: {
    title: 'Quiz',
    loading: 'Loading…',
    theme: 'Switch light or dark',
    conn_offline: 'Offline',
    conn_reconnecting: 'Reconnecting…',
    conn_connected: 'Connected',
    conn_live: 'Live',
    conn_online: 'Online',
    conn_checking: 'Connecting…',
    pin_label: 'Game PIN',
    nickname_label: 'Nickname',
    nickname_placeholder: 'Your name',
    join: 'Join',
    joining: 'Joining…',
    pin_digits: 'The game PIN has 6 digits.',
    pick_nickname: 'Pick a nickname.',
    wrong_pin: 'No game has that PIN — check the number on the screen.',
    no_game: "That game isn't open right now — wait for the host.",
    closed: 'That game has ended.',
    unreachable: "Can't reach the quiz — check your connection and try again.",
    too_many: 'Too many tries — wait a moment and try again.',
    failed: 'That did not work — try again.',
    no_quiz: 'No quiz is running right now — keep this page open.',
    gone: 'You are no longer in this game — join again.',
    in_lobby: "You're in! Watch the screen — the quiz starts soon.",
    watch: 'Watch the screen.',
    question: (i, n) => `Question ${i} of ${n}`,
    question_plain: 'Question',
    shape: { 1: 'triangle', 2: 'diamond', 3: 'circle', 4: 'square' },
    tile: (n, shapeName, text) => `Answer ${n} (${shapeName})${text ? `: ${text}` : ''}`,
    seconds: (s) => `${s} s`,
    times_up: "Time's up",
    locked: 'Locked in',
    watch_answer: 'Watch the screen for the answer.',
    too_late: 'Too late',
    too_late_detail: 'Time was up before your answer arrived.',
    not_sent: 'Not sent',
    refused: 'That answer was refused.',
    sending: 'Sending…',
    retrying: 'Sending… (retrying)',
    keep_open: 'Keep this page open.',
    correct: 'Correct',
    not_this_time: 'Not this time',
    no_answer: 'No answer',
    right_one: 'The answer',
    right_many: 'The answers',
    gained: (n) => `+${n} points`,
    points: (n) => `${n} points`,
    streak: (n) => `streak ${n}`,
    score: 'Score',
    final_score: 'Final score',
    leaderboard: 'Leaderboard',
    game_over: 'Game over',
    wait_podium: 'Wait for the podium…',
    removed: 'Removed',
    removed_detail: 'The host removed you from this game.',
  },
  es: {
    title: 'Quiz',
    loading: 'Cargando…',
    theme: 'Cambiar a modo claro u oscuro',
    conn_offline: 'Sin conexión',
    conn_reconnecting: 'Reconectando…',
    conn_connected: 'Conectado',
    conn_live: 'En directo',
    conn_online: 'En línea',
    conn_checking: 'Conectando…',
    pin_label: 'PIN del juego',
    nickname_label: 'Apodo',
    nickname_placeholder: 'Tu nombre',
    join: 'Entrar',
    joining: 'Entrando…',
    pin_digits: 'El PIN del juego tiene 6 cifras.',
    pick_nickname: 'Elige un apodo.',
    wrong_pin: 'Ningún juego tiene ese PIN: revisa el número de la pantalla.',
    no_game: 'Ese juego aún no está abierto: espera a que empiece.',
    closed: 'Ese juego ya ha terminado.',
    unreachable: 'No hay conexión con el quiz: revisa tu conexión y vuelve a intentarlo.',
    too_many: 'Demasiados intentos: espera un momento y vuelve a intentarlo.',
    failed: 'No ha funcionado: vuelve a intentarlo.',
    no_quiz: 'Ahora mismo no hay ningún quiz en marcha: deja esta página abierta.',
    gone: 'Ya no estás en este juego: vuelve a entrar.',
    in_lobby: '¡Ya estás dentro! Mira la pantalla: el quiz empieza enseguida.',
    watch: 'Mira la pantalla.',
    question: (i, n) => `Pregunta ${i} de ${n}`,
    question_plain: 'Pregunta',
    shape: { 1: 'triángulo', 2: 'rombo', 3: 'círculo', 4: 'cuadrado' },
    tile: (n, shapeName, text) => `Respuesta ${n} (${shapeName})${text ? `: ${text}` : ''}`,
    seconds: (s) => `${s} s`,
    times_up: '¡Se acabó el tiempo!',
    locked: '¡Respuesta registrada!',
    watch_answer: 'Mira la pantalla para ver la respuesta.',
    too_late: 'Demasiado tarde',
    too_late_detail: 'Se acabó el tiempo antes de que llegara tu respuesta.',
    not_sent: 'No enviada',
    refused: 'No se ha aceptado esa respuesta.',
    sending: 'Enviando…',
    retrying: 'Enviando… (reintentando)',
    keep_open: 'Deja esta página abierta.',
    correct: '¡Correcto!',
    not_this_time: 'Esta vez no',
    no_answer: 'Sin respuesta',
    right_one: 'La respuesta correcta',
    right_many: 'Las respuestas correctas',
    gained: (n) => `+${n} puntos`,
    points: (n) => `${n} puntos`,
    streak: (n) => `racha de ${n}`,
    score: 'Puntuación',
    final_score: 'Puntuación final',
    leaderboard: 'Clasificación',
    game_over: 'Fin del juego',
    wait_podium: 'Espera al podio…',
    removed: 'Fuera del juego',
    removed_detail: 'Te han sacado de este juego.',
  },
};
let lang = 'en';
let T = TEXT.en;
// Kahoot allows 120-character questions and 75-character answers: the longer the text, the
// smaller the step, so a 320 px phone shows every word (the tiles grow; nothing is clipped).
const answerSize = (answers) => { const n = Math.max(0, ...answers.map((a) => a.length)); return n > 45 ? 's' : n > 20 ? 'm' : 'l'; };
const questionSize = (q) => (q.length > 80 ? 's' : q.length > 40 ? 'm' : 'l');

const $ = (sel) => document.querySelector(sel);
const main = $('.play');
const params = new URLSearchParams(location.search);
const urlPin = (params.get('pin') || '').replace(/\D/g, '');
const forcePoll = params.get('transport') === 'poll';

let me = load(); // {player_id, secret, pin, name, game_id}
let msg = null; // the last view message
let offset = 0; // server clock − Date.now()
let pending = null; // {item_id, choice, state: 'sending' | ack state, message?}
const shownAt = {}; // item_id → performance.now() when its tiles appeared on this phone
let tick = null;

// ------------------------------------------------------------------ storage

function load() {
  try { return JSON.parse(localStorage.getItem(STORE) || 'null'); } catch { return null; }
}
function save(v) {
  me = v;
  try { v ? localStorage.setItem(STORE, JSON.stringify(v)) : localStorage.removeItem(STORE); } catch { /* private mode: this tab only */ }
}

// -------------------------------------------------------------------- http

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function post(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store' });
  let data = null;
  try { data = await r.json(); } catch { /* an empty or HTML body: a proxy error */ }
  return { status: r.status, ok: r.ok, data };
}

function clock(data) {
  if (data && typeof data.now_ms === 'number') offset = data.now_ms - Date.now();
}

// -------------------------------------------------------------- connection

let ws = null;
let wsOpen = false;
let wsRetry = 0;
let wsTimer = null;
let drops = [];
let pollTimer = null;
let online = null;

function setConn() {
  const el = $('#conn');
  const polling = pollTimer !== null;
  let state = online === false ? 'offline' : polling ? 'polling' : wsOpen ? 'online' : 'checking';
  if (!me && online) state = 'online'; // not joined yet: the ping answered, that is all there is
  el.dataset.state = state;
  el.textContent = {
    offline: me ? T.conn_reconnecting : T.conn_offline, polling: T.conn_connected,
    online: me ? T.conn_live : T.conn_online, checking: T.conn_checking,
  }[state];
  document.body.dataset.transport = wsOpen && !polling ? 'ws' : polling ? 'poll' : 'none';
}

function flaky() {
  const now = Date.now();
  drops = drops.filter((t) => now - t < FLAKY_WINDOW_MS);
  return drops.length >= 2;
}

function updatePolling() {
  const need = me && (forcePoll || !wsOpen || flaky());
  if (need && pollTimer === null) {
    pollTimer = setInterval(poll, POLL_MS);
    poll();
  } else if (!need && pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  setConn();
}

async function poll() {
  if (!me) return updatePolling();
  try {
    const r = await fetch(`/play/api/state?player_id=${encodeURIComponent(me.player_id)}&secret=${encodeURIComponent(me.secret)}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    online = true;
    onView(await r.json());
  } catch {
    online = false;
  }
  updatePolling();
}

function hello() {
  if (ws && wsOpen && me) ws.send(JSON.stringify({ op: 'hello', player_id: me.player_id, secret: me.secret }));
}

function connectWs() {
  clearTimeout(wsTimer);
  if (forcePoll || !me || ws) return;
  const sock = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/play/ws`);
  ws = sock;
  let opened = false;
  const giveUp = setTimeout(() => { if (!opened) sock.close(); }, 5000);
  sock.onopen = () => {
    opened = true;
    clearTimeout(giveUp);
    wsOpen = true;
    online = true;
    wsRetry = 0;
    hello();
    updatePolling();
  };
  sock.onmessage = (ev) => {
    let m = null;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'view') onView(m);
  };
  sock.onclose = () => {
    clearTimeout(giveUp);
    if (ws !== sock) return;
    ws = null;
    wsOpen = false;
    drops.push(Date.now());
    updatePolling();
    wsTimer = setTimeout(connectWs, Math.min(15000, 1000 * 2 ** wsRetry++));
  };
}

function startTransport() {
  updatePolling();
  if (ws && wsOpen) hello();
  else connectWs();
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' || !me) return;
  poll(); // an unlocked phone catches up at once
  if (!ws) connectWs();
});

// ------------------------------------------------------------------ join

function showJoin(error) {
  main.dataset.screen = 'join';
  const pin = $('#pin');
  if (!pin.value && (urlPin || (me && me.pin))) pin.value = urlPin || me.pin;
  const box = $('#joinError');
  box.hidden = !error;
  box.textContent = error || '';
  (pin.value ? $('#nickname') : pin).focus({ preventScroll: true });
}

async function join(ev) {
  ev.preventDefault();
  const pin = $('#pin').value.replace(/\D/g, '');
  const nickname = $('#nickname').value.trim();
  if (pin.length !== 6) return showJoin(T.pin_digits);
  if (!nickname) return showJoin(T.pick_nickname);
  const btn = $('#joinBtn');
  btn.disabled = true;
  btn.textContent = T.joining;
  const key = crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2); // the same key on every retry
  try {
    for (let attempt = 0; ; attempt++) {
      let r = null;
      try { r = await post('/play/api/join', { pin, nickname, key }); } catch { r = null; }
      if (r === null || r.status >= 500) {
        if (attempt >= 5) return showJoin(T.unreachable);
        await sleep(Math.min(4000, 500 * 2 ** attempt));
        continue;
      }
      clock(r.data);
      if (r.status === 429) return showJoin(T.too_many);
      if (!r.ok) return showJoin(r.data?.error?.code === 'bad_nickname' ? T.pick_nickname : T.failed);
      const d = r.data;
      if (d.state !== 'joined') return showJoin({ wrong_pin: T.wrong_pin, no_game: T.no_game, closed: T.closed }[d.state] || T.failed);
      save({ player_id: d.player_id, secret: d.secret, pin, name: d.name, game_id: d.game_id });
      if (d.view) onView(d.view);
      startTransport();
      return;
    }
  } finally {
    btn.disabled = false;
    btn.textContent = T.join;
  }
}

async function resume() {
  for (let attempt = 0; ; attempt++) {
    let r = null;
    try { r = await post('/play/api/resume', { player_id: me.player_id, secret: me.secret }); } catch { r = null; }
    if (r && r.ok) {
      clock(r.data);
      const d = r.data;
      if (d.state === 'resumed') {
        if (d.view) onView(d.view);
        return startTransport();
      }
      if (d.state === 'kicked') return render('kicked');
      if (d.state === 'no_game') { showWait(T.no_quiz); return startTransport(); }
      save(null); // unknown: this game is gone (or it was another game) — join again
      return showJoin();
    }
    online = false;
    setConn();
    await sleep(Math.min(5000, 500 * 2 ** attempt)); // offline or the server is restarting: keep the identity, retry
  }
}

// ------------------------------------------------------------------ views

function showWait(text) {
  $('[data-name]').textContent = me ? me.name : '';
  $('[data-wait-text]').textContent = text;
  main.dataset.screen = 'wait';
}

function render(screen) {
  main.dataset.screen = screen;
}

function onView(m) {
  clock(m);
  if (m.state === 'unknown_player') {
    save(null);
    stopTick();
    return showJoin(T.gone);
  }
  if (m.state === 'no_game') return showWait(T.no_quiz);
  if (m.view && m.view.lang) setLang(m.view.lang);
  msg = m;
  draw();
}

function shape(n) {
  return `<svg aria-hidden="true"><use href="#s-${n}"/></svg>`;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

// The answer text of tile n ('' when the server sent none).
function answerText(v, n) {
  const i = Array.isArray(v.answers) ? v.tiles.indexOf(n) : -1;
  return i >= 0 ? v.answers[i] || '' : '';
}

function draw() {
  const v = msg && msg.view;
  if (!v) return;
  if (v.kicked) { stopTick(); return render('kicked'); }
  const question = v.item_id && Array.isArray(v.tiles);
  if (pending && pending.item_id !== v.item_id && pending.state !== 'sending') pending = null;

  if (v.phase === 'question' && question && v.open) {
    const mine = v.answer || (pending && pending.item_id === v.item_id && pending.state !== 'not_open' ? pending : null);
    if (mine) return drawLocked(v, mine);
    return drawTiles(v);
  }
  stopTick();
  if ((v.phase === 'question' || v.phase === 'reveal') && question) return drawResult(v);
  if (v.phase === 'leaderboard' || v.phase === 'podium') return drawStanding(v);
  showWait(v.phase === 'lobby' ? T.in_lobby : T.watch);
}

function drawTiles(v) {
  const box = $('[data-tiles]');
  const texts = v.tiles.map((n) => answerText(v, n));
  const sig = JSON.stringify([v.item_id, v.tiles, texts, lang]);
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    box.dataset.size = answerSize(texts);
    box.innerHTML = v.tiles.map((n, i) =>
      `<button type="button" class="tile" data-choice="${n}" aria-label="${esc(T.tile(n, T.shape[n], texts[i]))}">${shape(n)}`
      + `${texts[i] ? `<span class="tile-text">${esc(texts[i])}</span>` : ''}</button>`).join('');
    box.querySelectorAll('.tile').forEach((b) => b.addEventListener('click', () => answer(v.item_id, Number(b.dataset.choice))));
  }
  const qtext = $('[data-qtext]');
  qtext.textContent = v.question || '';
  qtext.dataset.size = questionSize(qtext.textContent);
  qtext.hidden = !v.question;
  box.querySelectorAll('.tile').forEach((b) => { b.disabled = false; });
  $('[data-qindex]').textContent = v.question_index != null ? T.question(v.question_index + 1, v.question_count) : T.question_plain;
  render('question');
  if (!(v.item_id in shownAt)) shownAt[v.item_id] = performance.now();
  startTick(v);
}

function drawLocked(v, mine) {
  const choice = mine.choice;
  const badge = $('[data-locked-shape]');
  badge.dataset.choice = choice;
  badge.innerHTML = shape(choice);
  const st = $('[data-answer-status]');
  const acked = v.answer || mine.state === 'accepted' || mine.state === 'duplicate';
  if (acked) {
    st.dataset.kind = 'ok';
    st.innerHTML = `<svg class="icon" aria-hidden="true"><use href="#i-check"/></svg> ${esc(T.locked)}`;
    $('[data-locked-text]').textContent = T.watch_answer;
  } else if (mine.state === 'too_late') {
    st.dataset.kind = 'bad';
    st.textContent = T.too_late;
    $('[data-locked-text]').textContent = T.too_late_detail;
  } else if (mine.state === 'error') {
    st.dataset.kind = 'bad';
    st.textContent = T.not_sent;
    $('[data-locked-text]').textContent = T.refused; // the server's reason is in English: the phone says its own
  } else {
    st.dataset.kind = 'wait';
    st.textContent = mine.retrying ? T.retrying : T.sending;
    $('[data-locked-text]').textContent = T.keep_open;
  }
  render('locked');
  startTick(v);
}

function drawResult(v) {
  const title = $('[data-result]');
  const detail = $('[data-result-detail]');
  const a = v.answer;
  if (a && a.correct === true) {
    title.dataset.kind = 'ok';
    title.innerHTML = `<svg class="icon" aria-hidden="true"><use href="#i-check"/></svg> ${esc(T.correct)}`;
    detail.textContent = [v.last_points != null ? T.gained(v.last_points) : '', standing(v)].filter(Boolean).join(' · ');
  } else if (a && a.correct === false) {
    title.dataset.kind = 'bad';
    title.textContent = T.not_this_time;
    detail.textContent = standing(v);
  } else if (a) {
    title.dataset.kind = '';
    title.textContent = T.times_up;
    detail.textContent = T.watch_answer;
  } else {
    const late = pending && pending.item_id === v.item_id && pending.state === 'too_late';
    title.dataset.kind = 'bad';
    title.textContent = late ? T.too_late : T.no_answer;
    detail.textContent = standing(v);
  }
  drawRight(v);
  render('result');
}

// The right answer(s) under the result — shape and text, so a phone alone tells the whole story.
function drawRight(v) {
  const box = $('[data-right]');
  const nums = Array.isArray(v.correct) && Array.isArray(v.tiles) ? v.correct.filter((n) => v.tiles.includes(n)) : [];
  box.hidden = nums.length === 0;
  box.innerHTML = nums.length === 0 ? '' : `<p class="play-right-label">${esc(nums.length > 1 ? T.right_many : T.right_one)}</p>`
    + nums.map((n) => `<p class="play-right-item" data-choice="${n}"><span class="play-badge play-badge-sm" data-choice="${n}">${shape(n)}</span>`
      + `<span class="play-right-text">${esc(answerText(v, n) || T.tile(n, T.shape[n], ''))}</span></p>`).join('');
}

function drawStanding(v) {
  const title = $('[data-result]');
  title.dataset.kind = '';
  const final = v.phase === 'podium';
  // A place the stage has not revealed yet never reaches the phone (#147): it waits for the podium.
  title.innerHTML = v.rank ? `${v.rank === 1 ? '<svg class="icon" aria-hidden="true"><use href="#i-crown"/></svg> ' : ''}#${v.rank}`
    : esc(v.rank_pending ? T.wait_podium : final ? T.game_over : T.leaderboard);
  $('[data-result-detail]').textContent = `${final ? T.final_score : T.score}: ${T.points(fmt(v.score || 0))}${v.streak > 1 ? ` · ${T.streak(v.streak)}` : ''}`;
  drawRight({});
  render('result');
}

function standing(v) {
  return v.rank ? `#${v.rank} · ${T.points(fmt(v.score || 0))}` : '';
}

const fmt = (n) => Number(n).toLocaleString(lang);

// ------------------------------------------------------------------ timer

function startTick(v) {
  const left = () => Math.max(0, Math.ceil((v.deadline_ms - (Date.now() + offset)) / 1000));
  const draw1 = () => {
    const s = left();
    $('[data-left]').textContent = s > 0 ? T.seconds(s) : T.times_up;
    $('[data-timer]').toggleAttribute('data-low', s <= 5);
  };
  stopTick();
  if (!v.deadline_ms) return;
  draw1();
  tick = setInterval(draw1, 250);
}

function stopTick() {
  if (tick !== null) clearInterval(tick);
  tick = null;
}

// ------------------------------------------------------------------ answer

async function answer(itemId, choice) {
  if (!me || (pending && pending.item_id === itemId)) return; // one answer per question
  document.querySelectorAll('.tile').forEach((b) => { b.disabled = true; });
  const elapsed = Math.max(0, Math.round(performance.now() - (shownAt[itemId] ?? performance.now())));
  const mine = { item_id: itemId, choice, state: 'sending' };
  pending = mine;
  draw();
  const body = { player_id: me.player_id, secret: me.secret, item_id: itemId, choice, elapsed_ms: elapsed };
  const redraw = () => { if (pending === mine) draw(); }; // a later question may have taken over
  for (let attempt = 0; ; attempt++) {
    let r = null;
    try { r = await post('/play/api/answer', body); } catch { r = null; }
    const paused = r && r.ok && r.data.state === 'no_game'; // the server restarted and is not live yet: keep trying
    if (r && r.ok && !paused) {
      clock(r.data);
      online = true;
      Object.assign(mine, { choice: r.data.choice || choice, state: r.data.state, retrying: false });
      if (r.data.state === 'kicked') return render('kicked');
      if (r.data.state === 'unknown_player') { save(null); return showJoin(T.gone); }
      if (r.data.state === 'not_open' && pending === mine) { pending = null; return draw(); } // not asked yet: wait
      return redraw();
    }
    if (r && r.status === 422) {
      mine.state = 'error';
      return redraw();
    }
    // offline, a proxy error, 429 or a restart (no_game until it is live again): the same answer until acked
    mine.retrying = true;
    redraw();
    const wait = r && r.status === 429 ? (r.data?.error?.detail?.retry_after_s || 1) * 1000 : 0;
    await sleep(Math.max(wait, Math.min(3000, 300 * 2 ** attempt)));
  }
}

// ------------------------------------------------------------------ theme

$('[data-theme-toggle]').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(THEME, next); } catch { /* this tab only */ }
});

// ------------------------------------------------------------------ language

// Say everything in `l` (en | es; English when unknown): the page's own words now, the drawn
// screens on their next draw (onView sets the language before it draws).
function setLang(l) {
  const next = TEXT[l] ? l : 'en';
  if (next === lang) return;
  lang = next;
  T = TEXT[lang];
  document.documentElement.lang = lang;
  document.title = T.title;
  document.querySelectorAll('[data-t]').forEach((el) => { el.textContent = T[el.dataset.t]; });
  document.querySelectorAll('[data-t-placeholder]').forEach((el) => { el.placeholder = T[el.dataset.tPlaceholder]; });
  document.querySelectorAll('[data-t-label]').forEach((el) => { el.setAttribute('aria-label', T[el.dataset.tLabel]); });
  setConn();
}

// ------------------------------------------------------------------ start

$('#joinForm').addEventListener('submit', join);
$('#pin').addEventListener('input', (e) => { e.target.value = e.target.value.replace(/[^\d ]/g, ''); });

const pinged = fetch('/play/api/ping', { cache: 'no-store' })
  .then(async (r) => {
    online = r.ok;
    const d = r.ok ? await r.json().catch(() => null) : null;
    if (d && d.lang) setLang(d.lang); // before joining: the language of the game on stage
  })
  .catch(() => { online = false; })
  .finally(setConn);

// The first screen waits for the ping (a moment at most), so it opens in the game's language.
Promise.race([pinged, sleep(1500)]).then(() => {
  if (me && (!urlPin || urlPin === me.pin)) resume();
  else {
    if (me) save(null); // a new game's PIN: the old identity belongs to another game
    showJoin();
  }
});
