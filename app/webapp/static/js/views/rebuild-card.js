// The Rebuild results card (Results tab): a past session's frozen captures recomputed with today's
// rules. Preview (GET /api/sessions/<id>/rebuild, writes nothing) → Rebuild (POST, backs up first) →
// Undo (POST …/undo). A session that is live cannot be rebuilt; the server says so.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, toast } from '/static/js/ui.js';

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const RULES = 'Each activity uses the options the Plan says now, and rows the chat reader posted again are left out.';

/** The numbers that moved for one activity, as chips; an empty list means nothing changes. */
function changes(r) {
  const out = [];
  if (r.answers_before !== r.answers_after) out.push(`answers ${r.answers_before} → ${r.answers_after}`);
  if (r.whole_before !== null && r.whole_before !== r.whole_after) out.push(`shown whole ${r.whole_before} → ${r.whole_after}`);
  if (r.added.length) out.push(`${r.added.length} added`);
  if (r.removed.length) out.push(`${r.removed.length} removed`);
  const merged = Object.keys(r.merged).length;
  if (merged) out.push(`${merged} merged`);
  if (r.count_changes) out.push(`${plural(r.count_changes, 'count')} changed`);
  if (r.reposts_dropped) out.push(`${plural(r.reposts_dropped, 'reposted row')} dropped`);
  if (r.windows_cut) out.push(`${plural(r.windows_cut, 'window')} cut`);
  return out;
}

/**
 * @param {string} sid  the session id
 * @param {string|null} latest  the newest backup a rebuild left in the session folder (what Undo restores)
 * @param {() => Promise<void>} onChange  called after a rebuild or an undo, so the tab reads the new results
 */
export function rebuildCard(sid, latest, onChange) {
  const card = document.createElement('div');
  card.className = 'card rebuild-card';
  const url = `/api/sessions/${sid}/rebuild`;
  const head = `<div class="card-head"><h3 class="card-title">${icon('refresh-cw')} Rebuild results</h3></div>`;
  let backup = latest;

  const shell = (inner, state) => {
    card.dataset.state = state;
    card.innerHTML = head + inner;
  };

  const undoButton = () => (backup
    ? `<button type="button" class="button-surface" data-undo data-track="results.rebuild-undo">${icon('rotate-ccw')} Undo last rebuild</button>` : '');

  function idle(note) {
    shell(`<p class="small muted rebuild-note" role="status">${esc(note || `Recompute this session's results with today's rules. ${RULES} Nothing changes until you confirm.`)}</p>` +
      `<div class="row-actions"><button type="button" class="button-tint" data-preview data-track="results.rebuild-preview">${icon('eye')} Preview</button>${undoButton()}</div>`, 'idle');
    wire();
  }

  function wire() {
    card.querySelector('[data-preview]')?.addEventListener('click', preview);
    card.querySelector('[data-undo]')?.addEventListener('click', undo);
  }

  function failed(e) {
    shell('', 'error');
    card.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Back', onAction: () => idle() }));
  }

  async function preview() {
    shell(`<p class="small muted rebuild-note" role="status">${icon('refresh-cw')} Recomputing every capture…</p>`, 'loading');
    let p;
    try {
      p = await api(url);
    } catch (e) { failed(e); return; }
    backup = p.backup;
    draw(p);
  }

  function draw(p) {
    const rows = p.activities;
    if (!rows.length) { idle('No capture of this session was frozen, so there is nothing to rebuild.'); return; }
    const cut = p.stop_capture_on_leave ? 'A capture is cut where its activity was left (the session setting).' : 'Captures are not cut when an activity is left (the session setting).';
    const list = rows.map((r) => {
      const c = changes(r);
      return `<div class="list-row rebuild-row"><div class="grow"><div class="row-title">${esc(r.title)}</div>` +
        `<div class="row-meta">${c.length ? c.map((t) => `<span class="chip warn">${esc(t)}</span>`).join(' ') : '<span class="muted">No change</span>'}</div></div></div>`;
    }).join('');
    const none = p.changed === 0;
    shell(`<p class="small muted rebuild-note" role="status">${esc(none ? 'Nothing would change.' : `${plural(p.changed, 'activity')} of ${rows.length} would change.`)} ${esc(cut)}</p>` +
      `<div class="list">${list}</div>` +
      `<p class="small muted">A backup of the current results is kept in the session folder › live/. Re-export the PDF and the Excel afterwards.</p>` +
      `<div class="row-actions"><button type="button" class="button-primary" data-confirm data-track="results.rebuild-apply" ${none ? 'disabled' : ''}>${icon('check')} Rebuild ${p.changed} ${p.changed === 1 ? 'activity' : 'activities'}</button>` +
      `<button type="button" class="button-surface" data-cancel>Cancel</button></div>`, 'preview');
    card.querySelector('[data-confirm]').addEventListener('click', apply);
    card.querySelector('[data-cancel]').addEventListener('click', () => idle());
  }

  async function apply() {
    shell(`<p class="small muted rebuild-note" role="status">${icon('refresh-cw')} Drawing the stage pictures, then swapping in the results…</p>`, 'busy');
    try {
      const r = await api(url, { method: 'POST' });
      backup = r.backup;
      await onChange();
      idle(`Rebuilt ${plural(r.changed, 'activity')}. Export the PDF and the Excel again to carry it over. Undo puts the previous results back.`);
      toast('Results rebuilt');
    } catch (e) { failed(e); }
  }

  async function undo() {
    shell(`<p class="small muted rebuild-note" role="status">${icon('rotate-ccw')} Putting the previous results back…</p>`, 'busy');
    try {
      const r = await api(`${url}/undo`, { method: 'POST' });
      backup = r.backup;
      await onChange();
      idle('The previous results are back. Export the PDF and the Excel again to carry it over.');
      toast('Previous results restored');
    } catch (e) { failed(e); }
  }

  idle();
  return card;
}
