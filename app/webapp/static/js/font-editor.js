// The stage lettering editor — the title font (an installed font or a font
// file, weight, line thickness), the text font (every other text; by default
// the chat hint's plain sans) and, for each kind of text, which of the two it
// uses and whether it is in capitals. One editor for a session's own
// lettering (Sessions tab) and for the default every new session starts from
// (Settings → Stage defaults, #110); the caller says how a change is saved.

import { icon } from '/static/_vendored/icons/icons.js';
import { esc, toast } from '/static/js/ui.js';
import { ROLES, TEXT_FONTS, sessionRole } from '/static/js/lettering.js';

/** Installed fonts for the title font (value '' = the theme's Patrick Hand). */
export const STAGE_FONTS = [
  ['', 'Patrick Hand (theme)'],
  ['system-ui', 'System sans'],
  ['Georgia', 'Georgia (serif)'],
  ['Segoe Print', 'Segoe Print (handwriting)'],
];

export const FONT_DEFAULTS = {
  file: '', family: '', weight: 400, stroke_px: 0, caps: true, title_color: '', title_size: 0,
  timer_idle: '', timer_running: '', timer_paused: '', timer_done: '', text_family: '', text_weight: 400, roles: {},
};

/** The stage timer's four states (#211): the key in the block, its words, and the theme's colour (themes/default.css). */
export const TIMER_STATES = [
  ['idle', 'Not started', '#1f1f1f'],
  ['running', 'Running', '#00a44e'],
  ['paused', 'Paused', '#f2b705'],
  ['done', 'Ended', '#c40c0c'],
];

/** A lettering block that says nothing the theme doesn't: saved as no block at all. */
export function isThemeLettering(f) {
  return !f.file && !f.family && f.weight === 400 && !f.stroke_px && f.caps !== false && !f.title_color && !f.title_size &&
    !TIMER_STATES.some(([key]) => f[`timer_${key}`]) &&
    !f.text_family && f.text_weight === 400 && !Object.keys(f.roles || {}).length;
}

/** The pill's text on a fill: white while it holds 3:1 (large text), else the dark ink — src/sessions/theme.py `timer_ink`. */
const timerInk = (fill) => (contrastRatio(fill, 'rgb(255, 255, 255)') >= 3 ? '#ffffff' : '#1f1f1f');

/** WCAG contrast ratio of a #rrggbb text colour on a CSS `rgb(r, g, b)` background, or null if unreadable. */
export function contrastRatio(hex, bg) {
  const bgParts = String(bg || '').match(/\d+(\.\d+)?/g);
  if (!/^#[0-9a-f]{6}$/i.test(hex || '') || !bgParts || bgParts.length < 3) return null;
  const lum = (rgb) => {
    const [r, g, b] = rgb.map((c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const a = lum([1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)));
  const b = lum(bgParts.slice(0, 3).map(Number));
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

const fileName = (path) => String(path || '').split(/[\\/]/).pop();

/** The title font in words: its file's name, else the installed font's label. */
export function letteringLabel(font) {
  const f = Object.assign({}, FONT_DEFAULTS, font || {});
  if ((f.file || '').trim()) return fileName(f.file);
  return (STAGE_FONTS.find(([v]) => v === f.family) || [null, f.family || 'Patrick Hand (theme)'])[1];
}

/** The stage themes as <option>s (a theme no longer on this PC stays, marked). */
export function themeOptions(themes, now) {
  const all = themes.some((t) => t.value === now) ? themes : [...themes, { value: now, label: `${now} (not on this PC)` }];
  return all.map((t) => `<option value="${esc(t.value)}"${t.value === now ? ' selected' : ''}>${esc(t.source === 'library' ? 'Library · ' + t.label : t.label)}</option>`).join('');
}

/** Two buttons (or more) as range tabs; `value` is the active one. */
function tabs(label, attr, options, value) {
  return `<div class="range-tabs ed-tabs" role="group" aria-label="${esc(label)}">${options.map(([v, l]) =>
    `<button type="button" class="range-tab${v === value ? ' active' : ''}" aria-pressed="${v === value}" ${attr}="${esc(String(v))}">${esc(l)}</button>`).join('')}</div>`;
}

/**
 * The editor's markup: the sample (a small stage canvas) and the rows.
 * opts: hint (the chat hint's words), sampleClass (extra class on the sample
 * canvas), library ([{name, path}] font files to offer as title fonts).
 */
export function fontEditorHtml(font, { hint, sampleClass = '', library = [] }) {
  const f = Object.assign({}, FONT_DEFAULTS, font || {});
  const file = (f.file || '').trim();
  const stroke = Number(f.stroke_px) || 0;
  const color = f.title_color || '';
  const size = Number(f.title_size) || 0;
  const inLibrary = library.some((l) => l.path === file);
  const timerVars = TIMER_STATES.filter(([key]) => f[`timer_${key}`])
    .map(([key]) => `;--st-timer-${key}:${esc(f[`timer_${key}`])};--st-timer-${key}-ink:${timerInk(f[`timer_${key}`])}`).join('');
  return `<div class="font-sample-host"><div class="stage-canvas font-sample ${esc(sampleClass)}" style="--st-font-stroke:${stroke}px` +
    `${color ? `;--st-title-color:${esc(color)}` : ''}${size ? `;--st-title-size:${size}px` : ''}${timerVars}">` +
    `<h1 class="st-question">Hello, group!</h1>` +
    `<div class="font-sample-row"><span class="st-hint">${icon('message-square')}${esc(hint)}</span>` +
    `<div class="st-body"><span class="font-sample-words">meetings · focus</span></div></div>` +
    `<div class="font-sample-row font-sample-timers">${TIMER_STATES.map(([key, label]) =>
      `<span class="st-pill ${key}">${icon('timer')}${esc(label)}</span>`).join('')}</div></div></div>` +
    `<h4 class="font-group">Title font</h4><div class="font-rows">` +
    `<label class="font-row"><span class="small">Font</span><span class="inline-controls"><select class="select-native" aria-label="Title font" data-title-family>` +
    STAGE_FONTS.map(([v, l]) => `<option value="${esc(v)}"${!file && v === f.family ? ' selected' : ''}>${esc(l)}</option>`).join('') +
    library.map((l) => `<option value="${esc('file:' + l.path)}"${l.path === file ? ' selected' : ''}>Library · ${esc(l.name)}</option>`).join('') +
    (file && !inLibrary ? `<option value="__file" selected>File · ${esc(fileName(file))}</option>` : '') +
    `</select><button type="button" class="button-surface" data-font-pick>${icon('folder-open')} ${file ? 'Change file…' : 'Font file…'}</button></span></label>` +
    `<div class="font-row"><span class="small">Weight</span>${tabs('Title weight', 'data-weight', [[400, 'Regular'], [700, 'Bold']], f.weight)}</div>` +
    `<label class="font-row font-stroke"><span class="small">Line thickness</span>` +
    `<input type="range" min="0" max="6" step="0.25" value="${stroke}" aria-label="Line thickness in stage px">` +
    `<output class="mono small">${stroke} px</output></label>` +
    `<div class="font-row"><span class="small">Colour</span><span class="inline-controls">` +
    `<input type="color" class="font-color" aria-label="Title colour" data-title-color value="${esc(color || '#1f1f1f')}">` +
    `<button type="button" class="button-surface" data-title-color-reset${color ? '' : ' disabled'}>Theme colour</button>` +
    `<span class="small muted" data-contrast></span></span></div>` +
    `<label class="font-row"><span class="small">Size</span><span class="inline-controls">` +
    `<input type="number" class="input font-size-input" min="24" max="240" step="2" placeholder="72" value="${size || ''}" aria-label="Title size in stage px" data-title-size>` +
    `<span class="small muted">px · empty = 72</span></span></label></div>` +
    `<p class="small muted font-note">Colour and size letter activity titles and questions. Imported slides keep their PowerPoint size and colour.</p>` +
    (file ? `<p class="mono small muted font-path" title="${esc(file)}">${esc(file)}</p>` : '') +
    `<h4 class="font-group">Text font</h4><div class="font-rows">` +
    `<label class="font-row"><span class="small">Font</span><select class="select-native" aria-label="Text font" data-text-family>` +
    TEXT_FONTS.map(([v, l]) => `<option value="${esc(v)}"${v === f.text_family ? ' selected' : ''}>${esc(l)}</option>`).join('') +
    (f.text_family && !TEXT_FONTS.some(([v]) => v === f.text_family) ? `<option value="${esc(f.text_family)}" selected>${esc(f.text_family)}</option>` : '') +
    `</select></label>` +
    `<div class="font-row"><span class="small">Weight</span>${tabs('Text weight', 'data-text-weight', [[400, 'Regular'], [700, 'Bold']], f.text_weight)}</div></div>` +
    `<h4 class="font-group">Each kind of text</h4><div class="font-rows role-rows">` +
    ROLES.map((r) => {
      const now = sessionRole(f, r.key);
      return `<div class="role-row" data-role="${r.key}"><span class="small">${esc(r.label)}</span>` +
        tabs(`${r.label}: font`, 'data-role-font', [['title', 'Title font'], ['text', 'Text font']], now.font === 'title' ? 'title' : 'text') +
        tabs(`${r.label}: capitals`, 'data-role-caps', [['true', 'ALL CAPS'], ['false', 'As typed']], String(now.caps)) + '</div>';
    }).join('') + `</div>` +
    `<h4 class="font-group">Timer colours</h4><div class="font-rows">` +
    TIMER_STATES.map(([key, label, theme]) =>
      `<div class="font-row"><span class="small">${esc(label)}</span><span class="inline-controls">` +
      `<input type="color" class="font-color" aria-label="Timer colour: ${esc(label.toLowerCase())}" data-timer-color="${key}" value="${esc(f[`timer_${key}`] || theme)}">` +
      `</span></div>`).join('') +
    `<div class="font-row"><span class="small"></span><button type="button" class="button-surface" data-timer-reset` +
    `${TIMER_STATES.some(([key]) => f[`timer_${key}`]) ? '' : ' disabled'}>Theme colours</button></div></div>` +
    `<p class="small muted font-note">The stage timer — the pill on an activity or slide, the big clock on a break or breakout — takes the colour of its state: the pill as its fill, the clock as its text.</p>`;
}

/**
 * Wire the editor inside `card`.
 * opts: current() → the saved block (or null); save(next|null) → Promise<bool>
 * (null = the theme's lettering); redraw() after a saved change; pickFile() →
 * Promise<path|''> for "Font file…".
 */
export function wireFontEditor(card, { current, save, redraw, pickFile }) {
  const sample = card.querySelector('.font-sample');
  const range = card.querySelector('input[type=range]');
  const out = card.querySelector('output');
  // from what is saved now: the thickness slider saves without drawing the card again
  const saveFont = (change) => {
    const next = Object.assign({}, FONT_DEFAULTS, current() || {}, change);
    return save(isThemeLettering(next) ? null : next);
  };
  // one kind of text: back to its default = no entry for it
  const setRole = (key, change) => {
    const role = ROLES.find((r) => r.key === key);
    const roles = Object.assign({}, (current() || {}).roles);
    const mine = Object.assign({}, roles[key], change);
    if (!mine.font || mine.font === role.font) delete mine.font;
    if (mine.caps !== true) delete mine.caps;
    if (Object.keys(mine).length) roles[key] = mine; else delete roles[key];
    return saveFont({ roles });
  };
  const again = async (p) => { if (await p) redraw(); };
  card.querySelector('[data-title-family]').addEventListener('change', (e) => {
    const v = e.target.value;
    if (v.startsWith('file:')) again(saveFont({ file: v.slice(5) }));
    else if (v !== '__file') again(saveFont({ file: '', family: v }));
  });
  card.querySelector('[data-text-family]').addEventListener('change', (e) => again(saveFont({ text_family: e.target.value })));
  card.querySelectorAll('[data-weight]').forEach((b) => b.addEventListener('click', () => again(saveFont({ weight: Number(b.dataset.weight) }))));
  card.querySelectorAll('[data-text-weight]').forEach((b) => b.addEventListener('click', () => again(saveFont({ text_weight: Number(b.dataset.textWeight) }))));
  card.querySelectorAll('.role-row').forEach((row) => {
    const key = row.dataset.role;
    row.querySelectorAll('[data-role-font]').forEach((b) => b.addEventListener('click', () => again(setRole(key, { font: b.dataset.roleFont }))));
    row.querySelectorAll('[data-role-caps]').forEach((b) => b.addEventListener('click', () => {
      const caps = b.dataset.roleCaps === 'true';
      again(key === 'title' ? saveFont({ caps }) : setRole(key, { caps }));  // a title's capitals are font.caps
    }));
  });
  // the title's colour and size (#191): the sample follows as you pick, a change saves
  const colorEl = card.querySelector('[data-title-color]');
  const sizeEl = card.querySelector('[data-title-size]');
  const contrastEl = card.querySelector('[data-contrast]');
  const paintContrast = () => {
    const set = !card.querySelector('[data-title-color-reset]').disabled || colorEl.dataset.dirty === '1';
    const ratio = set ? contrastRatio(colorEl.value, getComputedStyle(sample).backgroundColor) : null;
    contrastEl.textContent = ratio ? `${ratio.toFixed(1)}:1 on the stage background${ratio < 3 ? ' — hard to read' : ''}` : '';
    contrastEl.className = `small ${ratio && ratio < 3 ? 'chip warn' : 'muted'}`;
  };
  paintContrast();
  colorEl.addEventListener('input', () => {
    colorEl.dataset.dirty = '1';
    sample.style.setProperty('--st-title-color', colorEl.value);
    paintContrast();
  });
  colorEl.addEventListener('change', () => again(saveFont({ title_color: colorEl.value })));
  card.querySelector('[data-title-color-reset]').addEventListener('click', () => again(saveFont({ title_color: '' })));
  // the timer's four colours (#211): the sample pills follow as you pick, a change saves; the theme's own colour saves as none
  TIMER_STATES.forEach(([key, , theme]) => {
    const el = card.querySelector(`[data-timer-color=${key}]`);
    el.addEventListener('input', () => {
      sample.style.setProperty(`--st-timer-${key}`, el.value);
      sample.style.setProperty(`--st-timer-${key}-ink`, timerInk(el.value));
      card.querySelector('[data-timer-reset]').disabled = false;
    });
    el.addEventListener('change', () => again(saveFont({ [`timer_${key}`]: el.value === theme ? '' : el.value })));
  });
  card.querySelector('[data-timer-reset]').addEventListener('click',
    () => again(saveFont(Object.fromEntries(TIMER_STATES.map(([key]) => [`timer_${key}`, ''])))));
  sizeEl.addEventListener('input', () => sample.style.setProperty('--st-title-size', `${Number(sizeEl.value) || 72}px`));
  sizeEl.addEventListener('change', async () => {
    const n = Math.round(Number(sizeEl.value)) || 0;
    const size = n ? Math.min(240, Math.max(24, n)) : 0;
    sizeEl.value = size || '';
    if (await saveFont({ title_size: size })) toast(size ? `Title size ${size} px saved` : 'Title size back to the theme’s');
  });
  range.addEventListener('input', () => {
    out.textContent = `${range.value} px`;
    sample.style.setProperty('--st-font-stroke', `${range.value}px`);
  });
  range.addEventListener('change', async () => {
    if (await saveFont({ stroke_px: Number(range.value) })) toast(`Line thickness ${range.value} px saved`);
  });
  card.querySelector('[data-font-pick]').addEventListener('click', async () => {
    const path = await pickFile();
    if (!path) return;
    if (await saveFont({ file: path })) { toast('Stage font saved'); redraw(); }
  });
}
