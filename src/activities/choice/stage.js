// Choice on the stage: one horizontal bar per lettered choice, its count and
// share at the end, the leading choice in full colour. Bars grow with a
// transition as votes arrive.

import { esc } from '/static/js/ui.js';

const names = (n) => (n.length ? n.slice(0, 4).join(', ') + (n.length > 4 ? ` +${n.length - 4}` : '') : '');

export function render(body, result, ctx) {
  const bars = (result && result.bars) || [];
  const sig = JSON.stringify(bars.map((b) => [b.key, b.label]));
  let host = body.querySelector('.ch');
  if (!host || host.dataset.sig !== sig) {
    body.innerHTML = `<div class="ch" data-n="${bars.length}"></div>`;
    host = body.querySelector('.ch');
    host.dataset.sig = sig;
    host.innerHTML = bars.map((b) => '<div class="ch-row">' +
      `<span class="ch-key">${esc(b.key)}</span>` +
      `<span class="ch-main"><span class="ch-label">${esc(b.label)}</span>` +
      '<span class="ch-track"><span class="ch-fill"></span></span><small class="ch-names"></small></span>' +
      '<span class="ch-count"></span></div>').join('');
  }
  const max = Math.max(1, ...bars.map((b) => b.count));
  const lead = bars.some((b) => b.count) ? max : -1;
  const pct = ctx.options.show_percent !== false;
  host.querySelectorAll('.ch-row').forEach((row, i) => {
    const b = bars[i];
    row.classList.toggle('lead', b.count === lead);
    row.querySelector('.ch-fill').style.width = `${(b.count / max) * 100}%`;
    row.querySelector('.ch-count').textContent = pct ? `${b.count} · ${b.percent}%` : String(b.count);
    row.querySelector('.ch-names').textContent = ctx.names ? names(b.names || []) : '';
  });
}
