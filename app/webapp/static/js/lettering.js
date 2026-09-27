// The stage's lettering: two fonts — the title font (the session's own, for
// titles and questions) and the text font (by default the chat hint's plain
// sans, for everything else) — and, for each kind of text, which of the two
// it uses and whether it is in capitals. The session sets them
// (session.yaml → font; src/sessions/theme.py writes its CSS) and an item can
// set its own exceptions (its font.roles, written here as inline variables).
// Keep ROLES in step with src/sessions/model.py → TEXT_ROLES and roleVars
// with theme.py → role_vars.

/** The kinds of text: key (session.yaml), CSS variable name, label, default font. */
export const ROLES = [
  { key: 'title', css: 'title', label: 'Titles and questions', font: 'title' },
  { key: 'sub', css: 'sub', label: 'Subtitles', font: 'text' },
  { key: 'hint', css: 'hint', label: 'Chat hint', font: 'text' },
  { key: 'answers', css: 'answers', label: 'Answers (word cloud, cards, feed…)', font: 'text' },
  { key: 'slide_text', css: 'slide', label: 'Slide text (not its title)', font: 'text' },
];

/** Fonts for the text font (value '' = the chat hint's plain sans). */
export const TEXT_FONTS = [
  ['', 'Plain sans (the chat hint\'s)'],
  ['Patrick Hand', 'Patrick Hand (handwriting)'],
  ['Georgia', 'Georgia (serif)'],
  ['Segoe Print', 'Segoe Print (handwriting)'],
];

const family = (name) => `'${String(name).replace(/["'\\;{}<>]/g, '').trim()}'`;

/** The inline CSS variables that give one kind of text a font (and capitals). */
export function roleVars(key, style) {
  const role = ROLES.find((r) => r.key === key);
  if (!role || !style) return '';
  const v = `--st-${role.css}`;
  let out = '';
  const font = (style.font || '').trim();
  if (font === 'title') {
    out += `${v}-font:var(--st-font);${v}-weight:var(--st-font-weight);${v}-stroke:var(--st-font-stroke);`;
  } else if (font && (font === 'text' || family(font) !== "''")) {
    const fam = font === 'text' ? 'var(--st-text-font)' : `${family(font)},var(--st-text-font)`;
    const weight = key === 'hint' ? 'max(600, var(--st-text-weight))' : 'var(--st-text-weight)';
    out += `${v}-font:${fam};${v}-weight:${weight};${v}-stroke:0px;`;
  }
  if (style.caps === true || style.caps === false) out += `${v}-case:${style.caps ? 'uppercase' : 'none'};`;
  return out;
}

/** An item's exceptions (its font.roles) as inline variables for its stage box. */
export function roleStyle(roles) {
  return Object.entries(roles || {}).map(([k, s]) => (k === 'title' ? '' : roleVars(k, s))).join('');
}

/** What the session says for one kind of text: { font: 'title'|'text'|family, caps }. */
export function sessionRole(sessionFont, key) {
  const f = sessionFont || {};
  const role = ROLES.find((r) => r.key === key);
  const own = (f.roles || {})[key] || {};
  const caps = key === 'title' ? f.caps !== false : own.caps === true;
  return { font: own.font || role.font, caps };
}

/** A font choice in words: the title font, the text font or a family. */
export function fontName(font) {
  if (font === 'title') return 'Title font';
  if (font === 'text') return 'Text font';
  return font;
}
