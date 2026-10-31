// Plan tab: sections with planned minutes, and slides / activities / breaks in
// order (left), the selected item's editor (right). Edits are staged in memory;
// "Save to session.yaml" is the one persistence boundary.
//
// Selection works as in a file manager: click selects one item, Ctrl/Cmd+click
// adds or removes one, Shift+click selects the range from the last clicked
// (Ctrl+Shift adds it); Shift+Up/Down extend, Ctrl+A selects all, Ctrl+D
// duplicates, Delete deletes, Esc keeps only the focused item. With several selected, the editor
// becomes the bulk panel and dragging any of them moves them all.
//
// The editor's fields (font, roles, timer, music, notes, and the rows they sit in) are in plan-fields.js.
// Deck re-import and Kahoot import are in plan-import.js.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, oneLine, pageHead, setStatus, toast, fmtMinutes, switchRow } from '/static/js/ui.js';
import { formDialog, confirmDialog, rowMenu } from '/static/js/dialogs.js';
import { applySessionTheme } from '/static/js/stage-render.js';
import { renderReviewNote, syncSelectedImport, showReview, reimportSelected, reimport, importKahoot } from '/static/js/views/plan-import.js';
import { words } from '/static/js/stage-words.js';
import { field, segmentedChoice, input, selectField, fmtTimer, planFields } from '/static/js/views/plan-fields.js';
import { createPreview } from '/static/js/views/plan-preview.js';

const PROFILES = [
  ['camera_strip', 'Camera strip'],
  ['camera_pip', 'Camera PiP'],
  ['screen_only', 'Screen only'],
];
const KIND_ICON = { break: 'coffee', breakout: 'door-open' };
const ROUNDS = [['pairs', 'Pairs'], ['g4a', 'Groups of 4 · A'], ['g4b', 'Groups of 4 · B'], ['', 'No rooms shown']];

let ctx;
let head;
let listCard;
let editor;
let toolbar;
let reviewNote;
let split;
let reviewHost;
// selected = the focused item (the editor's); picked = every selected item (it included); anchor = where Shift ranges start.
const st = { sid: null, session: null, slides: new Map(), deck: null, types: {}, rounds: null, selected: null, picked: new Set(), anchor: null, dirty: false, collapsed: new Set(), pending: false };
const { previewCard, updatePreview } = createPreview({ st, getEditor: () => editor, slideOf, titleOf });
const { loadDefaults, letteringChip, fontField, rolesField, notesField, timerField, musicField, answersField } = planFields({ st, markDirty, updatePreview, renderList, renderEditor, slideOf });

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
  // Default titles are the stage's words, in the session's language (as src/live/plan.py).
  const w = words(st.session && st.session.language);
  if (it.kind === 'activity') return it.question || (it.options || {}).title || (it.type === 'groups_reveal' ? w.reveal : (st.types[it.type] || {}).label) || 'Activity';
  return it.kind === 'breakout' ? w.breakout : w.break;
}

/** The title on one line, for the list and the editor ("\n" breaks it only on the stage). */
function labelOf(it) { return oneLine(titleOf(it)); }

/** The breakout round an item shows, as the editor names it ("Pairs"). */
function roundLabel(it) {
  if (it.kind === 'breakout') {
    const r = (it.options || {}).round || '';
    return r ? (ROUNDS.find(([v]) => v === r) || [r, r])[1] : '';
  }
  const opt = ((st.types[it.type] || {}).options || []).find((o) => o.key === 'round');
  const round = (it.options || {}).round || (opt && opt.default);
  const choice = opt && (opt.choices || []).find(([v]) => v === round);
  return choice ? choice[1] : round || '';
}

// Math.random() can hex-encode to fewer than 6 digits (even "" at exactly 0) — pad so the
// suffix is always 6 characters.
function newId(prefix) { return `${prefix}-${Math.random().toString(16).slice(2, 8).padEnd(6, '0')}`; }

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

/** A hidden item's thumbnail, dimmed and marked like PowerPoint's hidden slide. */
function hiddenThumbHtml(it) {
  return `<span class="thumb-box">${thumbHtml(it)}<span class="thumb-hidden" role="img" aria-label="Hidden" title="Hidden: skipped by Next and Previous, still reachable from the presenter">${icon('eye-off')}</span></span>`;
}

/** The items a row's menu or the H key acts on: the whole selection when the row is part of one, else the row alone. */
function targetItems(id) {
  const ids = st.picked.has(id) && st.picked.size > 1 ? st.picked : new Set([id]);
  return allItems().filter((x) => ids.has(x.it.id)).map((x) => x.it);
}

const allHidden = (items) => items.every((it) => it.include === false);

/** "Hide slide" / "Show slide" (by kind), or "Hide 3 items" for a selection. */
function hideLabel(items) {
  const verb = allHidden(items) ? 'Show' : 'Hide';
  return items.length === 1 ? `${verb} ${items[0].kind === 'slide' ? 'slide' : items[0].kind}` : `${verb} ${items.length} items`;
}

/** Hide (or, when every one is already hidden, show) the items: PowerPoint's "Hide slide". Drives the same `include` flag as the editor switch. */
function toggleHidden(ids) {
  const items = allItems().filter((x) => ids.includes(x.it.id)).map((x) => x.it);
  if (!items.length) return;
  const show = allHidden(items);
  items.forEach((it) => { it.include = show; });
  markDirty();
  renderList();
  renderEditor();
}

function chipHtml(it) {
  const chips = [];
  if (it.kind === 'activity') chips.push(`<span class="chip accent">${esc((st.types[it.type] || {}).label || it.type)}</span>`);
  if (it.kind === 'break') chips.push('<span class="chip">Break</span>');
  if (it.kind === 'breakout') chips.push('<span class="chip accent">Breakout</span>');
  const round = it.type === 'groups_reveal' || it.kind === 'breakout' ? roundLabel(it) : '';
  if (round) chips.push(`<span class="chip">${esc(round)}</span>`);
  if (it.timer && it.timer.enabled) chips.push(`<span class="chip">${icon('timer')}${fmtTimer(it.timer.seconds)}</span>`);
  if (it.music && it.music.enabled !== false) chips.push(`<span class="chip" title="Music">${icon('music')}</span>`);
  chips.push(letteringChip(it));
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
  ctx.onGroups(async () => {
    await loadRounds();
    const found = st.selected && findItem(st.selected);
    if (found) updatePreview(found.it);
  });
  await load();
}

/**
 * Add `item` (from another tab — the Groups tab's reveal) to the unsaved
 * edits: after the selected item, else at the end of the first section.
 * Returns the section's name.
 */
export async function stageItem(item) {
  if (!st.dirty) await load();
  if (!st.session || !(st.session.sections || []).length) throw new Error('The plan has no sections yet — import the slides first');
  const at = st.selected ? findItem(st.selected) : null;
  const sec = at ? at.sec : st.session.sections[0];
  const added = Object.assign({ id: newId(item.kind === 'activity' ? 'act' : item.kind === 'break' ? 'brk' : 'item') }, item);
  sec.items.splice(at ? at.ii + 1 : sec.items.length, 0, added);
  st.selected = st.anchor = added.id;
  st.picked = new Set([added.id]);
  st.collapsed.delete(sec.id);
  markDirty();
  render();
  return { section: sec.name, id: added.id };
}

/** The breakout rounds of groups.yaml — the reveal previews show the real rooms. */
async function loadRounds() {
  try {
    st.rounds = st.sid ? (await api(`/api/sessions/${st.sid}/groups`)).rounds : null;
  } catch (e) {
    st.rounds = null;
  }
}

/** The edit state and the nodes mount() creates, read when an import action runs. */
function importCtx() {
  return { st, ctx, reviewNote, split, reviewHost, toolbar, findItem, load };
}

export function show() {
  loadDefaults();
  if (ctx.reviewFor && ctx.reviewFor === ctx.sessionId) { load(); return; }
  if (!st.dirty) load();
}

async function load() {
  st.sid = ctx.sessionId;
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
    await loadRounds();
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
    showReview(importCtx());
  }
}

function render() {
  renderReviewNote(importCtx());
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
    `<button type="button" class="button-surface" data-reimport data-track="plan.import-deck">${icon('refresh-cw')} ${st.deck && st.deck.slides.length ? 'Re-import PowerPoint' : 'Import PowerPoint'}</button>` +
    (st.deck && st.deck.slides.length
      ? `<button type="button" class="button-surface" data-reimport-selected data-track="plan.import-selected" hidden>${icon('refresh-cw')} Re-import selected</button>`
      : '') +
    `<button type="button" class="button-surface" data-add-section data-track="plan.add-section">${icon('plus')} Add section</button>` +
    `<button type="button" class="button-surface" data-kahoot data-track="plan.import-kahoot" title="A quiz from Kahoot's spreadsheet template (.xlsx)">${icon('upload')} Import Kahoot</button>` +
    `<span class="plan-fold"><button type="button" class="button-ghost" data-fold="collapse" data-track="plan.fold-all" title="Collapse all sections">${icon('chevrons-down-up')} Collapse all</button>` +
    `<button type="button" class="button-ghost" data-fold="expand" data-track="plan.fold-all" title="Expand all sections">${icon('chevrons-up-down')} Expand all</button></span>` +
    `<span class="plan-totals ${over ? 'over' : ''}">Planned ${fmtMinutes(total)} of ${fmtMinutes(st.session ? st.session.duration_minutes : 0)}` +
    `${timers ? ` · ${Math.round(timers / 60)} min on timers` : ''}</span>` +
    (st.dirty ? `<span class="dirty-bar"><span class="chip warn">Unsaved changes</span>` +
      `<button type="button" class="button-ghost" data-discard data-track="plan.discard">Discard</button>` +
      `<button type="button" class="button-primary save-small" data-save data-track="plan.save">Save</button></span>` : '');
  toolbar.querySelector('[data-reimport]').addEventListener('click', () => reimport(importCtx()));
  const selected = toolbar.querySelector('[data-reimport-selected]');
  if (selected) selected.addEventListener('click', () => reimportSelected(importCtx()));
  syncSelectedImport(importCtx());
  toolbar.querySelector('[data-add-section]').addEventListener('click', () => addSection());
  toolbar.querySelector('[data-kahoot]').addEventListener('click', () => importKahoot(importCtx()));
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
    listCard.appendChild(emptyStateEl('upload', 'Import the PowerPoint to start the plan.', { actionLabel: 'Import PowerPoint', onAction: () => reimport(importCtx()) }));
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
      `<button type="button" class="icon-button sec-toggle" data-track="plan.section-toggle" aria-expanded="${!collapsed}" aria-label="${collapsed ? 'Expand' : 'Collapse'} ${esc(sec.name)}">${icon(collapsed ? 'chevron-right' : 'chevron-down')}</button>` +
      `<button type="button" class="sec-name" title="Rename">${esc(sec.name)}</button>` +
      `<span class="sec-meta">${sec.items.length} item${sec.items.length === 1 ? '' : 's'}${collapsed ? ' · collapsed' : ''}</span>` +
      `<label class="sec-min"><input type="number" min="0" max="1440" value="${sec.minutes}" aria-label="Planned minutes for ${esc(sec.name)}"><span>min</span></label>` +
      `<button type="button" class="icon-button kebab" aria-label="Section actions" data-track="plan.section-menu">${icon('ellipsis-vertical')}</button>`;
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
      row.className = 'item-row' + (st.picked.has(it.id) ? ' selected' : '') + (it.id === st.selected && st.picked.size > 1 ? ' focused' : '') + (it.include === false ? ' hidden-item' : '');
      row.dataset.item = it.id;
      row.tabIndex = 0;
      row.draggable = true;
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(st.picked.has(it.id)));
      row.innerHTML =
        `<span class="num">${n}</span>${it.include === false ? hiddenThumbHtml(it) : thumbHtml(it)}` +
        `<span class="item-title">${esc(labelOf(it))}</span><span class="chips">${chipHtml(it)}</span>` +
        `<button type="button" class="icon-button kebab" aria-label="Item actions" data-track="plan.item-menu" aria-haspopup="menu">${icon('ellipsis-vertical')}</button>`;
      row.querySelector('.kebab').addEventListener('click', (e) => {
        e.stopPropagation();
        const targets = targetItems(it.id);
        rowMenu(e.currentTarget, [
          { label: hideLabel(targets), icon: allHidden(targets) ? 'eye' : 'eye-off', onClick: () => toggleHidden(targets.map((x) => x.id)) },
        ]);
      });
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
          { label: 'Breakout', icon: 'door-open', onClick: () => addItem(si, 'breakout') },
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
  syncSelectedImport(importCtx());
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
  if (!mod && !e.altKey && e.key.toLowerCase() === 'h') {
    e.preventDefault();
    toggleHidden(targetItems(id).map((x) => x.id));
    focusRow(id);
    return;
  }
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
    : kind === 'breakout'
      ? { kind: 'breakout', id: newId('bko'), title: '', profile: 'screen_only', options: { round: 'pairs' },
        timer: { enabled: true, seconds: 600, start: 'manual', show_on: 'stage', end: 'keep' } }
      : { kind: 'break', id: newId('brk'), title: '', profile: 'camera_strip' };
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
  const copies = flat.map((x) => Object.assign(clone(x.it), { id: newId({ slide: 'slide', activity: 'act', break: 'brk', breakout: 'bko' }[x.it.kind] || 'item') }));
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

async function saveSession() {
  try {
    // Only `sections` is Plan's own edit; re-read the session first and merge
    // just that in, so a field saved from another tab in the meantime (title,
    // language, stage font…) isn't silently reverted by this PUT.
    const fresh = (await api(`/api/sessions/${st.sid}`)).session;
    const merged = Object.assign({}, fresh, { sections: st.session.sections });
    const res = await api(`/api/sessions/${st.sid}`, { method: 'PUT', body: { session: merged } });
    st.session = res.session;
    st.dirty = false;
    toast('Saved to session.yaml');
    render();
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ---------------------------------------------------------------- editor

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
  const title = editorTitle(it, sec);
  editor.appendChild(title);

  editor.appendChild(previewCard(it));

  const form = document.createElement('div');
  form.className = 'card ed-card';
  editor.appendChild(form);
  const rerenderRow = () => { renderList(); title.querySelector('h1').textContent = labelOf(it); };

  if (it.kind === 'activity') activityFields(form, it, rerenderRow);
  else otherItemFields(form, it, rerenderRow);

  form.appendChild(cameraLayoutField(it));
  form.appendChild(timerField(it));
  form.appendChild(musicField(it));

  form.appendChild(field('In this session', switchRow(it.include !== false, 'Off = hidden: Next skips it, the presenter can still open it', (next) => {
    it.include = next;
    markDirty();
    renderList();
  }, 'In this session')));

  const answers = it.kind === 'activity' ? answersField(it, st.types[it.type] || {}) : null;
  if (answers) form.appendChild(answers);

  form.appendChild(notesField(it));
  form.appendChild(editorTools(it));

  const save = saveButton();
  form.appendChild(save);
  const observer = () => { save.disabled = !st.dirty; save.textContent = st.dirty ? 'Save to session.yaml' : 'Saved'; };
  form.addEventListener('input', observer);
  form.addEventListener('click', () => setTimeout(observer, 0));
}

function editorTitle(it, sec) {
  const flat = allItems();
  const pos = flat.findIndex((x) => x.it.id === it.id);
  const prev = pos > 0 ? labelOf(flat[pos - 1].it) : null;
  const title = document.createElement('div');
  title.className = 'detail-title';
  title.innerHTML = `<h1>${esc(labelOf(it))}</h1><p class="muted">${esc(sec.name)} · item ${pos + 1}${prev ? ` · after “${esc(prev)}”` : ''}</p>`;
  return title;
}

/** An activity's type, title, question, lettering and chat prompt. */
function activityFields(form, it, rerenderRow) {
  // More than five types, so a select, not a segmented control (design.md segmented: at most five peers).
  // A type this PC doesn't have stays listed under its own name, so the select never shows another one.
  const types = Object.values(st.types).map((t) => [t.type, t.label]);
  if (!types.some(([t]) => t === it.type)) types.push([it.type, it.type]);
  form.appendChild(field('Type', selectField('Activity type', types, it.type, (v) => {
    it.type = v;
    it.options = {};
    markDirty();
    renderList();
    renderEditor();
  })));
  const spec = st.types[it.type] || {};
  form.appendChild(field('Title', input(it.title, (v) => { it.title = v; markDirty(); rerenderRow(); }, { placeholder: spec.capture !== false ? oneLine(it.question) || spec.label : (it.options || {}).title || spec.label }), 'with-hint'));
  form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', spec.capture !== false
    ? '<p class="ed-hint">The name in the plan and on the presenter. Empty = the question.</p>'
    : '<p class="ed-hint">Shown on the stage — type \\n for a line break there.</p>');
  if (spec.capture !== false) {
    form.appendChild(field('Question', input(it.question, (v) => { it.question = v; markDirty(); rerenderRow(); }, { placeholder: 'What did you learn about this group?' }), 'with-hint'));
    form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', '<p class="ed-hint">Type \\n where the line should break on the stage.</p>');
    form.appendChild(fontField(it, 'Question font', 72));
    form.appendChild(rolesField(it, ['hint', 'answers']));
    form.appendChild(field('Prompt for chat', input(it.chat_prompt, (v) => { it.chat_prompt = v; markDirty(); }, { placeholder: spec.chat_prompt_hint || 'One or two words: …' })));
  } else {
    form.appendChild(fontField(it, 'Title font', 72));
    form.appendChild(rolesField(it, ['answers']));
  }
}

/** A slide's, break's or breakout's title, lettering, rooms and slide text. */
function otherItemFields(form, it, rerenderRow) {
  const placeholder = it.kind === 'slide' ? (slideOf(it) || {}).title || '' : words(st.session.language)[it.kind];
  form.appendChild(field('Title', input(it.title, (v) => { it.title = v; markDirty(); rerenderRow(); }, { placeholder }), 'with-hint'));
  const hint = document.createElement('p');
  hint.className = 'ed-hint';
  hint.textContent = it.kind === 'slide' ? 'Shown on the presenter as "next". Empty = the slide\'s own title.' : 'Shown on the presenter and on the stage — type \\n for a line break there.';
  form.lastChild.querySelector('.ed-control').appendChild(hint);
  if (it.kind === 'break' || it.kind === 'breakout') form.appendChild(fontField(it, 'Title font', 72));
  if (it.kind === 'breakout') form.appendChild(rolesField(it, ['sub']));
  if (it.kind === 'breakout') {
    form.appendChild(field('Rooms', segmentedChoice(ROUNDS, (it.options || {}).round || '', (v) => {
      it.options = Object.assign({}, it.options, { round: v });
      markDirty();
      renderList();
      renderEditor();
    }, 'Breakout round'), 'with-hint'));
    form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', '<p class="ed-hint">Under the title the stage says which round is in the rooms, and how many (Groups tab).</p>');
  }
  if (it.kind === 'slide') slideTextFields(form, it);
}

function cameraLayoutField(it) {
  const row = field('Camera layout', segmentedChoice(PROFILES, it.profile, (v) => { it.profile = v; markDirty(); renderList(); renderEditor(); }, 'Camera layout'));
  if (it.kind === 'slide' && it.profile == null) {
    row.querySelector('.ed-control').insertAdjacentHTML('beforeend', '<p class="ed-hint warn">Detection was unsure for this slide — pick one.</p>');
  } else if (it.kind === 'slide') {
    const s = slideOf(it);
    if (s && s.profile && s.profile !== it.profile) {
      row.querySelector('.ed-control').insertAdjacentHTML('beforeend', `<p class="ed-hint">Detected: ${esc(PROFILES.find((p) => p[0] === s.profile)[1])}</p>`);
    }
  }
  return row;
}

function editorTools(it) {
  const tools = document.createElement('div');
  tools.className = 'ed-tools';
  tools.innerHTML =
    `<button type="button" class="button-surface" data-up data-track="plan.item-up">${icon('chevron-up')} Move up</button>` +
    `<button type="button" class="button-surface" data-down data-track="plan.item-down">${icon('chevron-down')} Move down</button>` +
    `<button type="button" class="button-surface" data-dup title="Ctrl+D">${icon('copy-plus')} Duplicate</button>` +
    `<button type="button" class="button-tint danger" data-delete>${icon('trash-2')} Delete</button>`;
  tools.querySelector('[data-dup]').addEventListener('click', () => duplicateItems([it.id]));
  tools.querySelector('[data-up]').addEventListener('click', () => moveItem(it.id, -1));
  tools.querySelector('[data-down]').addEventListener('click', () => moveItem(it.id, 1));
  tools.querySelector('[data-delete]').addEventListener('click', () => deleteItems([it.id]));
  return tools;
}

function saveButton() {
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'button-primary ed-save';
  save.textContent = st.dirty ? 'Save to session.yaml' : 'Saved';
  save.disabled = !st.dirty;
  save.addEventListener('click', saveSession);
  return save;
}

/** Several items selected: what they are, and what can be set on all of them at once. */
function renderBulk() {
  const items = allItems().filter((x) => st.picked.has(x.it.id)).map((x) => x.it);
  const count = (kind, one, many) => { const n = items.filter((it) => it.kind === kind).length; return n ? `${n} ${n === 1 ? one : many}` : ''; };
  const title = document.createElement('div');
  title.className = 'detail-title';
  title.innerHTML = `<h1>${items.length} items selected</h1><p class="muted">` +
    [count('slide', 'slide', 'slides'), count('activity', 'activity', 'activities'), count('break', 'break', 'breaks'), count('breakout', 'breakout', 'breakouts')].filter(Boolean).join(' · ') +
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
  form.appendChild(field('Camera layout', segmentedChoice(PROFILES, profiles.size === 1 ? [...profiles][0] : null, (v) => {
    items.forEach((it) => { it.profile = v; });
    markDirty();
    renderList();
    renderEditor();
  }, 'Camera layout for the selected items')));
  if (profiles.size > 1) form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', '<p class="ed-hint">They differ now — a pick sets all of them.</p>');

  const allIn = items.every((it) => it.include !== false);
  form.appendChild(field('In this session', switchRow(allIn, allIn ? 'All in · off = hide them' : 'Some are hidden · on = all in', (next) => {
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
  form.appendChild(saveButton());
}

/** A slide's text: drawn by the stage in the session's lettering (the default), or PowerPoint's picture. */
function slideTextFields(form, it) {
  const s = slideOf(it);
  if (!s) return;
  if (!s.boxes || !s.bg_file) {
    if ((s.texts || []).length) {
      form.appendChild(field('Slide text', '<p class="ed-hint">Shown as PowerPoint drew it. Re-import the PowerPoint to draw this slide\'s text in the stage font.</p>'));
    }
    return;
  }
  const live = it.live_text !== false;
  form.appendChild(field('Slide text', segmentedChoice([['live', 'In the stage lettering'], ['image', 'As in PowerPoint']], live ? 'live' : 'image', (v) => {
    it.live_text = v === 'image' ? false : null;
    markDirty();
    renderEditor();
  }, 'Slide text'), 'with-hint'));
  form.lastChild.querySelector('.ed-control').insertAdjacentHTML('beforeend', `<p class="ed-hint">${live
    ? `${s.boxes.length} text box${s.boxes.length === 1 ? '' : 'es'} drawn in the session's lettering, where PowerPoint had them.`
    : 'The slide exactly as PowerPoint drew it.'}</p>`);
  if (live) {
    form.appendChild(fontField(it, 'Title font', null));
    form.appendChild(rolesField(it, ['slide_text']));
  }
}
