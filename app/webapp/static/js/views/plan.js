// Plan tab: sections with planned minutes, and slides / activities / breaks in
// order (left), the selected item's editor (right). Edits are staged in memory;
// "Save to session.yaml" is the one persistence boundary.
//
// Selection works as in a file manager: click selects one item, Ctrl/Cmd+click
// adds or removes one, Shift+click selects the range from the last clicked
// (Ctrl+Shift adds it); Shift+Up/Down extend, Ctrl+A selects all, Ctrl+D
// duplicates, Delete deletes, Esc keeps only the focused item. With several selected, the editor
// becomes the bulk panel and dragging any of them moves them all.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { switchEl } from '/static/_vendored/switch/switch.js';
import { api, esc, oneLine, pageHead, setStatus, toast, fmtMinutes } from '/static/js/ui.js';
import { formDialog, confirmDialog, rowMenu } from '/static/js/dialogs.js';
import { importDialog } from '/static/js/importer.js';
import { openReview } from '/static/js/reimport.js';
import { createStage, applySessionTheme } from '/static/js/stage-render.js';

const PROFILES = [
  ['camera_strip', 'Camera strip'],
  ['camera_pip', 'Camera PiP'],
  ['screen_only', 'Screen only'],
];
const FONTS = [
  ['theme', 'Session font (Sessions → Stage font)'],
  ['system-ui', 'System sans'],
  ['Georgia', 'Georgia (serif)'],
  ['Segoe Print', 'Segoe Print (handwriting)'],
];
const TIMER_START = { manual: 'When I start it', on_enter: 'When the item opens', with_capture: 'With the capture' };
const TIMER_END = { keep: 'Keep showing 00:00', stop_capture: 'Stop the capture', advance: 'Go to the next item', chime: 'Play a chime' };
const KIND_ICON = { break: 'coffee' };

let ctx;
let head;
let listCard;
let editor;
let toolbar;
let reviewNote;
let split;
let reviewHost;
// selected = the focused item (the editor's); picked = every selected item (it included); anchor = where Shift ranges start.
const st = { sid: null, session: null, slides: new Map(), deck: null, types: {}, selected: null, picked: new Set(), anchor: null, dirty: false, collapsed: new Set(), loadedFor: null, pending: false };

// ---------------------------------------------------------------- helpers

function clone(o) { return JSON.parse(JSON.stringify(o)); }

function allItems() {
  const out = [];
  (st.session.sections || []).forEach((sec, si) => sec.items.forEach((it, ii) => out.push({ it, sec, si, ii })));
  return out;
}

function findItem(id) { return allItems().find((x) => x.it.id === id) || null; }

function slideOf(it) { return it.kind === 'slide' ? st.slides.get(it.slide_id) : null; }

function titleOf(it) {
  if (it.title) return it.title;
  if (it.kind === 'slide') { const s = slideOf(it); return s ? s.title : `Slide ${it.slide_id}`; }
  if (it.kind === 'activity') return it.question || (st.types[it.type] || {}).label || 'Activity';
  return 'Break';
}

/** The title on one line, for the list and the editor ("\n" breaks it only on the stage). */
function labelOf(it) { return oneLine(titleOf(it)); }

function newId(prefix) { return `${prefix}-${Math.random().toString(16).slice(2, 8)}`; }

function fmtTimer(sec) {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function parseTimer(text) {
  const t = String(text).trim();
  const m = t.match(/^(\d+)(?::(\d{1,2}))?$/);
  if (!m) return null;
  return m[2] === undefined ? parseInt(m[1], 10) * 60 : parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}

function markDirty() {
  st.dirty = true;
  renderToolbar();
  // Every edit (question, options, profile…) redraws the stage preview.
  const found = st.selected && findItem(st.selected);
  if (found) updatePreview(found.it);
}

function thumbHtml(it) {
  if (it.kind === 'slide') {
    const s = slideOf(it);
    if (s) return `<img class="thumb" loading="lazy" alt="" src="/api/sessions/${st.sid}/slides/${s.file}">`;
  }
  const glyph = it.kind === 'activity' ? ((st.types[it.type] || {}).icon || 'message-square') : (KIND_ICON[it.kind] || 'image');
  return `<span class="thumb thumb-icon">${icon(glyph)}</span>`;
}

function chipHtml(it) {
  const chips = [];
  if (it.kind === 'activity') chips.push(`<span class="chip accent">${esc((st.types[it.type] || {}).label || it.type)}</span>`);
  if (it.kind === 'break') chips.push('<span class="chip">Break</span>');
  if (it.timer && it.timer.enabled) chips.push(`<span class="chip">${icon('timer')}${fmtTimer(it.timer.seconds)}</span>`);
  if (!it.profile && it.kind === 'slide') chips.push('<span class="chip warn">Pick profile</span>');
  return chips.join('');
}

// ---------------------------------------------------------------- mount

export async function mount(el, context) {
  ctx = context;
  el.innerHTML = '';
  split = document.createElement('div');
  split.className = 'split plan-split';
  reviewHost = document.createElement('div');
  reviewHost.className = 'review-host';
  reviewHost.hidden = true;
  const left = document.createElement('div');
  left.className = 'split-list';
  editor = document.createElement('div');
  editor.className = 'split-detail';
  split.append(left, editor);
  el.append(split, reviewHost);

  head = pageHead({ glyph: 'presentation', title: 'Plan', status: '' });
  left.appendChild(head);
  toolbar = document.createElement('div');
  toolbar.className = 'plan-toolbar';
  left.appendChild(toolbar);
  reviewNote = document.createElement('div');
  left.appendChild(reviewNote);
  listCard = document.createElement('div');
  listCard.className = 'card list-card plan-list';
  left.appendChild(listCard);
  ctx.onSession(() => { if (!st.dirty) load(); });
  await load();
}

export function show() {
  if (ctx.reviewFor && ctx.reviewFor === ctx.sessionId) { load(); return; }
  if (st.loadedFor !== ctx.sessionId && !st.dirty) load();
}

function showReview() {
  split.hidden = true;
  reviewHost.hidden = false;
  window.scrollTo(0, 0);
  openReview(reviewHost, st.sid, {
    onClose: (changed) => {
      reviewHost.hidden = true;
      reviewHost.innerHTML = '';
      split.hidden = false;
      if (changed) load(); else renderReviewNote();
    },
  });
}

function renderReviewNote() {
  reviewNote.innerHTML = '';
  if (!st.pending) return;
  const b = document.createElement('div');
  b.className = 'banner warn review-banner';
  b.innerHTML = `${icon('refresh-cw')}<span class="grow">A re-import is waiting for your review — nothing has changed yet.</span>` +
    '<button type="button" class="banner-action" data-review>Review</button>';
  b.querySelector('[data-review]').addEventListener('click', showReview);
  reviewNote.appendChild(b);
}

async function load() {
  st.sid = ctx.sessionId;
  st.loadedFor = st.sid;
  st.dirty = false;
  st.collapsed.clear(); // every section opens expanded
  if (!st.sid) {
    setStatus(head, 'no session');
    toolbar.innerHTML = '';
    listCard.innerHTML = '';
    listCard.appendChild(emptyStateEl('calendar-days', 'Pick or create a session first.', { actionLabel: 'Sessions', onAction: () => ctx.goTo('sessions') }));
    editor.innerHTML = '';
    return;
  }
  listCard.innerHTML = '';
  listCard.appendChild(emptyStateEl('refresh-cw', 'Reading the plan…'));
  try {
    const [detail, deck, acts, review] = await Promise.all([
      api(`/api/sessions/${st.sid}`),
      api(`/api/sessions/${st.sid}/slides`),
      api('/api/activities'),
      api(`/api/sessions/${st.sid}/reimport`).catch(() => ({ pending: false })),
    ]);
    st.pending = review.pending;
    st.session = detail.session;
    st.deck = deck;
    st.slides = new Map(deck.slides.map((s) => [s.slide_id, s]));
    st.types = Object.fromEntries(acts.types.map((t) => [t.type, t]));
    // Camera zones as set in Settings (the preview's dashed box); defaults if unreachable.
    st.zones = await api('/api/settings').then((x) => Object.fromEntries(Object.entries(x.profiles).map(([k, v]) => [k, v.zone]))).catch(() => null);
  } catch (e) {
    listCard.innerHTML = '';
    listCard.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Retry', onAction: load }));
    return;
  }
  applySessionTheme(st.sid, st.session.theme, Date.now()); // the stage font, for the previews
  if (!st.selected || !findItem(st.selected)) {
    const first = allItems()[0];
    st.selected = first ? first.it.id : null;
  }
  st.picked = new Set([...st.picked].filter((id) => findItem(id)));
  if (!st.picked.size && st.selected) st.picked.add(st.selected);
  render();
  if (st.pending && ctx.reviewFor === st.sid) {
    ctx.reviewFor = null;
    showReview();
  }
}

function render() {
  renderReviewNote();
  const secs = st.session.sections || [];
  const total = secs.reduce((a, s) => a + (s.minutes || 0), 0);
  setStatus(head, `${secs.length} sections · ${fmtMinutes(total)}`);
  renderToolbar();
  renderList();
  renderEditor();
}

function renderToolbar() {
  const secs = st.session ? st.session.sections || [] : [];
  const total = secs.reduce((a, s) => a + (s.minutes || 0), 0);
  const timers = allItems().reduce((a, x) => a + (x.it.timer && x.it.timer.enabled ? x.it.timer.seconds : 0), 0);
  const over = st.session && total > st.session.duration_minutes;
  toolbar.innerHTML =
    `<button type="button" class="button-surface" data-reimport>${icon('refresh-cw')} ${st.deck && st.deck.slides.length ? 'Re-import PowerPoint' : 'Import PowerPoint'}</button>` +
    `<button type="button" class="button-surface" data-add-section>${icon('plus')} Add section</button>` +
    `<span class="plan-fold"><button type="button" class="button-ghost" data-fold="collapse" title="Collapse all sections">${icon('chevrons-down-up')} Collapse all</button>` +
    `<button type="button" class="button-ghost" data-fold="expand" title="Expand all sections">${icon('chevrons-up-down')} Expand all</button></span>` +
    `<span class="plan-totals ${over ? 'over' : ''}">Planned ${fmtMinutes(total)} of ${fmtMinutes(st.session ? st.session.duration_minutes : 0)}` +
    `${timers ? ` · ${Math.round(timers / 60)} min on timers` : ''}</span>` +
    (st.dirty ? `<span class="dirty-bar"><span class="chip warn">Unsaved changes</span>` +
      `<button type="button" class="button-ghost" data-discard>Discard</button>` +
      `<button type="button" class="button-primary save-small" data-save>Save</button></span>` : '');
  toolbar.querySelector('[data-reimport]').addEventListener('click', reimport);
  toolbar.querySelector('[data-add-section]').addEventListener('click', () => addSection());
  toolbar.querySelectorAll('[data-fold]').forEach((b) => b.addEventListener('click', () => {
    st.collapsed = new Set(b.dataset.fold === 'collapse' ? secs.map((x) => x.id) : []);
    renderList();
  }));
  const save = toolbar.querySelector('[data-save]');
  if (save) save.addEventListener('click', saveSession);
  const discard = toolbar.querySelector('[data-discard]');
  if (discard) discard.addEventListener('click', load);
}

// ---------------------------------------------------------------- list

function renderList() {
  listCard.innerHTML = '';
  const secs = st.session.sections || [];
  if (!secs.length) {
    listCard.appendChild(emptyStateEl('upload', 'Import the PowerPoint to start the plan.', { actionLabel: 'Import PowerPoint', onAction: reimport }));
    return;
  }
  let n = 0;
  secs.forEach((sec, si) => {
    const collapsed = st.collapsed.has(sec.id);
    const h = document.createElement('div');
    h.className = 'sec-row';
    h.dataset.sec = sec.id;
    h.draggable = true;
    h.innerHTML =
      `<span class="grip" aria-hidden="true">${icon('grip-vertical')}</span>` +
      `<button type="button" class="sec-toggle" aria-expanded="${!collapsed}" aria-label="${collapsed ? 'Expand' : 'Collapse'} ${esc(sec.name)}">${icon(collapsed ? 'chevron-right' : 'chevron-down')}</button>` +
      `<button type="button" class="sec-name" title="Rename">${esc(sec.name)}</button>` +
      `<span class="sec-meta">${sec.items.length} item${sec.items.length === 1 ? '' : 's'}${collapsed ? ' · collapsed' : ''}</span>` +
      `<label class="sec-min"><input type="number" min="0" max="1440" value="${sec.minutes}" aria-label="Planned minutes for ${esc(sec.name)}"><span>min</span></label>` +
      `<button type="button" class="kebab hit-target" aria-label="Section actions">${icon('ellipsis-vertical')}</button>`;
    h.querySelector('.sec-toggle').addEventListener('click', () => {
      if (collapsed) st.collapsed.delete(sec.id); else st.collapsed.add(sec.id);
      renderList();
    });
    h.querySelector('.sec-name').addEventListener('click', () => renameSection(sec));
    h.querySelector('.sec-min input').addEventListener('change', (e) => {
      sec.minutes = Math.max(0, parseInt(e.target.value, 10) || 0);
      markDirty();
      setStatus(head, `${secs.length} sections · ${fmtMinutes(secs.reduce((a, s) => a + (s.minutes || 0), 0))}`);
    });
    h.querySelector('.kebab').addEventListener('click', (e) => {
      e.stopPropagation();
      rowMenu(e.currentTarget, [
        { label: 'Rename', icon: 'pencil', onClick: () => renameSection(sec) },
        { label: 'Add an activity here', icon: 'plus', onClick: () => addItem(si, 'activity') },
        { label: 'Move up', icon: 'chevron-up', onClick: () => moveSection(si, -1) },
        { label: 'Move down', icon: 'chevron-down', onClick: () => moveSection(si, 1) },
        { label: 'Delete section', icon: 'trash-2', danger: true, onClick: () => deleteSection(si) },
      ]);
    });
    wireDrop(h, { sec: si, index: sec.items.length, header: true });
    h.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/fs-sec', String(si)); e.dataTransfer.effectAllowed = 'move'; });
    listCard.appendChild(h);

    sec.items.forEach((it, ii) => {
      n += 1;
      if (collapsed) return;
      const row = document.createElement('div');
      row.className = 'item-row' + (st.picked.has(it.id) ? ' selected' : '') + (it.id === st.selected && st.picked.size > 1 ? ' focused' : '') + (it.include === false ? ' excluded' : '');
      row.dataset.item = it.id;
      row.tabIndex = 0;
      row.draggable = true;
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(st.picked.has(it.id)));
      row.innerHTML =
        `<span class="num">${n}</span>${thumbHtml(it)}` +
        `<span class="item-title">${esc(labelOf(it))}</span><span class="chips">${chipHtml(it)}</span>`;
      row.addEventListener('mousedown', (e) => { if (e.shiftKey) e.preventDefault(); }); // no text selection on Shift+click
      row.addEventListener('click', (e) => clickItem(it.id, e));
      row.addEventListener('keydown', (e) => rowKey(it.id, e));
      row.addEventListener('dragstart', (e) => {
        // Dragging a selected row carries the whole selection; any other row goes alone.
        if (!st.picked.has(it.id)) select(it.id);
        e.dataTransfer.setData('text/fs-item', it.id);
        e.dataTransfer.effectAllowed = 'move';
        listCard.querySelectorAll('.item-row.selected').forEach((r) => r.classList.add('dragging'));
      });
      row.addEventListener('dragend', () => listCard.querySelectorAll('.item-row.dragging').forEach((r) => r.classList.remove('dragging')));
      wireDrop(row, { sec: si, index: ii });
      listCard.appendChild(row);
    });
    if (!collapsed) {
      const add = document.createElement('button');
      add.type = 'button';
      add.className = 'add-row';
      add.innerHTML = `${icon('plus')} Add slide or activity`;
      add.addEventListener('click', (e) => {
        rowMenu(e.currentTarget, [
          { label: 'Activity', icon: 'message-square', onClick: () => addItem(si, 'activity') },
          { label: 'Break', icon: 'coffee', onClick: () => addItem(si, 'break') },
          { label: 'Slide from the deck', icon: 'image', onClick: () => addSlide(si) },
          { label: 'Section after this one', icon: 'list-plus', onClick: () => addSection(si + 1) },
        ]);
      });
      wireDrop(add, { sec: si, index: sec.items.length });
      listCard.appendChild(add);
    }
  });
}

function wireDrop(el, target) {
  el.addEventListener('dragover', (e) => {
    const types = e.dataTransfer.types;
    if (types.includes('text/fs-item') || (target.header && types.includes('text/fs-sec'))) {
      e.preventDefault();
      el.classList.add('drop-target');
    }
  });
  el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
  el.addEventListener('drop', (e) => {
    el.classList.remove('drop-target');
    const itemId = e.dataTransfer.getData('text/fs-item');
    const secIdx = e.dataTransfer.getData('text/fs-sec');
    e.preventDefault();
    if (itemId) {
      const r = el.getBoundingClientRect();
      const after = !target.header && e.clientY > r.top + r.height / 2;
      dropItem(itemId, target.sec, target.index + (after ? 1 : 0), target.header);
    } else if (secIdx !== '') {
      const from = parseInt(secIdx, 10);
      const [moved] = st.session.sections.splice(from, 1);
      st.session.sections.splice(target.sec > from ? target.sec - 1 : target.sec, 0, moved);
      markDirty();
      renderList();
    }
  });
}

function dropItem(id, secIdx, index, toEnd) {
  if (!findItem(id)) return;
  const ids = st.picked.has(id) ? allItems().filter((x) => st.picked.has(x.it.id)).map((x) => x.it.id) : [id];
  const dest = st.session.sections[secIdx];
  // Insert before the first unmoved item at or after the drop point (none = the section's end).
  const before = toEnd ? null : dest.items.slice(index).find((x) => !ids.includes(x.id)) || null;
  const moving = ids.map((x) => { const f = findItem(x); return f.sec.items.splice(f.ii, 1)[0]; });
  const at = before ? dest.items.indexOf(before) : dest.items.length;
  dest.items.splice(at, 0, ...moving);
  markDirty();
  renderList();
  renderEditor();
}

function moveItem(id, dir, keepFocus = false) {
  const flat = allItems();
  const pos = flat.findIndex((x) => x.it.id === id);
  if (pos < 0) return;
  const cur = flat[pos];
  const secs = st.session.sections;
  cur.sec.items.splice(cur.ii, 1);
  if (dir < 0) {
    if (cur.ii > 0) cur.sec.items.splice(cur.ii - 1, 0, cur.it);
    else if (cur.si > 0) secs[cur.si - 1].items.push(cur.it);
    else cur.sec.items.unshift(cur.it);
  } else if (cur.ii < cur.sec.items.length) {
    cur.sec.items.splice(cur.ii + 1, 0, cur.it);
  } else if (cur.si < secs.length - 1) {
    secs[cur.si + 1].items.unshift(cur.it);
  } else {
    cur.sec.items.push(cur.it);
  }
  markDirty();
  renderList();
  renderEditor();
  if (keepFocus) {
    const row = listCard.querySelector(`[data-item="${id}"]`);
    if (row) row.focus();
  }
}

function moveSection(si, dir) {
  const secs = st.session.sections;
  const to = si + dir;
  if (to < 0 || to >= secs.length) return;
  const [s] = secs.splice(si, 1);
  secs.splice(to, 0, s);
  markDirty();
  renderList();
}

/** Rows in list order (collapsed sections hide theirs, as in a file manager). */
function visibleIds() { return [...listCard.querySelectorAll('.item-row')].map((r) => r.dataset.item); }

function paintSelection() {
  listCard.querySelectorAll('.item-row').forEach((r) => {
    const on = st.picked.has(r.dataset.item);
    r.classList.toggle('selected', on);
    r.classList.toggle('focused', st.picked.size > 1 && r.dataset.item === st.selected);
    r.setAttribute('aria-selected', String(on));
  });
  renderEditor();
}

function select(id) {
  st.selected = id;
  st.anchor = id;
  st.picked = new Set(id ? [id] : []);
  paintSelection();
}

function selectRange(id, add) {
  const ids = visibleIds();
  const from = ids.indexOf(st.anchor && ids.includes(st.anchor) ? st.anchor : st.selected);
  const to = ids.indexOf(id);
  if (from < 0 || to < 0) { select(id); return; }
  const range = ids.slice(Math.min(from, to), Math.max(from, to) + 1);
  st.picked = new Set(add ? [...st.picked, ...range] : range);
  st.selected = id;
  paintSelection();
}

function clickItem(id, e) {
  if (e.shiftKey) { selectRange(id, e.ctrlKey || e.metaKey); return; }
  if (e.ctrlKey || e.metaKey) {
    if (st.picked.has(id) && st.picked.size > 1) {
      st.picked.delete(id);
      if (st.selected === id) st.selected = [...st.picked].pop();
    } else {
      st.picked.add(id);
      st.selected = id;
    }
    st.anchor = id;
    paintSelection();
    return;
  }
  select(id);
}

function focusRow(id) {
  const row = listCard.querySelector(`[data-item="${CSS.escape(id)}"]`);
  if (row) row.focus();
}

function rowKey(id, e) {
  const mod = e.ctrlKey || e.metaKey;
  if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); moveItem(id, e.key === 'ArrowUp' ? -1 : 1, true); return; }
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); clickItem(id, e); return; }
  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    const ids = visibleIds();
    const next = ids[ids.indexOf(id) + (e.key === 'ArrowUp' ? -1 : 1)];
    if (!next) return;
    e.preventDefault();
    if (e.shiftKey) selectRange(next, false); else select(next);
    focusRow(next);
    return;
  }
  if (mod && e.key.toLowerCase() === 'a') {
    e.preventDefault();
    st.picked = new Set(visibleIds());
    paintSelection();
    return;
  }
  if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); duplicateItems([...st.picked]); return; }
  if (e.key === 'Escape' && st.picked.size > 1) { e.preventDefault(); select(st.selected); focusRow(st.selected); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteItems([...st.picked]); }
}

// ---------------------------------------------------------------- sections + items

async function renameSection(sec) {
  const v = await formDialog({ title: 'Rename section', fields: [{ name: 'name', label: 'Name', value: sec.name, required: true }] });
  if (!v) return;
  sec.name = v.name.trim();
  markDirty();
  render();
}

/** A new empty section at `at` (default: the end of the plan). */
async function addSection(at) {
  const v = await formDialog({
    title: 'Add section',
    fields: [
      { name: 'name', label: 'Name', required: true, placeholder: 'Working agreement' },
      { name: 'minutes', label: 'Planned minutes', type: 'number', value: 10 },
    ],
  });
  if (!v) return;
  const secs = st.session.sections;
  secs.splice(at == null ? secs.length : at, 0, { id: newId('sec'), name: v.name.trim(), minutes: Math.max(0, parseInt(v.minutes, 10) || 0), items: [] });
  markDirty();
  render();
}

async function deleteSection(si) {
  const sec = st.session.sections[si];
  const target = si > 0 ? st.session.sections[si - 1] : st.session.sections[si + 1];
  if (sec.items.length && !target) { toast('The last section cannot be deleted while it holds items', 'error'); return; }
  const ok = await confirmDialog({
    title: 'Delete section?',
    message: sec.items.length ? `Its ${sec.items.length} items move to "${target.name}".` : 'The section is empty.',
    actionLabel: 'Delete section', danger: true,
  });
  if (!ok) return;
  if (sec.items.length) {
    if (si > 0) target.items.push(...sec.items); else target.items.unshift(...sec.items);
  }
  st.session.sections.splice(si, 1);
  markDirty();
  render();
}

function addItem(si, kind) {
  const sec = st.session.sections[si];
  const item = kind === 'activity'
    ? { kind: 'activity', id: newId('act'), type: 'word_cloud', question: '', chat_prompt: '', profile: 'camera_pip', options: {} }
    : { kind: 'break', id: newId('brk'), title: 'Break', profile: 'camera_strip' };
  const selectedPos = sec.items.findIndex((x) => x.id === st.selected);
  sec.items.splice(selectedPos >= 0 ? selectedPos + 1 : sec.items.length, 0, item);
  st.selected = st.anchor = item.id;
  st.picked = new Set([item.id]);
  markDirty();
  render();
}

async function addSlide(si) {
  if (!st.deck || !st.deck.slides.length) { toast('Import the PowerPoint first', 'error'); return; }
  const used = new Set(allItems().filter((x) => x.it.kind === 'slide').map((x) => x.it.slide_id));
  const options = st.deck.slides.map((s) => ({ value: s.slide_id, label: `${s.index}. ${s.title}${used.has(s.slide_id) ? ' (already in the plan)' : ''}` }));
  const firstFree = st.deck.slides.find((s) => !used.has(s.slide_id));
  const v = await formDialog({
    title: 'Add a slide',
    fields: [{ name: 'slide', label: 'Slide', type: 'select', options, value: firstFree ? firstFree.slide_id : options[0].value }],
    saveLabel: 'Add',
  });
  if (!v) return;
  const s = st.slides.get(parseInt(v.slide, 10));
  const id = used.has(s.slide_id) ? newId('slide') : `slide-${s.slide_id}`;
  st.session.sections[si].items.push({ kind: 'slide', id, slide_id: s.slide_id, profile: s.profile });
  st.selected = st.anchor = id;
  st.picked = new Set([id]);
  markDirty();
  render();
}

/** Copies of `ids` (in plan order), right after the last of them; the copies become the selection. */
function duplicateItems(ids) {
  const flat = allItems().filter((x) => ids.includes(x.it.id));
  if (!flat.length) return;
  const last = flat[flat.length - 1];
  const copies = flat.map((x) => Object.assign(clone(x.it), { id: newId({ slide: 'slide', activity: 'act', break: 'brk' }[x.it.kind] || 'item') }));
  last.sec.items.splice(last.sec.items.indexOf(last.it) + 1, 0, ...copies);
  st.picked = new Set(copies.map((c) => c.id));
  st.selected = st.anchor = copies[copies.length - 1].id;
  markDirty();
  render();
  toast(copies.length === 1 ? 'Duplicated — the copy is selected' : `${copies.length} items duplicated — the copies are selected`);
}

async function deleteItems(ids) {
  const flat = allItems();
  const gone = flat.filter((x) => ids.includes(x.it.id));
  if (!gone.length) return;
  const one = gone.length === 1;
  const slides = gone.filter((x) => x.it.kind === 'slide').length;
  const kept = !slides ? '' : one
    ? ' It stays in the deck: "Add slide or activity → Slide from the deck" brings it back.'
    : ' Slides stay in the deck and can be added back.';
  const names = gone.slice(0, 4).map((x) => `"${labelOf(x.it)}"`).join(', ') + (gone.length > 4 ? ` and ${gone.length - 4} more` : '');
  const ok = await confirmDialog({
    title: one ? `Delete this ${gone[0].it.kind}?` : `Delete ${gone.length} items?`,
    message: `${names} ${one ? 'leaves' : 'leave'} the plan when you save.${kept}`,
    actionLabel: one ? 'Delete' : `Delete ${gone.length} items`, danger: true,
  });
  if (!ok) return;
  // Focus what follows the last deleted item (else what precedes the first).
  const last = flat.indexOf(gone[gone.length - 1]);
  const next = flat.slice(last + 1).find((x) => !ids.includes(x.it.id)) || flat.slice(0, flat.indexOf(gone[0])).reverse()[0];
  gone.forEach((x) => x.sec.items.splice(x.sec.items.indexOf(x.it), 1));
  st.selected = st.anchor = next ? next.it.id : null;
  st.picked = new Set(st.selected ? [st.selected] : []);
  markDirty();
  render();
}

async function reimport() {
  if (st.dirty) {
    const ok = await confirmDialog({ title: 'Unsaved changes', message: 'Save or discard your plan edits before importing.', actionLabel: 'Discard edits and continue', danger: true });
    if (!ok) return;
  }
  const had = st.deck && st.deck.slides.length > 0;
  const done = await importDialog(st.sid, { lastPath: (st.session.source && st.session.source.pptx) || '', reimport: had });
  if (!done) return;
  st.dirty = false;
  if (done.review) ctx.reviewFor = st.sid;
  await load();
}

async function saveSession() {
  try {
    const res = await api(`/api/sessions/${st.sid}`, { method: 'PUT', body: { session: st.session } });
    st.session = res.session;
    st.dirty = false;
    toast('Saved to session.yaml');
    render();
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ---------------------------------------------------------------- editor

function field(label, control, extraClass = '') {
  const row = document.createElement('div');
  row.className = 'ed-row ' + extraClass;
  const l = document.createElement('div');
  l.className = 'ed-label';
  l.textContent = label;
  const c = document.createElement('div');
  c.className = 'ed-control';
  if (typeof control === 'string') c.innerHTML = control; else c.appendChild(control);
  row.append(l, c);
  return row;
}

function rangeTabs(options, value, onPick, label) {
  const nav = document.createElement('div');
  nav.className = 'range-tabs ed-tabs';
  nav.setAttribute('role', 'group');
  nav.setAttribute('aria-label', label);
  options.forEach(([v, text]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'range-tab' + (v === value ? ' active' : '');
    b.textContent = text;
    b.setAttribute('aria-pressed', String(v === value));
    b.addEventListener('click', () => onPick(v));
    nav.appendChild(b);
  });
  return nav;
}

function input(value, onChange, attrs = {}) {
  const el = document.createElement(attrs.textarea ? 'textarea' : 'input');
  el.className = 'input';
  if (!attrs.textarea) el.type = attrs.type || 'text';
  el.value = value == null ? '' : value;
  Object.entries(attrs).forEach(([k, v]) => { if (k !== 'textarea' && k !== 'type') el.setAttribute(k, v); });
  el.addEventListener('input', () => onChange(el.value, el));
  return el;
}

function switchRow(on, text, onToggle, labelText) {
  const wrap = document.createElement('div');
  wrap.className = 'switch-line';
  const sw = switchEl(on, { label: labelText || text, onToggle: (next, btn) => { onToggle(next); btn.className = 'toggle' + (next ? ' on' : ''); btn.setAttribute('aria-checked', String(next)); } });
  const t = document.createElement('span');
  t.className = 'muted small';
  t.textContent = text;
  wrap.append(sw, t);
  return wrap;
}

function renderEditor() {
  editor.innerHTML = '';
  if (!st.session) return;
  if (st.picked.size > 1) { renderBulk(); return; }
  const found = st.selected ? findItem(st.selected) : null;
  if (!found) {
    const c = document.createElement('div');
    c.className = 'card';
    c.appendChild(emptyStateEl('presentation', allItems().length ? 'Select an item to edit it.' : 'The plan is empty.'));
    editor.appendChild(c);
    return;
  }
  const { it, sec } = found;
  const flat = allItems();
  const pos = flat.findIndex((x) => x.it.id === it.id);
  const prev = pos > 0 ? labelOf(flat[pos - 1].it) : null;

  const title = document.createElement('div');
  title.className = 'detail-title';
  title.innerHTML = `<h1>${esc(labelOf(it))}</h1><p class="muted">${esc(sec.name)} · item ${pos + 1}${prev ? ` · after “${esc(prev)}”` : ''}</p>`;
  editor.appendChild(title);

  editor.appendChild(previewCard(it));

  const form = document.createElement('div');
  form.className = 'card ed-card';
  editor.appendChild(form);
  const rerenderRow = () => { renderList(); title.querySelector('h1').textContent = labelOf(it); };

  if (it.kind === 'activity') {
    const types = Object.values(st.types).map((t) => [t.type, t.label]);
    const extra = types.slice(5).find(([t]) => t === it.type);
    const moreTab = types.length > 5 ? [[extra ? it.type : '__more', extra ? `${extra[1]} ▾` : 'More…']] : [];
    form.appendChild(field('Type', rangeTabs(types.slice(0, 5).concat(moreTab), it.type, (v) => {
      if (v === it.type && extra) v = '__more';
      if (v === '__more') {
        rowMenu(form.querySelector('.ed-tabs .range-tab:last-child'), types.slice(5).map(([t, l]) => ({ label: l, icon: (st.types[t] || {}).icon, onClick: () => { it.type = t; it.options = {}; markDirty(); renderList(); renderEditor(); } })));
        return;
      }
      it.type = v;
      it.options = {};
      markDirty();
      renderList();
      renderEditor();
    }, 'Activity type')));
    const spec = st.types[it.type] || {};
    form.appendChild(field('Title', input(it.title, (v) => { it.title = v; markDirty(); rerenderRow(); }, { placeholder: spec.capture !== false ? oneLine(it.question) || spec.label : spec.label }), 'with-hint'));
    form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', spec.capture !== false
      ? '<p class="ed-hint">The name in the plan and on the presenter. Empty = the question.</p>'
      : '<p class="ed-hint">Shown on the stage — type \\n for a line break there.</p>');
    if (spec.capture !== false) {
      form.appendChild(field('Question', input(it.question, (v) => { it.question = v; markDirty(); rerenderRow(); }, { placeholder: 'What did you learn about this group?' }), 'with-hint'));
      form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', '<p class="ed-hint">Type \\n where the line should break on the stage.</p>');
      const font = it.font || { family: 'theme', size_px: 72 };
      const fam = document.createElement('select');
      fam.className = 'select-native';
      fam.setAttribute('aria-label', 'Question font');
      FONTS.forEach(([v, l]) => { const o = document.createElement('option'); o.value = v; o.textContent = l; o.selected = v === (font.family === 'Patrick Hand' ? 'theme' : font.family); fam.appendChild(o); });
      fam.addEventListener('change', () => { it.font = Object.assign({}, it.font || font, { family: fam.value }); markDirty(); updatePreview(it); });
      const size = input(font.size_px, (v) => { const n = parseInt(v, 10); if (n >= 12 && n <= 240) { it.font = Object.assign({}, it.font || font, { size_px: n }); markDirty(); updatePreview(it); } }, { type: 'number', min: '12', max: '240', 'aria-label': 'Font size in stage px (1920 wide)' });
      size.classList.add('size-input');
      const fontRow = document.createElement('div');
      fontRow.className = 'inline-controls';
      fontRow.append(fam, size, Object.assign(document.createElement('span'), { className: 'muted small', textContent: 'px' }));
      form.appendChild(field('Question font', fontRow));
      form.appendChild(field('Prompt for chat', input(it.chat_prompt, (v) => { it.chat_prompt = v; markDirty(); }, { placeholder: spec.chat_prompt_hint || 'One or two words: …' })));
    }
  } else {
    const placeholder = it.kind === 'slide' ? (slideOf(it) || {}).title || '' : 'Break';
    form.appendChild(field('Title', input(it.title, (v) => { it.title = v; markDirty(); rerenderRow(); }, { placeholder }), 'with-hint'));
    const hint = document.createElement('p');
    hint.className = 'ed-hint';
    hint.textContent = it.kind === 'slide' ? 'Shown on the presenter as "next". Empty = the slide\'s own title.' : 'Shown on the presenter and on the stage — type \\n for a line break there.';
    form.lastChild.querySelector('.ed-control').appendChild(hint);
  }

  form.appendChild(field('OBS profile', rangeTabs(PROFILES, it.profile, (v) => { it.profile = v; markDirty(); renderList(); renderEditor(); }, 'OBS profile')));
  if (it.kind === 'slide' && it.profile == null) {
    form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', '<p class="ed-hint warn">Detection was unsure for this slide — pick one.</p>');
  } else if (it.kind === 'slide') {
    const s = slideOf(it);
    if (s && s.profile && s.profile !== it.profile) {
      form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', `<p class="ed-hint">Detected: ${esc(PROFILES.find((p) => p[0] === s.profile)[1])}</p>`);
    }
  }

  form.appendChild(timerField(it));

  form.appendChild(field('In this session', switchRow(it.include !== false, 'Off = skipped live, kept in the plan', (next) => {
    it.include = next;
    markDirty();
    renderList();
  }, 'In this session')));

  if (it.kind === 'activity') {
    const spec = st.types[it.type] || {};
    const opts = spec.options || [];
    if (opts.length) {
      const box = document.createElement('div');
      box.className = 'opt-list';
      it.options = it.options || {};
      opts.forEach((o) => {
        const cur = it.options[o.key] !== undefined ? it.options[o.key] : o.default;
        const setOpt = (v) => { it.options[o.key] = v; markDirty(); };
        if (o.kind === 'switch') {
          box.appendChild(switchRow(!!cur, o.label, setOpt));
        } else if (o.kind === 'select') {
          const sel = document.createElement('select');
          sel.className = 'select-native';
          sel.setAttribute('aria-label', o.label);
          o.choices.forEach(([v, l]) => { const op = document.createElement('option'); op.value = v; op.textContent = l; op.selected = v === cur; sel.appendChild(op); });
          sel.addEventListener('change', () => setOpt(sel.value));
          const line = document.createElement('label');
          line.className = 'opt-line';
          line.append(Object.assign(document.createElement('span'), { className: 'small', textContent: o.label }), sel);
          box.appendChild(line);
        } else {
          const el = input(cur, (v) => setOpt(o.kind === 'number' ? (v === '' ? o.default : Number(v)) : v), { type: o.kind === 'number' ? 'number' : 'text', placeholder: o.placeholder || '', 'aria-label': o.label });
          const line = document.createElement('label');
          line.className = 'opt-line';
          line.append(Object.assign(document.createElement('span'), { className: 'small', textContent: o.label }), el);
          box.appendChild(line);
        }
      });
      form.appendChild(field('Answers', box));
    }
  }

  form.appendChild(notesField(it));

  const tools = document.createElement('div');
  tools.className = 'ed-tools';
  tools.innerHTML =
    `<button type="button" class="button-surface" data-up>${icon('chevron-up')} Move up</button>` +
    `<button type="button" class="button-surface" data-down>${icon('chevron-down')} Move down</button>` +
    `<button type="button" class="button-surface" data-dup title="Ctrl+D">${icon('copy-plus')} Duplicate</button>` +
    `<button type="button" class="button-tint danger" data-delete>${icon('trash-2')} Delete</button>`;
  tools.querySelector('[data-dup]').addEventListener('click', () => duplicateItems([it.id]));
  tools.querySelector('[data-up]').addEventListener('click', () => moveItem(it.id, -1));
  tools.querySelector('[data-down]').addEventListener('click', () => moveItem(it.id, 1));
  tools.querySelector('[data-delete]').addEventListener('click', () => deleteItems([it.id]));
  form.appendChild(tools);

  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'button-primary ed-save';
  save.textContent = st.dirty ? 'Save to session.yaml' : 'Saved';
  save.disabled = !st.dirty;
  save.addEventListener('click', saveSession);
  form.appendChild(save);
  const observer = () => { save.disabled = !st.dirty; save.textContent = st.dirty ? 'Save to session.yaml' : 'Saved'; };
  form.addEventListener('input', observer);
  form.addEventListener('click', () => setTimeout(observer, 0));
}

/** Several items selected: what they are, and what can be set on all of them at once. */
function renderBulk() {
  const items = allItems().filter((x) => st.picked.has(x.it.id)).map((x) => x.it);
  const count = (kind, one, many) => { const n = items.filter((it) => it.kind === kind).length; return n ? `${n} ${n === 1 ? one : many}` : ''; };
  const title = document.createElement('div');
  title.className = 'detail-title';
  title.innerHTML = `<h1>${items.length} items selected</h1><p class="muted">` +
    [count('slide', 'slide', 'slides'), count('activity', 'activity', 'activities'), count('break', 'break', 'breaks')].filter(Boolean).join(' · ') +
    ' · Ctrl+click adds or removes one, Shift+click a range, Esc keeps one</p>';
  editor.appendChild(title);

  const form = document.createElement('div');
  form.className = 'card ed-card bulk-card';
  editor.appendChild(form);
  const strip = document.createElement('div');
  strip.className = 'bulk-list';
  strip.innerHTML = items.map((it) => `<div class="bulk-row">${thumbHtml(it)}<span class="item-title">${esc(labelOf(it))}</span></div>`).join('');
  form.appendChild(field('Selected', strip));

  const profiles = new Set(items.map((it) => it.profile || null));
  form.appendChild(field('OBS profile', rangeTabs(PROFILES, profiles.size === 1 ? [...profiles][0] : null, (v) => {
    items.forEach((it) => { it.profile = v; });
    markDirty();
    renderList();
    renderEditor();
  }, 'OBS profile for the selected items')));
  if (profiles.size > 1) form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', '<p class="ed-hint">They differ now — a pick sets all of them.</p>');

  const allIn = items.every((it) => it.include !== false);
  form.appendChild(field('In this session', switchRow(allIn, allIn ? 'All in · off = skip them live' : 'Some are skipped · on = all in', (next) => {
    items.forEach((it) => { it.include = next; });
    markDirty();
    renderList();
    renderEditor();
  }, 'In this session, for the selected items')));

  const tools = document.createElement('div');
  tools.className = 'ed-tools';
  tools.innerHTML =
    `<button type="button" class="button-surface" data-clear>${icon('x')} Keep one selected</button>` +
    `<button type="button" class="button-surface" data-dup title="Ctrl+D">${icon('copy-plus')} Duplicate ${items.length} items</button>` +
    `<button type="button" class="button-tint danger" data-delete>${icon('trash-2')} Delete ${items.length} items</button>`;
  tools.querySelector('[data-clear]').addEventListener('click', () => select(st.selected));
  tools.querySelector('[data-dup]').addEventListener('click', () => duplicateItems(items.map((it) => it.id)));
  tools.querySelector('[data-delete]').addEventListener('click', () => deleteItems(items.map((it) => it.id)));
  form.appendChild(tools);

  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'button-primary ed-save';
  save.textContent = st.dirty ? 'Save to session.yaml' : 'Saved';
  save.disabled = !st.dirty;
  save.addEventListener('click', saveSession);
  form.appendChild(save);
}

/** The presenter's notes. A slide starts from its PowerPoint notes; editing them keeps the deck as it is. */
function notesField(it) {
  const deck = it.kind === 'slide' ? ((slideOf(it) || {}).notes || '') : '';
  const box = input(it.notes || deck, (v) => { it.notes = v === deck ? '' : v; markDirty(); }, { textarea: true, rows: '4', 'aria-label': 'Notes',
    placeholder: it.kind === 'slide' ? 'No speaker notes in the deck — write yours here' : 'What to say or do on this item' });
  box.classList.add('notes-input');
  const row = field('Notes', box, 'with-hint');
  row.querySelector('.ed-control').insertAdjacentHTML('beforeend', `<p class="ed-hint">${it.kind === 'slide'
    ? (it.notes ? 'Edited here — the PowerPoint notes stay in the deck. Clear the box to get them back.' : 'From your PowerPoint. Editing them here leaves the deck as it is.')
    : 'Shown on the presenter while this item is on stage.'}</p>`);
  return row;
}

function timerField(it) {
  const wrap = document.createElement('div');
  wrap.className = 'timer-box';
  const on = !!(it.timer && it.timer.enabled);
  const isAct = it.kind === 'activity';
  wrap.appendChild(switchRow(on, on ? '' : 'No timer on this item', (next) => {
    if (next) {
      it.timer = Object.assign({ seconds: isAct ? 180 : it.kind === 'break' ? 600 : 120, start: isAct ? 'with_capture' : 'manual', show_on: isAct || it.kind === 'break' ? 'stage' : 'presenter', end: isAct ? 'stop_capture' : 'keep' }, it.timer || {}, { enabled: true });
    } else if (it.timer) {
      it.timer.enabled = false;
    }
    markDirty();
    renderList();
    renderEditor();
  }, 'Timer on this item'));
  if (on) {
    const t = it.timer;
    const dur = input(fmtTimer(t.seconds), (v, el) => {
      const s = parseTimer(v);
      el.classList.toggle('invalid', s == null || s < 5);
      if (s != null && s >= 5) { t.seconds = s; markDirty(); }
    }, { 'aria-label': 'Timer duration (m:ss)', placeholder: '3:00' });
    dur.classList.add('dur-input');
    wrap.querySelector('.switch-line').appendChild(dur);
    const grid = document.createElement('div');
    grid.className = 'timer-grid';
    const sel = (label, map, value, onPick, skip = []) => {
      const s = document.createElement('select');
      s.className = 'select-native';
      s.setAttribute('aria-label', label);
      Object.entries(map).filter(([k]) => !skip.includes(k)).forEach(([k, l]) => { const o = document.createElement('option'); o.value = k; o.textContent = l; o.selected = k === value; s.appendChild(o); });
      s.addEventListener('change', () => { onPick(s.value); markDirty(); });
      const line = document.createElement('label');
      line.className = 'opt-line';
      line.append(Object.assign(document.createElement('span'), { className: 'small', textContent: label }), s);
      return line;
    };
    grid.appendChild(sel('Starts', TIMER_START, t.start, (v) => { t.start = v; }, isAct ? [] : ['with_capture']));
    grid.appendChild(sel('Shows on', { stage: 'Stage', presenter: 'Presenter only', both: 'Stage and presenter' }, t.show_on, (v) => { t.show_on = v; }));
    grid.appendChild(sel('At the end', TIMER_END, t.end, (v) => { t.end = v; }, isAct ? [] : ['stop_capture']));
    wrap.appendChild(grid);
  }
  return field('Timer', wrap);
}

function previewCard(it) {
  const card = document.createElement('div');
  card.className = 'card preview-card';
  card.innerHTML =
    `<div class="preview-frame stage-host" data-preview></div>` +
    `<div class="preview-text"><b>Stage preview</b><p class="muted small">${it.kind === 'slide'
      ? 'The slide as imported. The dashed box is where the camera goes for the chosen profile.'
      : it.kind === 'activity'
        ? 'Drawn by the real stage with sample answers, at the size Zoom will see it. The dashed box is the camera.'
        : 'The break as the stage shows it.'}</p></div>`;
  setTimeout(() => updatePreview(it), 0);
  return card;
}

const ZONES = {
  camera_strip: [0.583, 0.23, 0.983, 0.77],
  camera_pip: [0.72, 0.04, 0.98, 0.3],
  screen_only: null,
};

let pv = null; // { frame, stage } — rebuilt when the editor re-renders its preview frame
const previewResults = new Map();
let previewTimer = null;

/** A plan item as the live run shows it (src/live/plan.py), for the stage renderer. */
function runItem(it) {
  const s = slideOf(it);
  const profile = it.profile || (s && s.profile) || 'screen_only';
  const spec = st.types[it.type] || {};
  return {
    id: it.id, kind: it.kind, type: it.type || null, type_label: spec.label,
    capture: it.kind === 'activity' && spec.capture !== false,
    title: titleOf(it), question: it.question || (it.kind === 'activity' ? 'Your question here' : ''),
    font: it.font || { family: 'theme', size_px: 72 }, options: it.options || {},
    profile, zone: st.zones && profile in st.zones ? st.zones[profile] : ZONES[profile], slide_file: s ? s.file : null,
    timer: it.timer && it.timer.enabled !== false ? it.timer : null,
  };
}

function updatePreview(it) {
  const frame = editor.querySelector('[data-preview]');
  if (!frame) return;
  if (!pv || pv.frame !== frame) pv = { frame, stage: createStage(frame, { guides: true, slideGuides: true }) };
  const item = runItem(it);
  const plan = { rev: 0, active: true, session: { id: st.sid }, run: { chat_hint: st.session.chat_hint, theme: st.session.theme } };
  const opts = Object.assign({}, ...((st.types[item.type] || {}).options || []).map((o) => ({ [o.key]: o.default })), item.options);
  const key = JSON.stringify([item.type, item.options]);
  const draw = () => pv.stage.render(item, {
    plan, state: { timers: {}, blackout: false }, now: Date.now(),
    names: !!opts.show_names, result: previewResults.get(key) || null,
  });
  draw();
  if (!item.capture || previewResults.has(key)) return;
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    try {
      const res = await api(`/api/activities/${encodeURIComponent(item.type)}/preview`, { method: 'POST', body: { options: item.options } });
      previewResults.set(key, res.result);
      if (pv && pv.frame === frame && frame.isConnected) draw();
    } catch (e) { /* the preview stays without sample answers */ }
  }, 250);
}
