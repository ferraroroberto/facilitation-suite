// The Playbook card of an open session: the session folder's playbook.md, rendered by the server
// (GET /api/sessions/<id>/playbook), with Edit (opens the file in the default editor), Reload and,
// for a session without one, Create playbook. The app shows the playbook; it never edits it.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, toast } from '/static/js/ui.js';

/**
 * @param {string} sid        the session id
 * @param {boolean} known     whether the folder card already lists playbook.md (the session payload's answer)
 * @param {() => void} onChanged  the playbook appeared or went away: the session redraws, so the folder card agrees
 */
export function playbookCard(sid, known, onChanged) {
  const card = document.createElement('div');
  card.className = 'card playbook-card';
  const url = `/api/sessions/${sid}/playbook`;

  /** Draw the card for a read of the playbook; `userAction` = a Create/Reload the user just pressed. */
  function show(read, userAction) {
    card.innerHTML = `<div class="card-head"><h3 class="card-title">${icon('file-text')} Playbook</h3></div>`;
    if (!read.exists) {
      card.dataset.state = 'empty';
      card.appendChild(emptyStateEl('file-text', 'No playbook for this session yet', { actionLabel: 'Create playbook', onAction: create }));
    } else {
      card.dataset.state = 'ready';
      drawBody(read.html);
    }
    if (userAction && read.exists !== known) onChanged();
  }

  function drawBody(html) {
    const body = document.createElement('div');
    body.className = 'prose playbook-body';
    body.dataset.collapsed = '';
    body.innerHTML = html; // the server escapes every character of the file before it adds a tag
    const actions = document.createElement('div');
    actions.className = 'row-actions';
    actions.innerHTML =
      `<button type="button" class="button-surface" data-edit data-track="sessions.playbook-edit">${icon('pencil')} Edit</button>` +
      `<button type="button" class="button-surface" data-reload data-track="sessions.playbook-reload">${icon('refresh-cw')} Reload</button>` +
      `<button type="button" class="button-surface" data-toggle aria-expanded="false" hidden data-track="sessions.playbook-toggle">${icon('chevron-down')} <span>Show all</span></button>`;
    card.append(body, actions);
    actions.querySelector('[data-edit]').addEventListener('click', edit);
    actions.querySelector('[data-reload]').addEventListener('click', () => load(true));
    const toggle = actions.querySelector('[data-toggle]');
    toggle.addEventListener('click', () => {
      const open = body.hasAttribute('data-collapsed');
      if (open) body.removeAttribute('data-collapsed'); else body.dataset.collapsed = '';
      toggle.setAttribute('aria-expanded', String(open));
      toggle.querySelector('span').textContent = open ? 'Show less' : 'Show all';
    });
    // The button only shows when the text is longer than the collapsed box.
    requestAnimationFrame(() => { toggle.hidden = body.scrollHeight <= body.clientHeight + 1; });
  }

  async function load(userAction) {
    if (userAction && card.dataset.state === 'ready') card.dataset.state = 'loading';
    try {
      const read = await api(url);
      show(read, userAction);
      if (userAction) toast('Playbook reloaded');
    } catch (e) {
      card.dataset.state = 'error';
      card.innerHTML = `<div class="card-head"><h3 class="card-title">${icon('file-text')} Playbook</h3></div>`;
      card.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Retry', onAction: () => load(false) }));
    }
  }

  async function create() {
    try {
      show(await api(url, { method: 'POST' }), true);
    } catch (e) {
      toast(e.message, 'error');
      if (e.status === 409) load(true); // someone made one meanwhile: show it
    }
  }

  async function edit() {
    try { await api(`/api/sessions/${sid}/open`, { method: 'POST', body: { what: 'playbook' } }); }
    catch (e) { toast(e.message, 'error'); }
  }

  card.dataset.state = 'loading';
  card.append(emptyStateEl('refresh-cw', 'Reading the playbook…'));
  load(false);
  return card;
}
