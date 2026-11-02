// Scale on the stage: the average up top, one bar per value with its count,
// the leading bar highlighted, labels "value · keyword". Bars grow with a
// transition as votes arrive.

export function render(body, result, ctx) {
  const bins = (result && result.bins) || [];
  let host = body.querySelector('.sc');
  if (!host || host.dataset.n !== String(bins.length)) {
    body.innerHTML = `<div class="sc" data-n="${bins.length}"><div class="sc-avg"></div><div class="sc-bars"></div></div>`;
    host = body.querySelector('.sc');
    host.querySelector('.sc-bars').innerHTML = bins.map(() =>
      '<div class="sc-col"><span class="sc-bar"><span class="sc-count"></span><span class="sc-fill"></span></span>' +
      '<span class="sc-label"></span><small class="sc-names"></small></div>').join('');
  }
  const max = Math.max(1, ...bins.map((b) => b.count));
  const lead = bins.some((b) => b.count) ? max : -1;
  host.querySelectorAll('.sc-col').forEach((col, i) => {
    const b = bins[i];
    col.classList.toggle('lead', b.count === lead);
    col.querySelector('.sc-count').textContent = b.count ? String(b.count) : '';
    col.querySelector('.sc-fill').style.height = `${(b.count / max) * 100}%`;
    col.querySelector('.sc-label').textContent = b.label === String(b.value) ? b.label : `${b.value} · ${b.label}`;
    const n = b.names || [];
    col.querySelector('.sc-names').textContent = ctx.names && n.length ? n.slice(0, 4).join(', ') + (n.length > 4 ? ` +${n.length - 4}` : '') : '';
  });
  const showAvg = ctx.options.show_average !== false && result && result.average != null;
  host.querySelector('.sc-avg').innerHTML = showAvg
    ? `<b>${result.average.toLocaleString(undefined, { maximumFractionDigits: 1 })}</b>` +
      `<svg class="icon" aria-hidden="true"><use href="#i-gauge"></use></svg>` : '';
}
