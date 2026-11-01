// /presenter — the Copilot card under the notes, and its header chip. presenter.js draws the card on
// every snapshot; the first draw into a fresh shell builds it. Everything comes from the snapshot's `copilot`;
// the switch sends the `copilot_toggle` intent. The hints always show here in full: in the
// overlay's cue mode this card is where the flashing icon sends the eye.

import { setSwitch } from '/static/_vendored/switch/switch.js';
import { api, esc, toast, switchRow } from '/static/js/ui.js';

const TYPE_LABEL = { next_q: 'Ask', notice: 'Notice', drift: 'Time', recap: 'Recap' };
const CHANNEL_STATE = { starting: 'accent', error: 'bad' };

/** Hints still worth showing: fresh (server time) and asked for the item on stage. */
function liveHints(c, itemId, now) {
  if (!c || !c.on || !c.hints || !c.hints.length || !c.hints_at) return [];
  if (now - c.hints_at > c.ttl_s * 1000 || c.hints_item_id !== itemId) return [];
  return c.hints;
}

/** Wire the card's two switches; `live` is the cockpit's connection. */
function buildCopilot(body, live) {
  body.innerHTML = '<div class="p-cop-row" data-cop-switches></div><div class="p-cop-status" data-cop-status></div>' +
    '<ol class="p-hints" data-cop-hints></ol><p class="small muted p-cop-idle" data-cop-idle></p>';
  const row = body.querySelector('[data-cop-switches]');
  const on = switchRow(false, 'Copilot', () => live.send('copilot_toggle'), 'Copilot on or off');
  on.querySelector('[role=switch]').dataset.copOn = '';
  on.querySelector('[role=switch]').dataset.track = 'live.copilot-toggle';
  const rec = document.createElement('span');
  rec.className = 'p-rec';
  rec.dataset.copRec = '';
  rec.hidden = true;
  rec.innerHTML = '<span class="p-rec-dot" aria-hidden="true"></span>Recording';
  const local = switchRow(false, 'Local only', async (next, btn) => {
    try {
      await api('/api/copilot/local-only', { method: 'PUT', body: { local_only: next } });
    } catch (e) {
      setSwitch(btn, !next);
      toast(e.message, 'error');
    }
  }, 'Hints from the local model only');
  local.querySelector('[role=switch]').dataset.copLocal = '';
  local.querySelector('[role=switch]').dataset.track = 'live.copilot-local-only';
  row.append(on, rec, local);
}

/** The card for this snapshot: switches, what is recorded, each failure on its own line, the hints. */
export function drawCopilot(card, live, cur) {
  const s = live.state;
  const c = s && s.copilot;
  if (!card || !c) return;
  const body = card.querySelector('[data-body]');
  if (!body.querySelector('[data-cop-switches]')) buildCopilot(body, live);
  const now = live.now();
  setSwitch(card.querySelector('[data-cop-on]'), !!c.on);
  setSwitch(card.querySelector('[data-cop-local]'), !!c.local_only);
  const recording = c.on && c.channels.some((ch) => ch.state === 'recording');
  const rec = card.querySelector('[data-cop-rec]');
  rec.hidden = !recording;
  rec.lastChild.textContent = `Recording · ${c.channels.filter((ch) => ch.state === 'recording').map((ch) => ch.label.toLowerCase()).join(' + ')}`;
  card.querySelector('.p-meta').textContent = !c.on ? 'off' : c.local_only ? 'local model only' : (c.hints_model || 'listening');

  const status = [];
  if (c.on) {
    c.channels.filter((ch) => ch.state !== 'recording').forEach((ch) =>
      status.push([CHANNEL_STATE[ch.state] || 'bad', ch.state === 'starting' ? `${ch.label} · starting` : `${ch.label} · not recording`, ch.detail]));
    if (c.behind_s >= c.behind_warn_s) status.push(['warn', `Transcript ${c.behind_s} s behind`, 'Chunks are waiting for the hub to transcribe them']);
    c.problems.forEach((p) => status.push(['bad', p.message.split(' — ')[0], p.message]));
  }
  const html = status.map(([k, t, title]) => `<span class="chip ${k}" title="${esc(title || '')}"><span class="dot"></span>${esc(t)}</span>`).join('');
  const box = card.querySelector('[data-cop-status]');
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }

  const hints = liveHints(c, cur ? cur.id : null, now);
  const list = card.querySelector('[data-cop-hints]');
  const items = hints.map((h) => `<li class="p-hint"><span class="p-hint-type">${esc(TYPE_LABEL[h.type] || h.type)}</span>` +
    `<span class="p-hint-text">${esc(h.text)}</span></li>`).join('');
  if (list.dataset.html !== items) { list.dataset.html = items; list.innerHTML = items; }
  card.querySelector('[data-cop-idle]').textContent = !c.on
    ? 'Records your mic and the call, and shows short hints here and on the overlay. Tell the participants before you switch it on.'
    : hints.length ? '' : `Listening — ${c.stats.lines} line${c.stats.lines === 1 ? '' : 's'} so far.`;
  card.classList.toggle('has-hints', hints.length > 0);
}

/** The header chip while Copilot is on: a chosen mode in accent, a failure as what it is (null while off). */
export function copilotChip(s) {
  const c = s.copilot;
  if (!c || !c.on) return null;
  if (c.problems.length) return ['bad', `Copilot · ${c.problems[0].message.split(' — ')[0].toLowerCase()}`, null, c.problems[0].message];
  if (c.channels.some((ch) => ch.state === 'error')) return ['bad', 'Copilot · a channel is not recording'];
  if (c.behind_s >= c.behind_warn_s) return ['warn', `Copilot · ${c.behind_s} s behind`];
  return ['accent', `Copilot · ${c.channels.length ? 'recording' : 'on'}`, null, c.local_only ? 'Hints from the local model only' : ''];
}
