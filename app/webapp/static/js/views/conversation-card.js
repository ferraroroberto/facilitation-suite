// The Conversation card (Results tab): the live copilot's transcript read back with its hints, under
// the item each belongs to. Read-only; GET /api/sessions/<id>/results/conversation does the ordering
// and grouping. A session the copilot never ran in shows no card.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc } from '/static/js/ui.js';

const TYPE_LABEL = { next_q: 'Ask', notice: 'Notice', drift: 'Time', recap: 'Recap' };
const REASON = { item: 'On item change', them: 'After a line from them', inject: 'After injected lines', drift: 'Clock', mode: 'On mode change' };
const FAILURE = { hub_down: 'hub down', timeout: 'timed out' };

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const clock = (ms) => new Date(ms).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const time = (ms) => `<time class="convo-time" datetime="${new Date(ms).toISOString()}">${clock(ms)}</time>`;

function count(c) {
  return [plural(c.lines, 'line'), plural(c.hints, 'hint'), c.stale ? `${c.stale} stale` : '', c.failed ? `${c.failed} failed` : '']
    .filter(Boolean).join(' · ');
}

function segmentTitle(s) {
  if (s.item_id == null) return 'No item on stage';
  if (!s.in_plan) return 'An item no longer in the plan';
  return s.title || 'Untitled item';
}

/** The channel names as the card shows them: an interview names the interviewee instead of "Them". */
function speakers(c) {
  return { me: 'Me', them: c.mode === 'interview' ? (c.interviewee || 'Interviewee') : 'Them' };
}

function lineRow(e, names) {
  return `<div class="list-row convo-line"><b class="convo-speaker">${esc(names[e.speaker] || e.speaker)}</b>` +
    `<span class="grow">${esc(e.text)}${e.injected ? ' <span class="chip">injected</span>' : ''}</span>${time(e.at_ms)}</div>`;
}

// A hint the presenter never saw says why: a chip only for those exceptions, never for one that was shown.
function hintRow(e) {
  const chip = e.status === 'stale' ? '<span class="chip warn">Dropped · stale</span>'
    : e.status === 'failed' ? `<span class="chip bad">No hint · ${esc(FAILURE[e.failure] || 'failed')}</span>` : '';
  const meta = [REASON[e.reason] || e.reason, e.reason === 'drift' ? '' : e.model,
    e.reason === 'drift' || e.lag_ms == null ? '' : `${(e.lag_ms / 1000).toFixed(1)} s`].filter(Boolean).join(' · ');
  const hints = e.hints.length
    ? `<ul class="convo-hints">${e.hints.map((h) => `<li><span class="convo-hint-type">${esc(TYPE_LABEL[h.type] || h.type)}</span>` +
      `<span>${esc(h.text)}</span></li>`).join('')}</ul>`
    : `<p class="small muted convo-hint-none">${e.status === 'failed' ? 'No answer reached the presenter.' : 'Nothing to suggest.'}</p>`;
  return `<div class="list-row convo-hint convo-hint--${esc(e.status)}"><span class="convo-speaker">${icon('wand-sparkles')}</span>` +
    `<div class="grow">${hints}<div class="convo-hint-meta"><span class="row-meta">${esc(meta)}</span>${chip}</div></div>${time(e.at_ms)}</div>`;
}

function body(c) {
  const names = speakers(c);
  return c.segments.map((s) => `<section class="convo-item" data-item="${esc(s.item_id || '')}">` +
    `<h4 class="convo-item-title">${esc(segmentTitle(s))}</h4>` +
    `<div class="list">${s.entries.map((e) => (e.kind === 'line' ? lineRow(e, names) : hintRow(e))).join('')}</div></section>`).join('');
}

/** @param {string} sid  the session id */
export function conversationCard(sid) {
  const card = document.createElement('details');
  card.className = 'card card--collapsible conversation-card';
  card.hidden = true; // until the copilot is known to have left something
  const summary = (meta) => `<summary class="collapse-summary" data-track="sessions.conversation-toggle"><span class="collapse-main">${icon('message-square')}` +
    `<h3 class="collapse-title">Conversation</h3><span class="collapse-count">${esc(meta)}</span></span>` +
    '<span class="collapse-chevron" aria-hidden="true">›</span></summary>';

  async function load() {
    let c;
    try {
      c = await api(`/api/sessions/${sid}/results/conversation`);
    } catch (e) {
      card.dataset.state = 'error';
      card.hidden = false;
      card.open = true;
      card.innerHTML = `${summary('could not be read')}<div class="collapse-body"></div>`;
      card.querySelector('.collapse-body').appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Retry', onAction: load }));
      return;
    }
    card.dataset.state = c.segments.length ? 'ready' : 'empty';
    card.hidden = !c.segments.length;
    card.innerHTML = `${summary(count(c))}<div class="collapse-body convo-body">${body(c)}</div>`;
  }

  load();
  return card;
}
