// /presenter — the Copilot card under the notes, and its header chip. presenter.js draws the card on
// every snapshot; the first draw into a fresh shell builds it. Everything comes from the snapshot's `copilot`
// (the panel under the switches, which says what feeds the hints and where they show, is presenter-copilot-panel.js);
// the switch sends the `copilot_toggle` intent. The hints always show here in full: in the
// overlay's cue mode this card is where the flashing icon sends the eye.

import { setSwitch } from '/static/_vendored/switch/switch.js';
import { api, esc, toast, switchRow } from '/static/js/ui.js';
import { confirmDialog } from '/static/js/dialogs.js';
import { buildPanel, drawPanel } from '/static/js/presenter-copilot-panel.js';

const TYPE_LABEL = { next_q: 'Ask', notice: 'Notice', drift: 'Time', recap: 'Recap' };

/** Hints still worth showing: fresh (server time) and asked for the item on stage. */
function liveHints(c, itemId, now) {
  if (!c || !c.on || !c.hints || !c.hints.length || !c.hints_at) return [];
  if (now - c.hints_at > c.ttl_s * 1000 || c.hints_item_id !== itemId) return [];
  return c.hints;
}

/** Copilot on or off. Switching on for a session whose consent is not recorded asks once first; the server
 *  refuses to record without it, so a Stream Deck press cannot skip the question. */
async function switchCopilot(live, next, btn) {
  const c = live.state && live.state.copilot;
  if (next && c && !c.consent) {
    const ok = await confirmDialog({
      title: 'Participants informed?',
      message: 'Copilot records your mic and the call. Confirm that the participants were told and agreed to be recorded. ' +
        'Staying in the call is not consent. The app records your confirmation in this session; it does not judge whether the consent was valid.',
      actionLabel: 'Confirm and switch on',
    });
    if (ok) {
      try {
        await api('/api/copilot/consent', { method: 'POST' });
      } catch (e) {
        toast(e.message, 'error');
        setSwitch(btn, false);
        return;
      }
    } else {
      setSwitch(btn, false);
      return;
    }
  }
  live.send('copilot_toggle');
}

/** Save the session's mode (and the interviewee's name when given); the snapshot that follows redraws the card. */
async function saveMode(body) {
  try {
    await api('/api/copilot/mode', { method: 'PUT', body });
    return true;
  } catch (e) {
    toast(e.message, 'error');
    return false;
  }
}

/** The interviewee's name, shown while Interview is on: saved on blur or Enter, put back on Escape. */
function nameField() {
  const input = document.createElement('input');
  input.className = 'input p-cop-name';
  input.type = 'text';
  input.maxLength = 80; // src.sessions.model.INTERVIEWEE_CHARS
  input.placeholder = "Interviewee's name";
  input.setAttribute('aria-label', "Interviewee's name");
  input.dataset.copName = '';
  input.dataset.track = 'live.copilot-interviewee';
  input.hidden = true;
  const save = () => {
    const name = input.value.trim().replace(/\s+/g, ' ');
    if (name !== (input.dataset.saved || '')) saveMode({ mode: 'interview', interviewee: name });
  };
  input.addEventListener('blur', save);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = input.dataset.saved || ''; input.blur(); }
  });
  return input;
}

/** Wire the card's switches and the interviewee's name; `live` is the cockpit's connection. */
function buildCopilot(body, live) {
  body.innerHTML = '<div class="p-cop-row" data-cop-switches></div><div class="p-cop-status" data-cop-status></div>' +
    '<ol class="p-hints" data-cop-hints></ol><p class="small muted p-cop-idle" data-cop-idle></p>';
  const row = body.querySelector('[data-cop-switches]');
  const on = switchRow(false, 'Copilot', (next, btn) => switchCopilot(live, next, btn), 'Copilot on or off');
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
  const name = nameField();
  const interview = switchRow(false, 'Interview', async (next, btn) => {
    if (!(await saveMode({ mode: next ? 'interview' : 'workshop' }))) {
      setSwitch(btn, !next);
    } else if (next && !name.dataset.saved) {
      name.hidden = false;
      name.focus(); // a new interview starts with the name
    }
  }, 'Interview mode: hints about the interviewee');
  interview.querySelector('[role=switch]').dataset.copInterview = '';
  interview.querySelector('[role=switch]').dataset.track = 'live.copilot-interview';
  row.append(on, rec, local, interview, name);
  buildPanel(body, live);
}

/** The card for this snapshot: switches, what is recorded, each failure on its own line, the hints. */
export function drawCopilot(card, live, cur, sid) {
  const s = live.state;
  const c = s && s.copilot;
  if (!card || !c) return;
  const body = card.querySelector('[data-body]');
  if (!body.querySelector('[data-cop-switches]')) buildCopilot(body, live);
  const now = live.now();
  setSwitch(card.querySelector('[data-cop-on]'), !!c.on);
  setSwitch(card.querySelector('[data-cop-local]'), !!c.local_only);
  const interview = c.mode === 'interview';
  setSwitch(card.querySelector('[data-cop-interview]'), interview);
  const name = card.querySelector('[data-cop-name]');
  name.hidden = !interview;
  name.dataset.saved = c.interviewee || '';
  if (document.activeElement !== name) name.value = c.interviewee || ''; // never under the facilitator's typing
  const recording = c.on && c.channels.some((ch) => ch.recording);
  const rec = card.querySelector('[data-cop-rec]');
  rec.hidden = !recording;
  rec.lastChild.textContent = `Recording · ${c.channels.filter((ch) => ch.recording).map((ch) => ch.label.toLowerCase()).join(' + ')}`;
  card.querySelector('.p-meta').textContent = !c.on ? 'off' : c.local_only ? 'local model only' : (c.hints_model || 'listening');

  const status = [];
  if (c.on) {
    c.channels.filter((ch) => !ch.recording).forEach((ch) =>
      status.push([ch.starting ? 'accent' : 'bad', ch.starting ? `${ch.label} · starting` : `${ch.label} · not recording`, ch.detail]));
    if (c.behind_s >= c.behind_warn_s) status.push(['warn', `Transcript ${c.behind_s} s behind`, 'Chunks are waiting for the hub to transcribe them']);
    c.problems.forEach((p) => status.push([p.level || 'bad', p.message.split(' — ')[0], p.message]));
  }
  const html = status.map(([k, t, title]) => `<span class="chip ${k}" title="${esc(title || '')}"><span class="dot"></span>${esc(t)}</span>`).join('');
  const box = card.querySelector('[data-cop-status]');
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }

  const hints = liveHints(c, cur ? cur.id : null, now);
  const list = card.querySelector('[data-cop-hints]');
  const items = hints.map((h) => `<li class="p-hint"><span class="p-hint-type">${h.test ? 'Test · ' : ''}${esc(TYPE_LABEL[h.type] || h.type)}</span>` +
    `<span class="p-hint-text">${esc(h.text)}</span></li>`).join('');
  if (list.dataset.html !== items) { list.dataset.html = items; list.innerHTML = items; }
  card.querySelector('[data-cop-idle]').textContent = !c.on
    ? 'Records your mic and the call, and shows short hints here and on the overlay. Tell the participants before you switch it on.'
    : hints.length ? '' : `Listening — ${c.stats.lines} line${c.stats.lines === 1 ? '' : 's'} so far.`;
  card.classList.toggle('has-hints', hints.length > 0);
  drawPanel(card.querySelector('[data-cop-panel]'), c, { cur, sid, now });
}

/** The header chip while Copilot is on: a chosen mode in accent, a failure as what it is (null while off). */
export function copilotChip(s) {
  const c = s.copilot;
  if (!c || !c.on) return null;
  const p = c.problems.find((x) => x.level !== 'warn') || c.problems[0]; // a failure outranks a cap held on purpose
  if (p) return [p.level || 'bad', `Copilot · ${p.message.split(' — ')[0].toLowerCase()}`, null, p.message];
  if (c.channels.some((ch) => ch.failed)) return ['bad', 'Copilot · a channel is not recording'];
  if (c.behind_s >= c.behind_warn_s) return ['warn', `Copilot · ${c.behind_s} s behind`];
  return ['accent', `Copilot · ${c.channels.length ? 'recording' : 'on'}`, null, c.local_only ? 'Hints from the local model only' : ''];
}
