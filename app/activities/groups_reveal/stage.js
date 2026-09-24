// "Who are you with?" on the stage: every breakout room of the chosen round
// as a numbered card, so each person finds their name and their room number.
// The rooms come with the item (groups.yaml, via the live run) — no capture.

import { esc } from '/static/js/ui.js';

/** The largest type (30px down to 14px) at which the whole grid fits the body. */
function fit(body) {
  const grid = body.querySelector('.gr');
  if (!grid || !body.clientHeight) return; // not laid out yet: the observer calls again
  let size = 30;
  grid.style.setProperty('--size', `${size}px`);
  while (grid.scrollHeight > body.clientHeight + 1 && size > 14) {
    size -= 2;
    grid.style.setProperty('--size', `${size}px`);
  }
}

export function render(body, result, ctx) {
  const rooms = (ctx.item && ctx.item.rooms) || [];
  if (!rooms.length) {
    body.innerHTML = '<div class="gr-empty">Shuffle the groups in the Groups tab first.</div>';
    return;
  }
  const cols = rooms.length > 12 ? 4 : rooms.length > 6 ? 3 : 2;
  body.innerHTML = `<div class="gr" style="--cols:${cols}">` + rooms.map((names, i) =>
    `<div class="gr-room"><span class="gr-n c${i % 4}">${i + 1}</span>` +
    `<div class="gr-names">${names.map((n) => `<span>${esc(n)}</span>`).join('')}</div></div>`).join('') + '</div>';
  if (!body._grObserver) {
    body._grObserver = new ResizeObserver(() => fit(body));
    body._grObserver.observe(body);
  }
  fit(body);
}
