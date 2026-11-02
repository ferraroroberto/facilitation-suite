// Feed on the stage: answers flow in as bubbles, the newest first and
// largest; older ones shrink and fade as they move along.

import { esc } from '/static/js/ui.js';

export function render(body, result, ctx) {
  const items = (result && result.items) || [];
  let host = body.querySelector('.fd');
  if (!host) {
    body.innerHTML = '<div class="fd"></div>';
    host = body.querySelector('.fd');
  }
  const keep = new Set(items.map((m) => String(m.id)));
  host.querySelectorAll('.fd-bubble').forEach((el) => { if (!keep.has(el.dataset.id)) el.remove(); });
  items.forEach((m, i) => {
    let el = host.querySelector(`[data-id="${m.id}"]`);
    if (!el) {
      el = document.createElement('div');
      el.className = 'fd-bubble fd-enter';
      el.dataset.id = String(m.id);
      el.innerHTML = `<span class="fd-name"></span><p class="fd-text">${esc(m.text)}</p>`;
      host.appendChild(el);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('fd-enter')));
    }
    el.style.order = String(i);
    el.classList.toggle('fd-newest', i === 0);
    el.style.setProperty('--age', String(Math.min(i, 8)));
    el.querySelector('.fd-name').textContent = ctx.names ? m.sender : '';
  });
}
