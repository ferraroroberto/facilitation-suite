// Number on the stage: the median up top (with the unit and the range of the
// answers), then a histogram: a bar per whole number, or per range for wide
// or decimal answers, its count above it.

import { esc } from '/static/js/ui.js';

const WORDS = { en: { median: 'median', range: 'from {a} to {b}' }, es: { median: 'mediana', range: 'de {a} a {b}' } };
const fmt = (x) => x.toLocaleString(undefined, { maximumFractionDigits: 2 });

export function render(body, result, ctx) {
  const w = WORDS[ctx.lang] || WORDS.en;
  const bars = (result && result.bars) || [];
  const sig = JSON.stringify(bars.map((b) => b.label));
  let host = body.querySelector('.nm');
  if (!host) {
    body.innerHTML = '<div class="nm"><div class="nm-mid"><b></b><span class="nm-unit"></span><small></small></div><div class="nm-bars"></div></div>';
    host = body.querySelector('.nm');
  }
  const box = host.querySelector('.nm-bars');
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    box.dataset.n = String(bars.length);
    box.innerHTML = bars.map((b) => '<div class="nm-col"><span class="nm-bar"><span class="nm-count"></span><span class="nm-fill"></span></span>' +
      `<span class="nm-label">${esc(b.label)}</span></div>`).join('');
  }
  const max = Math.max(1, ...bars.map((b) => b.count));
  box.querySelectorAll('.nm-col').forEach((col, i) => {
    const b = bars[i];
    col.querySelector('.nm-count').textContent = b.count ? String(b.count) : '';
    col.querySelector('.nm-fill').style.height = `${(b.count / max) * 100}%`;
  });
  const has = result && result.median != null;
  host.querySelector('.nm-mid b').textContent = has ? fmt(result.median) : '–';
  host.querySelector('.nm-unit').textContent = has && result.unit ? result.unit : '';
  host.querySelector('.nm-mid small').textContent = has
    ? `${w.median}${result.votes > 1 ? ` · ${w.range.replace('{a}', fmt(result.lowest)).replace('{b}', fmt(result.highest))}` : ''}` : '';
}
