// The Settings pane's markup builders on the vendored settings-group component: an overline header
// over an inset card of one-line rows, each row a lead glyph, a label and one trailing item (a value
// and chevron that opens a sheet, a select, a switch, a number field, or a control of its own).
// The component's README is the markup contract; nothing here restyles it.

import { icon } from '/static/_vendored/icons/icons.js';
import { switchEl, setSwitch } from '/static/_vendored/switch/switch.js';
import { esc } from '/static/js/ui.js';

const el = (tag, className, html = '') => {
  const node = document.createElement(tag);
  node.className = className;
  node.innerHTML = html;
  return node;
};

/** The label, with one muted hint line under it when there is one. */
const labelHtml = (label, hint) => (hint
  ? `<span class="settings-row-text"><span class="settings-row-label">${esc(label)}</span><span class="settings-row-hint">${esc(hint)}</span></span>`
  : `<span class="settings-row-label">${esc(label)}</span>`);
const lead = (glyph) => icon(glyph, 'settings-row-icon');
const chevron = (name) => icon(name, 'settings-row-chevron');

/**
 * One group: the overline, the card of rows and the footnote under it. Returns the fragment to
 * append and the `card` / `foot` the caller redraws when the rows arrive later.
 */
export function settingsGroup(id, title, rows = [], footnote = '') {
  const frag = document.createDocumentFragment();
  const head = el('h3', 'settings-overline');
  head.id = `settings-group-${id}`;
  head.textContent = title;
  const card = el('section', 'card settings-group');
  card.dataset.group = id;
  card.setAttribute('aria-labelledby', head.id);
  rows.forEach((r) => card.appendChild(r));
  const foot = el('p', 'settings-footnote');
  foot.textContent = footnote;
  foot.hidden = !footnote;
  frag.append(head, card, foot);
  return { frag, card, foot };
}

/** A row that opens a sheet: the current value (muted, `tone` for an exception) and a chevron. */
export function valueRow({ glyph, label, hint, value, tone, valueKey, track, onOpen }) {
  const row = el('button', 'settings-row', `${lead(glyph)}${labelHtml(label, hint)}` +
    `<span class="settings-row-value"></span>${chevron('chevron-right')}`);
  row.type = 'button';
  row.setAttribute('aria-haspopup', 'dialog');
  row.dataset.track = track;
  if (valueKey) row.querySelector('.settings-row-value').dataset.value = valueKey;
  setRowValue(row, value, tone);
  row.addEventListener('click', onOpen);
  return row;
}

/** Repaint a value row's text (and its full text as the title, for an ellipsis) and tone. */
export function setRowValue(row, value, tone) {
  const v = row.querySelector('.settings-row-value');
  v.textContent = value;
  v.title = value;
  if (tone) v.dataset.tone = tone; else delete v.dataset.tone;
}

/** A row whose value is a native picker drawn as the muted value; `optionsHtml` is its <option>s. */
export function selectRow({ glyph, label, optionsHtml, aria, track }) {
  // untracked-control-exempt: `select.dataset.track` below gives it the constant id each caller passes
  const picker = `<select class="settings-row-select" aria-label="${esc(aria)}">${optionsHtml}</select>`;
  const row = el('label', 'settings-row', `${lead(glyph)}${labelHtml(label)}${picker}${chevron('chevron-down')}`);
  const select = row.querySelector('select');
  select.dataset.track = track;
  return { row, select };
}

/** A row with the vendored switch: a tap anywhere on the row toggles it. */
export function toggleRow({ glyph, label, hint, on, track, onToggle }) {
  const row = el('label', 'settings-row', `${lead(glyph)}${labelHtml(label, hint)}`);
  const sw = switchEl(on, { label, onToggle: (next, btn) => { setSwitch(btn, next); onToggle(next, btn); } });
  sw.dataset.track = track;
  row.appendChild(sw);
  return row;
}

/** A row with a number field and its unit after it; `onCommit(number)` runs on a change (blur or Enter). */
export function numberRow({ glyph, label, value, unit, min, max, step = '1', aria, track, onCommit }) {
  const row = el('label', 'settings-row', `${lead(glyph)}${labelHtml(label)}` +
    `<span class="settings-row-number"><input class="settings-row-input" type="number" inputmode="decimal" ` +
    `min="${min}" max="${max}" step="${step}" value="${esc(value)}" aria-label="${esc(aria)}">` +
    `<span class="settings-row-unit">${esc(unit)}</span></span>`);
  const input = row.querySelector('input');
  input.dataset.track = track;
  input.addEventListener('change', () => onCommit(input.valueAsNumber, input));
  return row;
}

/** A row that holds a control of its own (a segmented choice), set after the label. */
export function controlRow({ glyph, label, control }) {
  const row = el('div', 'settings-row', `${lead(glyph)}${labelHtml(label)}`);
  row.appendChild(control);
  return row;
}

/** A row that acts when tapped (no chevron: nothing opens). */
export function actionRow({ glyph, label, track, onTap }) {
  const row = el('button', 'settings-row', `${lead(glyph)}${labelHtml(label)}`);
  row.type = 'button';
  row.dataset.track = track;
  row.addEventListener('click', onTap);
  return row;
}

/** A row that only states a value. */
export function factRow({ glyph, label, value, valueKey }) {
  const row = el('div', 'settings-row', `${lead(glyph)}${labelHtml(label)}<span class="settings-row-value"></span>`);
  if (valueKey) row.querySelector('.settings-row-value').dataset.value = valueKey;
  setRowValue(row, value);
  return row;
}
