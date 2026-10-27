// Word cloud on the stage: the most frequent words biggest, laid out on a
// spiral from the centre. Each word keeps its colour (hashed from its key)
// and its element, so a word that grows animates instead of flashing.
// Every size comes from its count, then one factor scales them all so the
// whole set fits the body; an entry that still has no room at the smallest
// readable size is left out, never silently: the stage says "+N more" and the
// presenter hears how many through the `wc-left-out` event.
// An entry of more than three words (only a verbatim answer has one) is
// capped so it fits the cloud and, from six words, wraps onto balanced lines
// of about four words; shorter entries are laid out exactly as always.

import { words as stageWords } from '/static/js/stage-words.js';

const PALETTE = ['--st-c1', '--st-c2', '--st-c3', '--st-c4', '--st-c5', '--st-c6'];
const MIN_PX = 34;
const FLOOR_PX = 22; // the smallest word that still reads on a shared screen
const FILL = 0.5; // the share of the body the word boxes may cover: beyond it the spiral cannot pack them
const SHRINK = 0.92; // one step of the common scale when the set does not fit
const NOTE_PX = 30; // the "+N more" line at the foot of the body
const NOTE_ROOM = NOTE_PX * 1.8;
const PHRASE_MAX_WORDS = 3; // up to this many words: one line (every entry of the automatic and words modes)
const WORDS_PER_LINE = 4;
const REF_PX = 100; // widths are measured once at this size and scaled
const fontsAsked = new WeakMap(); // body -> the font specs it already waits for
const latest = new WeakMap(); // body -> the last render's arguments, for the re-layout once a font has loaded

function hash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

let measureCtx = null;
/** A text's width at `px` in `font` (its weight and family; caps: in capitals). */
function measure(text, px, font) {
  measureCtx = measureCtx || document.createElement('canvas').getContext('2d');
  measureCtx.font = `${font.weight} ${px}px ${font.family}`;
  return measureCtx.measureText(font.caps ? text.toUpperCase() : text).width;
}

/** An entry's lines: one up to five words, else balanced lines of about four words. */
function wrap(text) {
  const words = text.split(' ');
  const count = Math.round(words.length / WORDS_PER_LINE);
  if (count < 2) return [text];
  const target = text.length / count;
  const lines = [];
  let line = '';
  for (const word of words) {
    if (line && line.length + 1 + word.length / 2 > target) { lines.push(line); line = word; } else line = line ? `${line} ${word}` : word;
  }
  lines.push(line);
  return lines;
}

function overlaps(r, placed) {
  return placed.some((p) => r.x < p.x + p.w && r.x + r.w > p.x && r.y < p.y + p.h && r.y + r.h > p.y);
}

/** The names under a word, as drawn. */
function namesText(word) {
  const n = word.names || [];
  return n.slice(0, 3).join(', ') + (n.length > 3 ? ` +${n.length - 3}` : '');
}

/** An entry's lines and its widths at REF_PX, measured once: its box at any size follows by scaling. */
function prepare(word, font, uiFont, names) {
  const long = word.text.split(' ').length > PHRASE_MAX_WORDS;
  const lines = long ? wrap(word.text) : [word.text];
  const nameText = names ? namesText(word) : '';
  return {
    word,
    long,
    lines,
    wide: Math.max(...lines.map((l) => measure(l, REF_PX, font))),
    namesWide: nameText ? measure(nameText, REF_PX * 0.26, uiFont) : 0,
    tall: (lines.length - 1) * 1.05 + (nameText ? 1.5 : 1.2),
  };
}

/** The box of an entry at `px`: its widest line or names line, a pad for the ink beyond the glyphs and the stroke. */
function box(entry, px, stroke) {
  return {
    tw: Math.max(entry.wide, entry.namesWide) * (px / REF_PX) + px * 0.35 + stroke * 2,
    th: px * entry.tall + stroke * 2,
  };
}

/** The spot for a tw×th box on a spiral from the centre of the w×h body, or null. */
function findSpot(tw, th, w, h, placed) {
  for (let t = 0; t < 900; t += 1) {
    const a = t * 0.19;
    const r = 4 * a;
    const x = w / 2 + r * Math.cos(a) * 1.6 - tw / 2;
    const y = h / 2 + r * Math.sin(a) - th / 2;
    if (x < 0 || y < 0 || x + tw > w || y + th > h) continue;
    const rect = { x, y, w: tw, h: th };
    if (!overlaps(rect, placed)) return rect;
  }
  return null;
}

/**
 * Positions for the entries (most answers first) inside w×h. Sizes follow the counts, scaled by one
 * common factor until the set fits; at the smallest readable size the entries from the first one with
 * no room on are left out, so an entry with more answers is never left out while one with fewer is drawn.
 */
function layout(entries, w, h, stroke) {
  if (!entries.length) return { spots: [], left: 0 };
  const maxCount = Math.max(...entries.map((e) => e.word.count));
  const maxPx = Math.min(150, h / (entries.length < 4 ? 3 : 4.2));
  for (const e of entries) {
    e.ideal = MIN_PX + (maxPx - MIN_PX) * Math.sqrt(e.word.count / maxCount);
    if (e.long) { // a long answer: no wider than 80% of the cloud, no taller than 40%
      const { tw, th } = box(e, e.ideal, stroke);
      e.ideal *= Math.min(1, (w * 0.8) / tw, (h * 0.4) / th);
    }
  }
  const area = entries.reduce((sum, e) => { const { tw, th } = box(e, e.ideal, stroke); return sum + tw * th; }, 0);
  let scale = Math.min(1, Math.sqrt((FILL * w * h) / area));
  for (;;) {
    const sizes = entries.map((e) => Math.min(e.ideal, Math.max(FLOOR_PX, e.ideal * scale)));
    const atFloor = sizes.every((px, i) => px <= Math.min(entries[i].ideal, FLOOR_PX));
    const placed = [];
    const spots = [];
    for (let i = 0; i < entries.length; i += 1) {
      const { tw, th } = box(entries[i], sizes[i], stroke);
      const spot = findSpot(tw, th, w, h, placed);
      if (!spot) break;
      placed.push(spot);
      spots.push({ entry: entries[i], px: sizes[i], x: spot.x, y: spot.y });
    }
    if (spots.length === entries.length || atFloor) return { spots, left: entries.length - spots.length };
    scale *= SHRINK;
  }
}

/** The "+N more" note at the foot of the body, made on the first entry left out. */
function drawNote(host, left, lang) {
  let note = host.querySelector('.wc-more');
  if (!left) { if (note) note.remove(); return; }
  if (!note) {
    note = document.createElement('div');
    note.className = 'wc-more';
    host.appendChild(note);
  }
  note.style.fontSize = `${NOTE_PX}px`;
  note.textContent = stageWords(lang).more(left);
}

export function render(body, result, ctx) {
  latest.set(body, { result, ctx });
  const again = () => { const l = latest.get(body); if (l) render(body, l.result, l.ctx); };
  const entries = (result && result.words) || [];
  let host = body.querySelector('.wc');
  if (!host) {
    body.innerHTML = '<div class="wc"></div>';
    host = body.querySelector('.wc');
  }
  const css = getComputedStyle(body);
  const font = {
    family: css.getPropertyValue('--st-font').trim() || 'system-ui, sans-serif',
    weight: css.getPropertyValue('--st-font-weight').trim() || 400,
    caps: css.textTransform === 'uppercase',
  };
  const uiFont = { family: css.getPropertyValue('--st-ui-font').trim() || font.family, weight: 600, caps: false };
  const stroke = parseFloat(css.getPropertyValue('--st-font-stroke')) || 0;
  // Widths are measured in the theme font: lay out again once it has loaded.
  if (document.fonts) {
    if (document.fonts.status !== 'loaded') document.fonts.ready.then(again);
    const asked = fontsAsked.get(body) || new Set();
    fontsAsked.set(body, asked);
    for (const f of [font, uiFont]) {
      const spec = `${f.weight} 40px ${f.family}`;
      if (!asked.has(spec)) { asked.add(spec); document.fonts.load(spec).then(again, () => {}); }
    }
  }
  const prepared = entries.map((word) => prepare(word, font, uiFont, ctx.names));
  const w = body.clientWidth;
  const h = body.clientHeight;
  let { spots, left } = layout(prepared, w, h, stroke);
  if (left) ({ spots, left } = layout(prepared, w, h - NOTE_ROOM, stroke)); // room for the note
  drawNote(host, left, ctx.lang);
  host.dataset.leftOut = String(left);
  host.dispatchEvent(new CustomEvent('wc-left-out', { bubbles: true, detail: { left } }));
  const keep = new Set();
  for (const s of spots) {
    const word = s.entry.word;
    const key = word.key;
    keep.add(key);
    let el = host.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!el) {
      el = document.createElement('span');
      el.className = 'wc-word wc-enter';
      el.dataset.key = key;
      el.style.color = `var(${PALETTE[hash(key) % PALETTE.length]})`;
      el.innerHTML = '<span class="wc-text"></span><small class="wc-names"></small>';
      host.appendChild(el);
      requestAnimationFrame(() => el.classList.remove('wc-enter'));
    }
    el.querySelector('.wc-text').textContent = s.entry.lines.join('\n');
    el.classList.toggle('wc-lines', s.entry.lines.length > 1);
    el.querySelector('.wc-names').textContent = ctx.names ? namesText(word) : '';
    el.style.fontSize = `${Math.round(s.px)}px`;
    el.style.transform = `translate(${Math.round(s.x)}px, ${Math.round(s.y)}px)`;
  }
  host.querySelectorAll('.wc-word').forEach((el) => { if (!keep.has(el.dataset.key)) el.remove(); });
}
