// Stars on the stage: the average as a number and as five stars filled to it,
// the spread (±) under it, and one row per rating, 5 stars down to 1, with a
// bar of its votes. The rows are fixed, so a first draw with no result yet
// still has every row to fill later.

const STAR = '<svg class="icon" aria-hidden="true"><use href="#i-star"/></svg>';

export function render(body, result) {
  const bins = (result && result.bins) || [];
  let host = body.querySelector('.stv');
  if (!host) {
    body.innerHTML = `<div class="stv"><div class="stv-avg"><b></b><span class="stv-stars">${STAR.repeat(5)}<span class="stv-on">${STAR.repeat(5)}</span></span><small></small></div>` +
      `<div class="stv-rows">${[5, 4, 3, 2, 1].map((v) => `<div class="stv-row" data-v="${v}"><span class="stv-label">${v} ${STAR}</span>` +
        '<span class="stv-track"><span class="stv-fill"></span></span><span class="stv-count"></span></div>').join('')}</div></div>`;
    host = body.querySelector('.stv');
  }
  const avg = result && result.average;
  const has = avg != null;
  host.querySelector('.stv-avg b').textContent = has ? avg.toLocaleString(undefined, { maximumFractionDigits: 1 }) : '–';
  host.querySelector('.stv-on').style.width = `${has ? (avg / 5) * 100 : 0}%`;
  host.querySelector('.stv-avg small').textContent = has && result.spread != null
    ? `± ${result.spread.toLocaleString(undefined, { maximumFractionDigits: 1 })}` : '';
  const max = Math.max(1, ...bins.map((b) => b.count));
  host.querySelectorAll('.stv-row').forEach((row) => {
    const b = bins.find((x) => String(x.value) === row.dataset.v) || { count: 0 };
    row.querySelector('.stv-fill').style.width = `${(b.count / max) * 100}%`;
    row.querySelector('.stv-count').textContent = b.count ? String(b.count) : '';
  });
}
