// Cards on the stage: the latest answers as cards with the person's name;
// a new card pops in, the oldest leaves when the grid is full.

import { esc } from '/static/js/ui.js';

export function render(body, result, ctx) {
  const cards = (result && result.cards) || [];
  let host = body.querySelector('.cd');
  if (!host) {
    body.innerHTML = '<div class="cd"></div>';
    host = body.querySelector('.cd');
  }
  const keep = new Set(cards.map((c) => String(c.id)));
  host.querySelectorAll('.cd-card').forEach((el) => { if (!keep.has(el.dataset.id)) el.remove(); });
  cards.forEach((c, i) => {
    let el = host.querySelector(`[data-id="${c.id}"]`);
    if (!el) {
      el = document.createElement('div');
      el.className = 'cd-card cd-enter';
      el.dataset.id = String(c.id);
      el.innerHTML = `<p class="cd-text">${esc(c.text)}</p><span class="cd-name"></span>`;
      host.appendChild(el);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('cd-enter')));
    }
    el.style.order = String(i);
    el.querySelector('.cd-name').textContent = ctx.names ? c.sender : '';
  });
}
