// The re-import review (epic §6): what changed in the deck — identical,
// modified (image / title / notes), new, removed, moved — each change with its
// own switch, applied only on "Apply". Shown by the Plan tab in place of the
// plan; Back leaves the review waiting, Cancel throws the staged export away.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { switchEl, setSwitch } from '/static/_vendored/switch/switch.js';
import { api, esc, toast } from '/static/js/ui.js';
import { confirmDialog } from '/static/js/dialogs.js';

const KINDS = [
  ['identical', 'Identical'],
  ['modified', 'Modified'],
  ['new', 'New'],
  ['removed', 'Removed'],
  ['moved', 'Moved'],
];
const PROFILE_TEXT = {
  camera_strip: (n) => `${n} slide${n === 1 ? ' leaves' : 's leave'} the right-hand grey strip empty → Camera strip`,
  camera_pip: (n) => `${n} slide${n === 1 ? ' has' : 's have'} an empty top-right box → Camera PiP`,
  screen_only: (n) => `${n} slide${n === 1 ? ' uses' : 's use'} the full width → Screen only`,
  unsure: (n) => `${n} slide${n === 1 ? ' is' : 's are'} unsure — pick a profile in the plan`,
};

/**
 * Render the review into ``host``. ``onClose(applied)`` runs when the user
 * leaves it: true after Apply or Cancel (reload the plan), false on Back.
 */
export async function openReview(host, sid, { onClose }) {
  host.innerHTML = '';
  host.appendChild(emptyStateEl('refresh-cw', 'Comparing the slides…'));
  let data;
  try {
    data = await api(`/api/sessions/${sid}/reimport`);
  } catch (e) {
    host.innerHTML = '';
    host.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Back', onAction: () => onClose(false) }));
    return;
  }
  if (!data.pending) { onClose(true); return; }
  const accepted = new Set(data.changes.map((c) => c.id));
  const oldSrc = (f) => `/api/sessions/${sid}/slides/${encodeURIComponent(f)}`;
  const newSrc = (f) => `/api/sessions/${sid}/reimport/slides/${encodeURIComponent(f)}`;

  host.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'review';
  host.appendChild(wrap);

  const deckName = (data.source || '').split(/[\\/]/).pop() || 'the deck';
  const when = data.imported_at ? new Date(data.imported_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  const head = document.createElement('div');
  head.className = 'card review-head';
  head.innerHTML = `<button type="button" class="home-toggle" data-back aria-label="Back to the plan" title="Back to the plan — the review keeps waiting">${icon('chevron-left')}</button>` +
    `<div class="grow"><div class="row-title">Re-import PowerPoint</div><div class="row-meta">${esc(deckName)}${when ? ` · exported ${esc(when)}` : ''}</div></div>`;
  head.querySelector('[data-back]').addEventListener('click', () => onClose(false));
  wrap.appendChild(head);

  const tiles = document.createElement('div');
  tiles.className = 'review-tiles';
  tiles.innerHTML = KINDS.map(([k, label]) => `<div class="review-tile ${k}"><b>${data.counts[k] || 0}</b><span>${label}</span></div>`).join('');
  wrap.appendChild(tiles);
  wrap.insertAdjacentHTML('beforeend', '<p class="small muted review-note">Slides are matched by PowerPoint\'s own slide ID first, then by image and text fingerprints. Activities stay anchored to the slide they follow.</p>');

  const list = document.createElement('div');
  list.className = 'card list-card review-list';
  if (!data.changes.length) {
    list.appendChild(emptyStateEl('circle-check', 'Nothing changed in the deck — Apply refreshes the slide images.'));
  }
  data.changes.forEach((c) => {
    const row = document.createElement('div');
    row.className = 'review-row';
    row.dataset.change = c.id;
    const label = c.kind === 'new'
      ? `${c.after ? `After ${c.after}` : 'At the start'} · ${c.title}`
      : `${c.position} · ${c.title}`;
    const thumb = (side, src) => (side ? `<img class="review-thumb" loading="lazy" alt="" src="${src(side.file)}">` : '<span class="review-thumb empty"></span>');
    row.innerHTML = `<span class="review-pill ${c.kind}">${esc(KINDS.find(([k]) => k === c.kind)[1])}</span>` +
      `<span class="review-thumbs">${thumb(c.old, oldSrc)}<span class="review-arrow" aria-hidden="true">→</span>${thumb(c.new, newSrc)}</span>` +
      `<span class="review-text"><span class="row-title">${esc(label)}</span><span class="row-meta">${esc(c.detail)}</span></span>`;
    row.appendChild(switchEl(true, {
      label: `Apply: ${label}`,
      onToggle: (next, btn) => {
        setSwitch(btn, next);
        if (next) accepted.add(c.id); else accepted.delete(c.id);
        row.classList.toggle('declined', !next);
        syncApply();
      },
    }));
    list.appendChild(row);
  });
  wrap.appendChild(list);

  if (data.identical.length) {
    const det = document.createElement('details');
    det.className = 'card card--collapsible';
    det.innerHTML = `<summary class="collapse-summary"><span class="collapse-main"><h3 class="collapse-title">${data.identical.length} identical slide${data.identical.length === 1 ? '' : 's'}</h3>` +
      '<span class="collapse-count">nothing to do</span></span><span class="collapse-chevron" aria-hidden="true">›</span></summary>' +
      `<div class="collapse-body"><div class="list">${data.identical.map((s) =>
        `<div class="list-row"><span class="grow"><span class="row-title">${s.index} · ${esc(s.title)}</span>` +
        `${s.rematched ? '<span class="row-meta">Matched by its picture and text — PowerPoint gave it a new slide ID</span>' : ''}</span></div>`).join('')}</div></div>`;
    wrap.appendChild(det);
  }

  const prof = Object.entries(data.profiles || {}).filter(([, n]) => n);
  if (prof.length) {
    const card = document.createElement('div');
    card.className = 'card review-obs';
    card.innerHTML = '<div class="row-title">OBS profile detected from the slide images</div>' +
      `<p class="small muted">${esc(prof.map(([k, n]) => (PROFILE_TEXT[k] || ((x) => `${x} ${k}`))(n)).join(' · '))}. Change any of them in the plan.</p>`;
    wrap.appendChild(card);
  }

  const actions = document.createElement('div');
  actions.className = 'row-actions review-actions';
  actions.innerHTML = '<button type="button" class="button-primary" data-apply></button><button type="button" class="button-surface" data-cancel>Cancel</button>';
  wrap.appendChild(actions);
  const applyBtn = actions.querySelector('[data-apply]');
  function syncApply() {
    const n = accepted.size;
    applyBtn.textContent = data.changes.length ? `Apply ${n} change${n === 1 ? '' : 's'}` : 'Apply';
  }
  syncApply();

  applyBtn.addEventListener('click', async () => {
    applyBtn.disabled = true;
    try {
      const r = await api(`/api/sessions/${sid}/reimport/apply`, { method: 'POST', body: { accepted: [...accepted] } });
      const parts = [r.modified && `${r.modified} updated`, r.added && `${r.added} added`, r.removed && `${r.removed} removed`, r.moved && `${r.moved} moved`].filter(Boolean);
      toast(`Re-import applied${parts.length ? ` · ${parts.join(' · ')}` : ''}${r.declined ? ` · ${r.declined} left as they were` : ''}`);
      onClose(true);
    } catch (e) {
      toast(e.message, 'error');
      applyBtn.disabled = false;
    }
  });
  actions.querySelector('[data-cancel]').addEventListener('click', async () => {
    const ok = await confirmDialog({ title: 'Cancel the re-import?', message: 'The exported slides are thrown away; the plan and slides stay as they are.', actionLabel: 'Cancel re-import', danger: true });
    if (!ok) return;
    try {
      await api(`/api/sessions/${sid}/reimport`, { method: 'DELETE' });
      toast('Re-import cancelled');
      onClose(true);
    } catch (e) { toast(e.message, 'error'); }
  });
}
