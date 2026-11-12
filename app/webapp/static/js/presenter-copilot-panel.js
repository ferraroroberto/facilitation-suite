// /presenter — the Copilot card's panel. Collapsed it is one strip: each channel's live level and how the latest
// hint check ended, so the facilitator sees the pipeline is alive without the page growing. "Details" opens what
// feeds the hints (the item's notes, the playbook), where they show (the overlay's screen and corner), a sample
// and a test that runs one hint check on the facilitator's own mic. presenter-copilot.js draws it on every
// snapshot; while Copilot is on it also polls the copilot block once a second, because the levels move faster
// than the snapshots do.

import { api, esc, toast } from '/static/js/ui.js';
import { icon } from '/static/_vendored/icons/icons.js';
import { MODE_LABEL, POSITION_LABEL, screenName } from '/static/js/views/overlay-card.js';

const POLL_MS = 1000;
const NOTES_CHARS = 1500; // src.copilot.hints.NOTES_CHARS
const PLAYBOOK_CHARS = 1500; // src.copilot.hints.PLAYBOOK_CHARS
const SPEAKER_LABEL = { me: 'Your mic', them: 'The call' };
// A level meter spans a quiet room to a loud voice; below the floor is silence.
const METER_FLOOR_DB = -60;
const METER_SPAN_DB = 50;
const LEVEL_FRESH_S = 3;

const HELP = '<p><b>It reads</b> the notes of the item on stage and the next one, this session’s playbook and the live clocks, and it hears what is said.</p>' +
  '<p><b>It listens on two channels:</b> your mic (<i>me</i>) and the sound of the call (<i>them</i>). Both are recorded while it is on; the recording dot stays lit.</p>' +
  '<p><b>It speaks up</b> when the other side has said something new and at every item change, never more often than the configured interval. ' +
  'Hints appear here and on the overlay window; “nothing worth a hint” is a normal answer.</p>' +
  '<p><b>Alone?</b> Say a few sentences into your mic, then press Test copilot: it asks for one hint on what you said and labels it a test.</p>';

const ago = (ms, now) => {
  const s = Math.max(0, Math.round((now - ms) / 1000));
  return s < 90 ? `${s} s ago` : `${Math.round(s / 60)} min ago`;
};

/** The meter fill (0–1) for a channel's last block, 0 when the block is old or there is none. */
export function meterFill(level) {
  if (!level || level.age_s > LEVEL_FRESH_S) return 0;
  return Math.min(1, Math.max(0, (level.db - METER_FLOOR_DB) / METER_SPAN_DB));
}

/** How the latest hint check ended, in words; `c` is the snapshot's copilot block. */
export function checkText(c, now) {
  const k = c.last_check;
  if (!c.on) return 'Copilot is off';
  if (!k) return 'No hint yet: waiting for the other side to speak';
  const test = k.test ? 'Test: ' : '';
  const when = ago(k.at, now);
  return {
    shown: `${test}hint shown ${when}`,
    none: `${test}checked ${when}: nothing worth a hint`,
    stale: `${test}checked ${when}: dropped, the item changed`,
    failed: `${test}the check ${when} failed (see above)`,
  }[k.outcome] || `Checked ${when}`;
}

const stripChannel = (speaker) =>
  `<li class="p-cop-chan" data-chan="${speaker}"><span class="small">${SPEAKER_LABEL[speaker]}</span>` +
  '<span class="p-meter" aria-hidden="true"><i data-meter></i></span></li>';

/** Build the panel into the card body once; `live` is the cockpit's connection. */
export function buildPanel(body, live) {
  const panel = document.createElement('div');
  panel.className = 'p-cop-panel';
  panel.dataset.copPanel = '';
  panel.innerHTML =
    '<div class="p-cop-strip">' +
      `<ul class="p-cop-chans">${stripChannel('me')}${stripChannel('them')}</ul>` +
      '<span class="small p-cop-check" data-cop-check role="status"></span>' +
      `<button type="button" class="p-cop-more" data-cop-more aria-expanded="false" aria-controls="p-cop-detail" data-track="live.copilot-details">Details ${icon('chevron-down')}</button>` +
    '</div>' +
    '<div class="p-cop-detail" id="p-cop-detail" data-cop-detail hidden>' +
      '<section class="p-cop-sec"><h4 class="p-cop-h">Status</h4><ul class="p-cop-list">' +
        '<li class="small" data-chan-line="me"></li><li class="small" data-chan-line="them"></li></ul></section>' +
      '<section class="p-cop-sec"><h4 class="p-cop-h">What it reads</h4><ul class="p-cop-list">' +
        '<li class="p-cop-read small"><span class="grow" data-read-notes></span>' +
        `<a class="p-cop-link" data-read-notes-link href="#" target="fs-app" data-track="live.copilot-edit-notes">${icon('pencil')} Edit</a></li>` +
        '<li class="p-cop-read small"><span class="grow" data-read-playbook></span>' +
        `<button type="button" class="p-cop-link" data-read-playbook-open data-track="live.copilot-open-playbook">${icon('file-text')} Open</button>` +
        `<a class="p-cop-link" data-read-playbook-link href="#" target="fs-app" data-track="live.copilot-playbook-session">${icon('external-link')} Session</a></li>` +
      '</ul></section>' +
      '<section class="p-cop-sec"><h4 class="p-cop-h">Where hints show</h4>' +
        '<p class="small p-cop-where" data-cop-where></p>' +
        '<div class="p-cop-actions">' +
          `<button type="button" class="button-surface" data-cop-sample data-track="live.copilot-sample">${icon('eye')} Show a sample</button>` +
          `<a class="button-surface" href="/#settings/live" target="fs-app" data-track="live.copilot-overlay-settings">${icon('monitor')} Overlay settings</a>` +
          `<button type="button" class="button-tint" data-cop-test data-track="live.copilot-test">${icon('wand-sparkles')} Test copilot</button>` +
        '</div></section>' +
      `<details class="p-cop-help"><summary data-track="live.copilot-help">${icon('circle-help')} How the copilot works</summary><div class="p-cop-help-body small">${HELP}</div></details>` +
    '</div>';
  body.appendChild(panel);
  const more = panel.querySelector('[data-cop-more]');
  more.addEventListener('click', () => {
    const open = more.getAttribute('aria-expanded') !== 'true';
    more.setAttribute('aria-expanded', String(open));
    panel.querySelector('[data-cop-detail]').hidden = !open;
    panel.classList.toggle('open', open);
  });
  panel.querySelector('[data-cop-sample]').addEventListener('click', async () => {
    try {
      const d = await api('/api/copilot/sample', { method: 'POST' });
      if (!d.overlay.enabled) toast('The overlay is off — switch it on in Settings');
      else if (d.delivered) toast('A sample is on the overlay');
      else toast('The overlay is not running — see data/logs/facilitation-suite.log, then restart the app', 'error');
    } catch (e) {
      toast(e.message, 'error');
    }
  });
  panel.querySelector('[data-cop-test]').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await api('/api/copilot/test', { method: 'POST' });
      toast('Test running — the hint appears here and on the overlay, labelled Test');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = !panel.dataset.on;
    }
  });
  panel.querySelector('[data-read-playbook-open]').addEventListener('click', async () => {
    try {
      await api(`/api/sessions/${panel.dataset.sid}/open`, { method: 'POST', body: { what: 'playbook' } });
    } catch (err) {
      toast(err.message, 'error');
    }
  });
  panel.screens = [];
  api('/api/copilot/settings').then((d) => { panel.screens = d.screens; }).catch(() => {}); // names the overlay's screen
  startPoll(panel, live);
  return panel;
}

/** The meters, the per-channel lines and the check line, from one copilot block. */
function drawStatus(panel, c, now) {
  ['me', 'them'].forEach((speaker) => {
    const ch = (c.channels || []).find((x) => x.speaker === speaker);
    const fill = c.on && ch && ch.recording ? meterFill(ch.level) : 0;
    panel.querySelector(`[data-chan="${speaker}"] [data-meter]`).style.transform = `scaleX(${fill.toFixed(2)})`;
    const at = (c.last_chunk_at || {})[speaker];
    let text = 'off';
    if (c.on) {
      if (ch && !ch.recording) text = ch.starting ? 'starting' : 'not recording';
      else if (c.capture === 'off') text = 'no audio source (capture is off)';
      else text = at ? `last chunk transcribed ${ago(at, now)}` : 'no chunk transcribed yet';
    }
    panel.querySelector(`[data-chan-line="${speaker}"]`).textContent = `${SPEAKER_LABEL[speaker]} · ${text}`;
  });
  panel.querySelector('[data-cop-check]').textContent = checkText(c, now);
}

function describeNotes(cur) {
  if (!cur) return 'Notes · no item on stage';
  const n = String(cur.notes || '').trim().length;
  if (!n) return 'Notes · this item has none; the next item’s are read too';
  return `Notes · this item has ${n} characters${n > NOTES_CHARS ? ` (the first ${NOTES_CHARS} are read)` : ''}; the next item’s are read too`;
}

function describePlaybook(p) {
  if (!p || !p.exists) return 'Playbook · none yet — create one on the session';
  return `Playbook · ${p.chars} characters${p.chars > PLAYBOOK_CHARS ? ` (the first ${PLAYBOOK_CHARS} are read)` : ''}`;
}

function overlayText(o, screens) {
  if (!o.enabled) return 'The overlay window is off: hints show on this card only. Switch it on in Settings.';
  const known = screens.find((s) => s.id === o.screen);
  const screen = !o.screen ? 'Primary screen' : known ? screenName(known) : `${o.screen} (not connected)`;
  return `${screen} · ${POSITION_LABEL[o.position] || o.position} · ${MODE_LABEL[o.mode] || o.mode}${o.running ? '' : ' · opens with the first hint'}`;
}

/** Redraw the panel for this snapshot. `cur` is the item on stage, `sid` the live session's id. */
export function drawPanel(panel, c, { cur, sid, now }) {
  panel.dataset.on = c.on ? '1' : '';
  panel.dataset.sid = sid;
  panel.classList.toggle('is-on', !!c.on);
  drawStatus(panel, c, now);
  panel.querySelector('[data-read-notes]').textContent = describeNotes(cur);
  panel.querySelector('[data-read-playbook]').textContent = describePlaybook(c.playbook);
  const session = `/#sessions/${encodeURIComponent(sid)}`;
  panel.querySelector('[data-read-notes-link]').href = session;
  panel.querySelector('[data-read-playbook-link]').href = session;
  const hasPlaybook = !!(c.playbook && c.playbook.exists);
  panel.querySelector('[data-read-playbook-open]').hidden = !hasPlaybook;
  panel.querySelector('[data-read-playbook-link]').hidden = hasPlaybook;
  panel.querySelector('[data-cop-where]').innerHTML = esc(overlayText(c.overlay, panel.screens));
  panel.querySelector('[data-cop-test]').disabled = !c.on;
}

/** While Copilot is on, ask for the copilot block every second and redraw the status from it. */
function startPoll(panel, live) {
  let busy = false;
  const timer = setInterval(async () => {
    if (!panel.isConnected) { // the presenter rebuilt its shell: this panel is gone, so is its loop
      clearInterval(timer);
      return;
    }
    if (busy || !panel.dataset.on || document.hidden) return;
    busy = true;
    try {
      drawStatus(panel, await api('/api/copilot/state'), live.now());
    } catch (e) {
      /* the next snapshot draws it again */
    } finally {
      busy = false;
    }
  }, POLL_MS);
}
