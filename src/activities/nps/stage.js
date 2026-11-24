// NPS on the stage: the score (-100 to 100) big, a bar split into detractors,
// passives and promoters by their share, and each group's count and share
// under it.

const GROUPS = {
  en: [['detractors', 'Detractors', '0–6'], ['passives', 'Passives', '7–8'], ['promoters', 'Promoters', '9–10']],
  es: [['detractors', 'Detractores', '0–6'], ['passives', 'Pasivos', '7–8'], ['promoters', 'Promotores', '9–10']],
};

export function render(body, result, ctx) {
  const groups = GROUPS[ctx.lang] || GROUPS.en;
  let host = body.querySelector('.nps');
  if (!host) {
    body.innerHTML = '<div class="nps"><div class="nps-score"><b></b><span>NPS</span></div>' +
      `<div class="nps-bar">${groups.map(([k]) => `<span class="nps-seg" data-g="${k}"></span>`).join('')}</div>` +
      `<div class="nps-legend">${groups.map(([k, label, range]) => `<div class="nps-g" data-g="${k}"><i></i>` +
        `<span class="nps-name">${label} <small>${range}</small></span><b class="nps-n"></b></div>`).join('')}</div></div>`;
    host = body.querySelector('.nps');
  }
  const score = result && result.score;
  host.querySelector('.nps-score b').textContent = score == null ? '–' : score > 0 ? `+${score}` : String(score);
  host.querySelector('.nps-score').dataset.sign = score == null ? '' : score > 0 ? 'up' : score < 0 ? 'down' : '';
  const votes = (result && result.votes) || 0;
  groups.forEach(([k]) => {
    const g = (result && result[k]) || { count: 0, percent: 0 };
    const seg = host.querySelector(`.nps-seg[data-g="${k}"]`);
    seg.style.flexGrow = String(votes ? g.count : 1);
    seg.textContent = g.count ? `${g.percent}%` : '';
    host.querySelector(`.nps-g[data-g="${k}"] .nps-n`).textContent = votes ? `${g.count} · ${g.percent}%` : '0';
  });
}
