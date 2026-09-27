// Word cloud on the stage: the most frequent words biggest, laid out on a
// spiral from the centre. Each word keeps its colour (hashed from its key)
// and its element, so a word that grows animates instead of flashing.

const PALETTE = ['--st-c1', '--st-c2', '--st-c3', '--st-c4', '--st-c5', '--st-c6'];
const MIN_PX = 34;

function hash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

let measureCtx = null;
function measure(text, px, family) {
  measureCtx = measureCtx || document.createElement('canvas').getContext('2d');
  measureCtx.font = `${px}px ${family}`;
  return measureCtx.measureText(text).width;
}

function overlaps(r, placed) {
  return placed.some((p) => r.x < p.x + p.w && r.x + r.w > p.x && r.y < p.y + p.h && r.y + r.h > p.y);
}

/** Positions for the words (largest first) inside w×h; unplaceable words are dropped. */
function layout(words, w, h, family, names) {
  if (!words.length) return [];
  const maxCount = Math.max(...words.map((x) => x.count));
  const maxPx = Math.min(150, h / (words.length < 4 ? 3 : 4.2));
  const placed = [];
  const out = [];
  for (const word of words) {
    let px = MIN_PX + (maxPx - MIN_PX) * Math.sqrt(word.count / maxCount);
    let spot = null;
    for (let attempt = 0; attempt < 3 && !spot; attempt += 1) {
      const tw = measure(word.text, px, family) + px * 0.35;
      const th = px * (names && word.names.length ? 1.5 : 1.12);
      for (let t = 0; t < 900; t += 1) {
        const a = t * 0.19;
        const r = 4 * a;
        const x = w / 2 + r * Math.cos(a) * 1.6 - tw / 2;
        const y = h / 2 + r * Math.sin(a) - th / 2;
        if (x < 0 || y < 0 || x + tw > w || y + th > h) continue;
        const rect = { x, y, w: tw, h: th };
        if (!overlaps(rect, placed)) { spot = rect; break; }
      }
      if (!spot) px *= 0.8;
    }
    if (!spot) continue;
    placed.push(spot);
    out.push({ word, px, x: spot.x, y: spot.y });
  }
  return out;
}

export function render(body, result, ctx) {
  // Widths are measured in the theme font: lay out again once it has loaded.
  if (document.fonts && document.fonts.status !== 'loaded') document.fonts.ready.then(() => render(body, result, ctx));
  const words = (result && result.words) || [];
  let host = body.querySelector('.wc');
  if (!host) {
    body.innerHTML = '<div class="wc"></div>';
    host = body.querySelector('.wc');
  }
  const family = getComputedStyle(body).getPropertyValue('--st-font') || '"Patrick Hand", sans-serif';
  const spots = layout(words, body.clientWidth, body.clientHeight, family, ctx.names);
  const keep = new Set();
  for (const s of spots) {
    const key = s.word.key;
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
    el.querySelector('.wc-text').textContent = s.word.text;
    const n = s.word.names || [];
    el.querySelector('.wc-names').textContent = ctx.names && n.length ? n.slice(0, 3).join(', ') + (n.length > 3 ? ` +${n.length - 3}` : '') : '';
    el.style.fontSize = `${Math.round(s.px)}px`;
    el.style.transform = `translate(${Math.round(s.x)}px, ${Math.round(s.y)}px)`;
  }
  host.querySelectorAll('.wc-word').forEach((el) => { if (!keep.has(el.dataset.key)) el.remove(); });
}
