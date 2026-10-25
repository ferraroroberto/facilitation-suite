// /presenter — the quiz panel on a quiz item's card. presenter.js calls buildQuiz / drawQuiz /
// tickQuiz from the item card and hands over the live connection and its two shared helpers.

import { icon } from '/static/_vendored/icons/icons.js';
import { api, esc, toast } from '/static/js/ui.js';
import { confirmDialog } from '/static/js/dialogs.js';
import { clock } from '/static/js/stage-render.js';

// The host's side of a quiz: phase, time left, players, the join PIN and
// link, and the controls. "Next phase" is the plain `next` (the server steps a
// question → reveal → leaderboard before it moves on — the last one before a podium
// skips its leaderboard); Space locks an open question.
const QUIZ_TYPES = new Set(['quiz_lobby', 'quiz', 'quiz_podium']);
export const isQuiz = (it) => !!(it && it.kind === 'activity' && QUIZ_TYPES.has(it.type));

/** Build the panel's markup and wire its controls: `host` is the cockpit's `live` connection, `copyText` and `countOwnSwitch`. */
export function buildQuiz(body, { live, copyText, countOwnSwitch }) {
  body.insertAdjacentHTML('beforeend',
    `<div class="p-cap-status"><span class="p-cap-dot" data-qdot></span><span class="grow" data-qphase></span><span class="p-quiz-left" data-qleft></span></div>` +
    // Tighter: the counts share a row with the Sounds chip, the PIN with its link.
    `<div class="p-quiz-join"><p class="small muted p-cap-counts" data-qcounts></p><span class="p-quiz-part" data-qsound></span></div>` +
    `<div class="p-quiz-join" data-qjoin></div>` +
    `<div class="p-timer-actions p-quiz-actions">` +
    `<button type="button" class="button-primary p-cap-btn" data-qnext data-track="live.quiz-next"></button>` +
    `<button type="button" class="button-surface" data-qlock data-track="live.quiz-lock">${icon('square')} Lock answers<kbd>Space</kbd></button>` +
    `<button type="button" class="button-surface" data-qnew data-track="live.quiz-new">${icon('rotate-ccw')} New game</button></div>` +
    `<p class="overline p-quiz-head">${icon('users')} Players</p><ul class="p-quiz-players" data-qplayers></ul>`);
  body.querySelector('[data-qnext]').addEventListener('click', () => live.send('next'));
  body.querySelector('[data-qlock]').addEventListener('click', () => live.send('quiz_lock'));
  body.querySelector('[data-qnew]').addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: 'Start a new game?',
      message: 'The quiz starts over from this lobby with no players. The game played so far is kept, and its results stay in the Results tab.',
      actionLabel: 'New game',
    });
    if (ok) live.send('quiz_new_game');
  });
  body.querySelector('[data-qplayers]').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-kick]');
    if (!b) return;
    const ok = await confirmDialog({
      title: `Remove ${b.dataset.name}?`,
      message: 'They leave the game and the leaderboard, and their phone says so. This cannot be undone.',
      actionLabel: 'Remove', danger: true,
    });
    if (ok) live.send('quiz_kick', b.dataset.kick);
  });
  body.querySelector('[data-qjoin]').addEventListener('click', (e) => {
    const b = e.target.closest('[data-qcopy]');
    if (b) copyText(b.dataset.qcopy, 'Join link copied');
    const check = e.target.closest('[data-qreachcheck]');
    if (check) {
      check.disabled = true; // the result arrives with the next state push; the button is redrawn then
      api('/api/quiz/reach', { method: 'POST' }).catch((err) => toast(err.message, 'error')).finally(() => { check.disabled = false; });
    }
  });
  const row = countOwnSwitch(!!(live.state && live.state.count_own), 'Count my own chat answers (rehearsal)');
  row.dataset.qown = '';
  body.appendChild(row);
}

function quizPhaseText(q) {
  if (!q) return 'Not in a game — put a quiz lobby before it';
  if (q.error) return `Cannot be played: ${q.error}`;
  const n = q.question_index != null ? q.question_index + 1 : null;
  return {
    lobby: 'Lobby — players are joining',
    question: `Question ${n} of ${q.question_count} — answers open`,
    reveal: `Question ${n} of ${q.question_count} — answers locked`,
    leaderboard: `Leaderboard after question ${n} of ${q.question_count}`,
    podium: quizPodiumText(q),
  }[q.phase] || 'Waiting';
}

// The podium reveals one place per Next: 3rd, 2nd, then 1st (fewer with fewer players).
const PLACE = ['1st', '2nd', '3rd'];
const podiumLeft = (q) => (q.podium_places || 0) - (q.podium_step || 0); // places still hidden

function quizPodiumText(q) {
  if (!q.podium_places) return 'Podium — no players';
  if (!q.podium_step) return 'Podium — no place shown yet';
  return `Podium — ${PLACE[podiumLeft(q)]} shown`;
}

function quizNextLabel(q) {
  if (!q || !q.phase) return 'Next';
  const last = q.question_index != null && q.question_index + 1 >= q.question_count;
  if (q.phase === 'lobby') return 'Start the first question';
  if (q.phase === 'question') return 'Reveal the answer';
  // The last question before a podium goes straight to it: no leaderboard to spoil it.
  if (q.phase === 'reveal') return last && q.podium_follows ? 'On to the podium' : 'Show the leaderboard';
  if (q.phase === 'leaderboard') return !last ? 'Next question' : q.podium_follows ? 'On to the podium' : 'Next';
  if (q.phase === 'podium' && podiumLeft(q) > 0) return `Show ${PLACE[podiumLeft(q) - 1]}`;
  return 'Next';
}

function quizJoinHtml(q) {
  // PIN and link come with the player page; until then — or while quiz.public_url
  // is empty — the card says so rather than showing nothing.
  const pin = q && q.pin ? `${q.pin.slice(0, 3)} ${q.pin.slice(3)}` : '';
  if (!pin) return `<span class="chip">Join · not configured</span><span class="small muted">No join PIN for this game yet.</span>`;
  const url = q.join_url || '';
  if (!url) return `<span class="p-quiz-pin">PIN <b>${esc(pin)}</b></span><span class="chip warn">Public link · not configured</span>` +
    `<span class="small muted">Set quiz.public_url in the config.</span>`;
  return `<span class="p-quiz-pin">PIN <b>${esc(pin)}</b></span>` +
    `<span class="p-quiz-url" title="${esc(url)}">${esc(url)}</span>` +
    `<button type="button" class="button-surface" data-qcopy="${esc(url)}">${icon('copy')} Copy link</button>` +
    quizReachHtml(q.reach);
}

// The public-link check (src/quiz/reach.py), the same six states as the readiness list:
// ok | listener_down | tunnel_down | public_unreachable | not_configured | unknown — unknown is never shown as ok.
const REACH_CHIP = { ok: 'ok', listener_down: 'warn', tunnel_down: 'warn', public_unreachable: 'warn', not_configured: 'warn', unknown: '' };

function quizReachHtml(reach) {
  const r = reach || { state: 'unknown', label: 'unknown', detail: 'Not checked yet' };
  const when = r.checked_at ? ` · checked ${new Date(r.checked_at * 1000).toLocaleTimeString()}` : '';
  return `<span class="chip ${REACH_CHIP[r.state] ?? ''}" data-qreach="${esc(r.state)}" title="${esc(r.detail + when)}">Reachability · ${esc(r.label)}</span>` +
    `<button type="button" class="button-surface" data-qreachcheck data-track="live.quiz-reach-check">${icon('refresh-cw')} Check</button>`;
}

// The Sounds chip: which of the four cue files (src/quiz/cues.py) are in audio/, and
// whether this PC has an audio output device at all — "unknown" (the check could not run) is
// never shown as ok, and no device is its own distinct message, not folded into the count.
function quizSoundHtml(s) {
  if (!s) return '';
  if (s.device === 'none') return `<span class="chip bad" title="${esc(s.device_detail || '')}">No audio output device</span>`;
  if (s.device === 'unknown') return `<span class="chip" title="${esc(s.device_detail || 'Could not check for an audio output device')}">Sounds · unknown</span>`;
  const title = s.missing.length ? `Missing: ${s.missing.join(', ')}` : `All ${s.total} quiz cues are in audio/`;
  return `<span class="chip ${s.have === s.total ? 'ok' : 'warn'}" title="${esc(title)}">Sounds · ${s.have}/${s.total}</span>`;
}

export function drawQuiz(body, q, live) {
  const phase = q && !q.error ? q.phase : null;
  body.querySelector('[data-qdot]').className = `p-cap-dot ${phase === 'question' ? 'live' : phase ? 'stopped' : 'error'}`;
  body.querySelector('[data-qphase]').textContent = quizPhaseText(q);
  const answering = phase === 'question' || phase === 'reveal';
  body.querySelector('[data-qcounts]').textContent = q && !q.error
    ? `${q.player_count} player${q.player_count === 1 ? '' : 's'} joined${answering ? ` · ${q.answered_count} answered` : ''}` +
      (q.accept_chat ? ' · chat answers on' : ' · chat answers off')
    : '';
  const sound = body.querySelector('[data-qsound]');
  const soundHtml = quizSoundHtml(q && q.sounds);
  if (sound.dataset.html !== soundHtml) { sound.dataset.html = soundHtml; sound.innerHTML = soundHtml; }
  const join = body.querySelector('[data-qjoin]');
  const joinHtml = q && !q.error ? quizJoinHtml(q) : '';
  if (join.dataset.html !== joinHtml) { join.dataset.html = joinHtml; join.innerHTML = joinHtml; }
  const next = body.querySelector('[data-qnext]');
  const nextHtml = `${icon('skip-forward')} ${esc(quizNextLabel(q))}<kbd>→</kbd>`;
  if (next.dataset.html !== nextHtml) { next.dataset.html = nextHtml; next.innerHTML = nextHtml; }
  body.querySelector('[data-qlock]').disabled = phase !== 'question';
  body.querySelector('[data-qnew]').hidden = phase !== 'lobby';
  body.querySelector('[data-qown]').hidden = !(q && q.accept_chat);
  const players = (q && q.players) || [];
  const list = body.querySelector('[data-qplayers]');
  const sig = JSON.stringify(players);
  if (list.dataset.sig !== sig) {
    list.dataset.sig = sig;
    list.innerHTML = players.length ? players.map((p) =>
      `<li><span class="grow">${esc(p.name)}</span>${p.source === 'chat' ? '<span class="chip">chat</span>' : ''}` +
      `<button type="button" class="icon-button danger" data-kick="${esc(p.id)}" data-name="${esc(p.name)}" aria-label="Remove ${esc(p.name)}" title="Remove from the game">${icon('x')}</button></li>`).join('')
      : `<li class="muted small">${phase === 'lobby' ? 'Nobody has joined yet.' : 'No players.'}</li>`;
  }
  tickQuiz(body, q, live);
}

export function tickQuiz(body, q, live) {
  const left = body.querySelector('[data-qleft]');
  if (!left) return;
  const open = q && q.phase === 'question' && q.deadline_ms;
  left.textContent = open ? clock(Math.max(0, (q.deadline_ms - live.now()) / 1000)) : '';
}
