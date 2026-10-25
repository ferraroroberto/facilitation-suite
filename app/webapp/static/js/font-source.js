// Where a stage font value comes from. Three layers: the global default
// (Settings → Stage defaults, a template copied into a new session), the
// presentation (the session's own lettering, Sessions → Stage look) and the
// item (its own exception, Plan tab). resolveFont() is the one place that
// decides which layer is in effect; the Plan editor, the plan list and Stage
// look all read it. No imports, so a unit test can load it outside a browser.

/** Installed fonts for the title font (value '' = the theme's Patrick Hand). */
export const STAGE_FONTS = [
  ['', 'Patrick Hand (theme)'],
  ['system-ui', 'System sans'],
  ['Georgia', 'Georgia (serif)'],
  ['Segoe Print', 'Segoe Print (handwriting)'],
];

const fileName = (path) => String(path || '').split(/[\\/]/).pop();

/** The title font in words: its file's name, else the installed font's label. */
export function letteringLabel(font) {
  const f = font || {};
  if ((f.file || '').trim()) return fileName(f.file);
  return (STAGE_FONTS.find(([v]) => v === (f.family || '')) || [null, f.family])[1];
}

// A lettering block's title font as one comparable value: 'file:<path>', an installed family, or '' (the theme's).
const titleFamily = (f) => ((f.file || '').trim() ? `file:${f.file.trim()}` : (f.family || '').trim());

// Per property: its value in a session-level block (global or presentation; null = the theme's), and an
// item's own value (undefined = it follows). An item's family 'theme' (alias 'Patrick Hand') follows.
const READ = {
  size: {
    layer: (f) => Number((f || {}).title_size) || 72,
    item: (it) => (it.kind !== 'slide' && (it.font || {}).size_px ? it.font.size_px : undefined),
  },
  caps: {
    layer: (f) => (f || {}).caps !== false,
    item: (it) => ((it.font || {}).caps === true || (it.font || {}).caps === false ? it.font.caps : undefined),
  },
  family: {
    layer: (f) => titleFamily(f || {}),
    item: (it) => {
      const fam = ((it.font || {}).family || '').trim();
      return fam && fam !== 'theme' && fam !== 'Patrick Hand' ? fam : undefined;
    },
  },
};

/** The key an item's own value is saved under (session.yaml → item.font). */
export const ITEM_KEY = { size: 'size_px', caps: 'caps', family: 'family' };

/**
 * One font property in effect: `{ value, source, presentation, global }`, `source` ∈
 * 'global' | 'presentation' | 'item'. `session` is the session (its `font`), `defaults`
 * the Settings defaults payload (`{ stage: { font } }`), `item` a plan item or null
 * (Stage look), `prop` ∈ 'size' | 'caps' | 'family'.
 */
export function resolveFont(session, defaults, item, prop) {
  const read = READ[prop];
  const global = read.layer(((defaults || {}).stage || {}).font);
  const presentation = read.layer((session || {}).font);
  const own = item ? read.item(item) : undefined;
  if (own !== undefined) return { value: own, source: 'item', presentation, global };
  return { value: presentation, source: presentation === global ? 'global' : 'presentation', presentation, global };
}

/** A property's value in words: '72', 'ALL CAPS' / 'as typed', or the font's name. */
export function fontValueText(prop, value) {
  if (prop === 'size') return String(value);
  if (prop === 'caps') return value ? 'ALL CAPS' : 'as typed';
  return value.startsWith('file:') ? fileName(value.slice(5)) : letteringLabel({ family: value });
}

/** The editor chip for a resolved value: `Global`, `Presentation · global is 72`, `This item · presentation is 90`. */
export function sourceLabel(prop, r) {
  if (r.source === 'global') return 'Global';
  if (r.source === 'presentation') return `Presentation · global is ${fontValueText(prop, r.global)}`;
  if (r.value === r.global && r.presentation !== r.global) return 'This item · matches global';
  return `This item · presentation is ${fontValueText(prop, r.presentation)}`;
}

/** Whether an item can hold the global value as its own: an item names an installed font, never a file or the theme's. */
export function itemCanHold(prop, value) {
  return prop !== 'family' || (value !== '' && !value.startsWith('file:'));
}

/** The item follows the presentation again for `prop`: its own key goes (and an emptied block with it). */
export function followPresentation(item, prop) {
  const font = Object.assign({}, item.font);
  delete font[ITEM_KEY[prop]];
  if (Object.keys(font).length) item.font = font; else delete item.font;
}

/** The item holds the global value as its own (it stops following the presentation for `prop`). */
export function holdGlobal(item, prop, r) {
  item.font = Object.assign({ family: 'theme' }, item.font, { [ITEM_KEY[prop]]: r.global });
}

/** The session-level change that puts `prop` back to the global default's own value (Stage look's reset). */
export function globalPatch(prop, defaults) {
  const g = ((defaults || {}).stage || {}).font || {};
  if (prop === 'size') return { title_size: Number(g.title_size) || 0 };
  if (prop === 'caps') return { caps: g.caps !== false };
  return { file: g.file || '', family: g.family || '' };
}

/** What an item's own lettering overrides, one phrase each ("size 120 px (presentation: 72 px)"); empty when it follows. */
export function ownLettering(session, defaults, item) {
  const out = [];
  const words = { size: 'size', caps: 'capitals', family: 'font' };
  ['size', 'caps', 'family'].forEach((prop) => {
    const r = resolveFont(session, defaults, item, prop);
    if (r.source !== 'item') return;
    const px = prop === 'size' ? ' px' : '';
    out.push(`${words[prop]} ${fontValueText(prop, r.value)}${px} (presentation: ${fontValueText(prop, r.presentation)}${px})`);
  });
  if (Object.keys((item.font || {}).roles || {}).length) out.push('other text');
  return out;
}
