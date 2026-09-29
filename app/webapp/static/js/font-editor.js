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

export const FONT_DEFAULTS = { file: '', family: '', weight: 400, stroke_px: 0, caps: true, text_family: '', text_weight: 400, roles: {} };

/** A lettering block that says nothing the theme doesn't: saved as no block at all. */
export function isThemeLettering(f) {
  return !f.file && !f.family && f.weight === 400 && !f.stroke_px && f.caps !== false &&
    !f.text_family && f.text_weight === 400 && !Object.keys(f.roles || {}).length;
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
  const inLibrary = library.some((l) => l.path === file);
  return `<div class="font-sample-host"><div class="stage-canvas font-sample ${esc(sampleClass)}" style="--st-font-stroke:${stroke}px">` +
    `<h1 class="st-question">Hello, group!</h1>` +
    `<div class="font-sample-row"><span class="st-hint">${icon('message-square')}${esc(hint)}</span>` +
    `<div class="st-body"><span class="font-sample-words">meetings · focus</span></div></div></div></div>` +
    `<h4 class="font-group">Title font</h4><div class="font-rows">` +
    `<label class="font-row"><span class="small">Font</span><span class="inline-controls"><select class="select-native" aria-label="Title font" data-title-family>` +
    STAGE_FONTS.map(([v, l]) => `<option value="${esc(v)}"${!file && v === f.family ? ' selected' : ''}>${esc(l)}</option>`).join('') +
    library.map((l) => `<option value="${esc('file:' + l.path)}"${l.path === file ? ' selected' : ''}>Library · ${esc(l.name)}</option>`).join('') +
    (file && !inLibrary ? `<option value="__file" selected>File · ${esc(fileName(file))}</option>` : '') +
    `</select><button type="button" class="button-surface" data-font-pick>${icon('folder-open')} ${file ? 'Change file…' : 'Font file…'}</button></span></label>` +
    `<div class="font-row"><span class="small">Weight</span>${tabs('Title weight', 'data-weight', [[400, 'Regular'], [700, 'Bold']], f.weight)}</div>` +
    `<label class="font-row font-stroke"><span class="small">Line thickness</span>` +
    `<input type="range" min="0" max="6" step="0.25" value="${stroke}" aria-label="Line thickness in stage px">` +
    `<output class="mono small">${stroke} px</output></label></div>` +
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
    }).join('') + `</div>`;
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
