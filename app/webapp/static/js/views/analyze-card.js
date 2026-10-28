// The Analyze session card (Results tab and the open session): copies the analyze-session skill's
// command for this session's folder. The app starts no agent; the command is pasted into an agent
// session started in the repo root. GET /api/sessions/<id>/analysis says what the analysis will find.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, toast } from '/static/js/ui.js';

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** One chip per input: what the analysis will use, and what the skill will ask for instead. */
function inputChips(a) {
  const chip = (found, ok, missing) => `<span class="chip${found ? ' ok' : ''}">${icon(found ? 'check' : 'circle-help')} ${esc(found ? ok : missing)}</span>`;
  return chip(a.playbook, 'playbook.md found', 'No playbook.md · the skill will ask what to analyse') +
    chip(a.notes, 'notes.md found', 'No notes.md · the skill will ask for your notes') +
    chip(a.reports.length > 0, `report/ holds ${plural(a.reports.length, 'file')}`, 'report/ is empty');
}

/** @param {string} sid  the session id */
export function analyzeCard(sid) {
  const card = document.createElement('div');
  card.className = 'card analyze-card';
  card.dataset.state = 'loading';
  const head = `<div class="card-head"><h3 class="card-title">${icon('wand-sparkles')} Analyze session</h3></div>`;
  card.innerHTML = head;
  card.appendChild(emptyStateEl('refresh-cw', 'Reading the session…'));

  async function load() {
    let a;
    try {
      a = await api(`/api/sessions/${sid}/analysis`);
    } catch (e) {
      card.dataset.state = 'error';
      card.innerHTML = head;
      card.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Retry', onAction: load }));
      return;
    }
    draw(a);
  }

  function draw(a) {
    const ready = a.captured > 0;
    card.dataset.state = ready ? 'ready' : 'empty';
    card.innerHTML = head +
      `<div class="analyze-inputs">${inputChips(a)}</div>` +
      `<input class="input mono analyze-command" readonly value="${esc(a.command)}" aria-label="Command to paste into the agent" ${ready ? '' : 'disabled'}>` +
      `<p class="small muted analyze-note" role="status">${esc(ready ? a.hint : a.reason)}</p>` +
      `<div class="row-actions"><button type="button" class="button-tint" data-copy data-track="results.copy-analyze" ${ready ? '' : 'disabled'}>${icon('copy')} Copy command</button></div>`;
    card.querySelector('[data-copy]').addEventListener('click', () => copy(a));
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
