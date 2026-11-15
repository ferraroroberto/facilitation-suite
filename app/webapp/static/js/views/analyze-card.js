// The Analyze session card (Results tab and the open session). *Run analysis* asks the hub for the
// session's report (POST /api/sessions/<id>/analysis/run): it lands in report/ and shows here as a draft
// to review. *Copy command* copies the analyze-session skill's command instead, for the interactive run
// in an agent session started in the repo root. GET /api/sessions/<id>/analysis says what the analysis
// will find and how the current or last run stands.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, plural, toast } from '/static/js/ui.js';
import { CardState, setCardState, settledState } from '/static/js/card-state.js';

// A report takes minutes; one read every few seconds keeps the card current without loading the server.
const POLL_MS = 3000;
const FAILURES = {
  hub_down: 'The hub is not answering',
  timeout: 'No report in time',
  error: 'The hub gave no report',
  write: 'The report could not be saved in report/',
};

/** One chip per input: what the analysis will use, and what happens without it. */
function inputChips(a) {
  const chip = (found, ok, missing) => `<span class="chip${found ? ' ok' : ''}">${icon(found ? 'check' : 'circle-help')} ${esc(found ? ok : missing)}</span>`;
  return chip(a.playbook, 'playbook.md found', 'No playbook.md · a general report') +
    chip(a.notes, 'notes.md found', 'No notes.md · answers and counts only') +
    chip(a.reports.length > 0, `report/ holds ${plural(a.reports.length, 'file')}`, 'report/ is empty');
}

/** The run's line under the buttons: running, written, or why it failed. */
function jobText(job) {
  if (!job) return '';
  if (job.running) return 'Analysing through the hub… a report takes a few minutes.';
  if (job.failed) return `${FAILURES[job.failure] || 'The analysis failed'}: ${job.detail}`;
  return `Draft saved as report/${job.report} (${job.model}). Review it before you hand it on.`;
}

/** @param {string} sid  the session id */
export function analyzeCard(sid) {
  const card = document.createElement('div');
  card.className = 'card analyze-card';
  setCardState(card, CardState.LOADING);
  const head = `<div class="card-head"><h3 class="card-title">${icon('wand-sparkles')} Analyze session</h3></div>`;
  card.innerHTML = head;
  card.appendChild(emptyStateEl('refresh-cw', 'Reading the session…'));

  async function load() {
    let a;
    try {
      a = await api(`/api/sessions/${sid}/analysis`);
    } catch (e) {
      setCardState(card, CardState.ERROR);
      card.innerHTML = head;
      card.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Retry', onAction: load }));
      return;
    }
    draw(a);
  }

  function draw(a) {
    const ready = a.captured > 0;
    const job = a.job;
    const running = Boolean(job && job.running);
    const canRun = ready && !a.run_unavailable && !running;
    setCardState(card, settledState(ready));
    card.innerHTML = head +
      `<div class="analyze-inputs">${inputChips(a)}</div>` +
      `<p class="small muted analyze-run-note" role="status">${esc(!ready ? a.reason : (jobText(job) || a.run_unavailable || 'Run it here through the hub, or copy the command for an agent session.'))}</p>` +
      (job && job.text ? `<div class="analyze-draft-head small muted">Draft · ${esc(job.report || 'not saved')}</div><pre class="analyze-draft" tabindex="0" aria-label="Draft report">${esc(job.text)}</pre>` : '') +
      `<div class="row-actions"><button type="button" class="button-primary" data-run data-track="results.run-analysis" ${canRun ? '' : 'disabled'}>${icon(running ? 'refresh-cw' : 'wand-sparkles')} ${running ? 'Analysing…' : job ? 'Run again' : 'Run analysis'}</button></div>` +
      `<input class="input mono analyze-command" readonly value="${esc(a.command)}" aria-label="Command to paste into the agent" ${ready ? '' : 'disabled'}>` +
      `<p class="small muted analyze-note">${esc(ready ? a.hint : '')}</p>` +
      `<div class="row-actions"><button type="button" class="button-tint" data-copy data-track="results.copy-analyze" ${ready ? '' : 'disabled'}>${icon('copy')} Copy command</button></div>`;
    card.querySelector('[data-copy]').addEventListener('click', () => copy(a));
    card.querySelector('[data-run]').addEventListener('click', run);
    if (running) setTimeout(poll, POLL_MS);
  }

  async function run() {
    card.querySelector('[data-run]').disabled = true;
    try {
      settle(await api(`/api/sessions/${sid}/analysis/run`, { method: 'POST' }));
    } catch (e) {
      card.querySelector('.analyze-run-note').textContent = e.message;
      card.querySelector('[data-run]').disabled = false;
    }
  }

  async function poll() {
    if (!card.isConnected) return;
    let a;
    try { a = await api(`/api/sessions/${sid}/analysis`); } catch (e) {
      card.querySelector('.analyze-run-note').textContent = `${e.message} — checking again…`;
      setTimeout(poll, POLL_MS);
      return;
    }
    settle(a);
  }

  /** Draw a read taken after a run started; a run that ended says so once, as the POST or a poll saw it. */
  function settle(a) {
    draw(a);
    if (!a.job || a.job.running) return;
    if (a.job.failed) toast(jobText(a.job), 'error');
    else toast(`Analysis saved as report/${a.job.report}`);
  }

  // A browser can refuse the clipboard (an insecure page, a denied permission): the command stays in the
  // field above, selected, so Ctrl+C finishes the job and the note says so.
  async function copy(a) {
    const field = card.querySelector('.analyze-command');
    const note = card.querySelector('.analyze-note');
    try {
      await navigator.clipboard.writeText(a.command);
      note.textContent = `Copied. ${a.hint}`;
      toast('Command copied — paste it into an agent session in the facilitation-suite repo');
    } catch (e) {
      field.focus();
      field.select();
      note.textContent = 'The browser blocked copying. The command is selected: press Ctrl+C.';
      toast('Could not copy — the command is selected, press Ctrl+C', 'error');
    }
  }

  load();
  return card;
}
