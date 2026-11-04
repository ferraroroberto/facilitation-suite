// The recorder part of the Conversation card (Results tab): the podcast recorder's episode folder, the
// after-call run that transcribes its per-side tracks through the hub, and the summary of the call it
// leaves. GET/POST /api/sessions/<id>/results/recorded does the work; this module shows it and polls
// while a run is going.

import { api, esc } from '/static/js/ui.js';

const FAILURE = {
  hub_down: 'the hub is not answering', timeout: 'the hub timed out', error: 'the hub gave no usable answer',
  ffmpeg_missing: 'ffmpeg is not installed on this PC', unreadable: 'a track could not be read',
  write_failed: 'the file could not be written', empty: 'nothing was said in the tracks',
};
const POLL_MS = 2000;

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const duration = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const when = (ms) => new Date(ms).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const reason = (code, detail) => `${FAILURE[code] || code}${detail ? ` (${detail})` : ''}`;

/** The summary of the call, at the head of the recorded transcript: its text, or why there is none. */
export function summaryHtml(summary) {
  if (!summary) return '';
  const body = summary.text
    ? `<p class="convo-summary-text">${esc(summary.text)}</p><p class="small muted">${esc(summary.model || '')}</p>`
    : `<p class="small muted">No summary: ${esc(reason(summary.failure, ''))}.</p>`;
  return `<section class="convo-summary"><h4 class="convo-item-title">Summary of the call</h4>${body}</section>`;
}

function statusText(st) {
  const job = st.job;
  if (!st.consent) return 'Confirm the copilot consent for this session first: the presenter asks for it when Copilot is switched on.';
  if (job && job.state === 'running') {
    if (job.step === 'summary') return 'Summarising the call…';
    return `Transcribing ${plural(job.tracks.length, 'track')} · ${duration(job.done_s)} of ${duration(job.total_s)} of audio`;
  }
  if (job && job.state === 'failed') return `Not transcribed: ${reason(job.failure, job.detail)}.`;
  const parts = [];
  if (st.transcript) parts.push(`Transcribed ${when(st.transcript.modified_ms)} · ${plural(st.transcript.lines, 'line')}`);
  if (job && job.missed) parts.push(`${plural(job.missed, 'chunk')} could not be transcribed`);
  if (job && job.summary_failure && st.summary && st.summary.text) parts.push(`summary not refreshed: ${reason(job.summary_failure, job.summary_detail)}`);
  return parts.length ? `${parts.join(' · ')}.` : 'After the call, transcribe each side’s own track from the podcast recorder’s episode folder.';
}

/**
 * The recorder section: the folder, Transcribe, Summarise again and the run's state.
 * @param {string} sid  the session id
 * @param {object} st  GET …/results/recorded
 * @param {() => void} done  called when a run ends, so the card reads the new transcript
 */
export function recorderSection(sid, st, done) {
  const el = document.createElement('section');
  el.className = 'convo-recorder';
  const running = Boolean(st.job && st.job.state === 'running');
  const off = !st.consent || running;
  el.innerHTML = '<h4 class="convo-item-title">Recorder tracks</h4>' +
    '<form class="convo-recorder-form">' +
    `<input class="input" type="text" name="folder" value="${esc(st.folder || '')}" aria-label="The podcast recorder's episode folder" ` +
    `placeholder="The podcast recorder's episode folder" autocomplete="off" spellcheck="false"${off ? ' disabled' : ''}>` +
    `<button type="submit" class="button-tint" data-track="results.recorder-transcribe"${off ? ' disabled' : ''}` +
    `${st.consent ? '' : ' title="Confirm the copilot consent first"'}>${st.transcript ? 'Transcribe again' : 'Transcribe'}</button>` +
    '</form>' +
    `<div class="convo-recorder-state"><p class="small muted grow" role="status">${esc(statusText(st))}</p>` +
    (st.transcript && !off ? '<button type="button" class="button-ghost" data-track="results.recorder-summarise">Summarise again</button>' : '') +
    '</div>';

  const say = (text) => { el.querySelector('[role="status"]').textContent = text; };
  const run = async (path, body) => {
    try {
      el.replaceWith(recorderSection(sid, await api(`/api/sessions/${sid}/results/recorded${path}`, { method: 'POST', body }), done));
    } catch (e) { say(e.message); }
  };
  el.querySelector('form').addEventListener('submit', (e) => {
    e.preventDefault();
    const folder = el.querySelector('input[name="folder"]').value.trim();
    if (!folder) { say('Type the episode folder first.'); return; }
    run('', { folder });
  });
  el.querySelector('[data-track="results.recorder-summarise"]')?.addEventListener('click', () => run('/summary'));

  if (running) {
    setTimeout(async () => {
      if (!el.isConnected) return;
      let next;
      try { next = await api(`/api/sessions/${sid}/results/recorded`); } catch (e) { say(e.message); return; }
      if (next.job && next.job.state === 'running') el.replaceWith(recorderSection(sid, next, done));
      else done();
    }, POLL_MS);
  }
  return el;
}
