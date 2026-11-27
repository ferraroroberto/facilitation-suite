// /presenter — a feedback form's backstage panel on its item card: open or close the form, its
// link to paste in the meeting chat, the CSV, and the results as they come in (the response
// count, NPS, star and scale averages, choice tallies, the free-text answers with names).
// The results are read from /api/forms/<item>/results, never from the live snapshot, so no answer
// ever rides the socket the stage listens on (src/forms/service.py).

import { icon } from '/static/_vendored/icons/icons.js';
import { startPoll } from '/static/_vendored/poll/poll.js';
import { api, esc, toast } from '/static/js/ui.js';

const POLL_MS = 3000; // a response or two a minute: every few seconds is live enough backstage

export const isForm = (it) => !!(it && it.kind === 'activity' && it.type === 'form');

/** Build the panel on `body` for the form item `cur` and keep its results fresh while it is on screen. */
export function buildForm(body, cur, { copyText }) {
  body.insertAdjacentHTML('beforeend',
    `<div class="p-form" data-surface="presenter.form">` +
    `<div class="p-cap-status"><span class="p-cap-dot" data-fdot></span><span class="grow" data-fstate></span></div>` +
    `<p class="small muted p-cap-counts" data-fcounts></p>` +
    `<div class="p-timer-actions">` +
    `<button type="button" class="button-primary p-cap-btn" data-ftoggle data-track="live.form-toggle"></button>` +
    `<button type="button" class="button-surface" data-fcopy data-track="live.form-copy">${icon('copy')} Copy link</button>` +
    `<a class="button-surface" data-fcsv data-track="live.form-csv" href="/api/forms/${encodeURIComponent(cur.id)}/csv">${icon('download')} CSV</a></div>` +
    `<div class="p-form-results" data-fresults></div></div>`);
  const panel = body.querySelector('.p-form');
  panel.dataset.item = cur.id;
  const read = async () => {
    if (!panel.isConnected) { poll.stop(); return; } // the card was rebuilt for another item
    try {
      drawResults(panel, await api(`/api/forms/${encodeURIComponent(cur.id)}/results`));
    } catch (e) {
      panel.querySelector('[data-fcounts]').textContent = e.message;
    }
  };
  const poll = startPoll(read, POLL_MS);
  panel.querySelector('[data-ftoggle]').addEventListener('click', async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    try {
      drawResults(panel, await api(`/api/forms/${encodeURIComponent(cur.id)}/${panel.dataset.open === '1' ? 'close' : 'open'}`, { method: 'POST' }));
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      b.disabled = false;
    }
  });
  panel.querySelector('[data-fcopy]').addEventListener('click', () => {
    if (panel.dataset.url) copyText(panel.dataset.url, 'Form link copied — paste it in the meeting chat');
    else toast('The public URL is not configured — set quiz.public_url in config/config.json', 'error');
  });
  poll.kick();
}

function questionHtml(q) {
  const head = `<p class="p-form-q"><b>${esc(q.text || q.id)}</b> <span class="muted">· ${q.count}</span></p>`;
  if (q.type === 'text') {
    const rows = (q.answers || []).slice().reverse().map((a) => `<li><span>${esc(a.text)}</span>${a.name ? ` <span class="muted">— ${esc(a.name)}</span>` : ''}</li>`).join('');
    return head + (rows ? `<ul class="p-form-texts">${rows}</ul>` : '');
  }
  if (q.type === 'choice') {
    return head + `<ul class="p-form-tally">${(q.choices || []).map((c) => `<li><span>${esc(c.key)} · ${esc(c.label)}</span><b>${c.count}</b></li>`).join('')}</ul>`;
  }
  const avg = q.average == null ? '–' : Number(q.average).toLocaleString();
  if (q.type === 'nps') {
    const score = q.score == null ? '–' : (q.score > 0 ? `+${q.score}` : String(q.score));
    return head + `<p class="small p-form-stat">NPS <b>${score}</b> · ${q.promoters} promoters · ${q.passives} passives · ${q.detractors} detractors</p>`;
  }
  const label = q.type === 'stars' ? `${avg} ${icon('star')} average` : `${avg} average`;
  return head + `<p class="small p-form-stat"><b>${label}</b>${q.type === 'number' && q.min != null ? ` · ${q.min} to ${q.max}` : ''}</p>`;
}

function drawResults(panel, r) {
  panel.dataset.open = r.open ? '1' : '0';
  panel.dataset.url = r.url || '';
  panel.querySelector('[data-fdot]').className = `p-cap-dot ${r.open ? 'live' : r.link ? 'stopped' : ''}`;
  panel.querySelector('[data-fstate]').textContent = r.open ? 'Open — taking responses'
    : r.link ? 'Closed — the link says the form is closed' : 'Not opened yet';
  panel.querySelector('[data-fcounts]').textContent = `${r.count} response${r.count === 1 ? '' : 's'}`
    + (r.csv ? ` · saved to live/captures/${r.csv}` : '') + (r.open && !r.url ? ' · public URL not configured' : '');
  const toggle = panel.querySelector('[data-ftoggle]');
  toggle.innerHTML = r.open ? `${icon('lock')} Close form` : `${icon('play')} ${r.link ? 'Reopen form' : 'Open form'}`;
  panel.querySelector('[data-fcsv]').hidden = !r.count;
  const sig = JSON.stringify(r.questions);
  const box = panel.querySelector('[data-fresults]');
  if (box.dataset.sig === sig) return; // nothing new: keep the scroll position of a long text list
  box.dataset.sig = sig;
  box.innerHTML = r.count ? r.questions.map(questionHtml).join('') : '<p class="small muted">No responses yet. Answers show here only, never on the stage.</p>';
}
