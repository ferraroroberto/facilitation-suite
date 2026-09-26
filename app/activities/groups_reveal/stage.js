// "Who are you with?" on the stage: every breakout room of the chosen round
// as a numbered card, so each person finds their name and their room number.
// The rooms come with the item (groups.yaml, via the live run) — no capture.

import { esc } from '/static/js/ui.js';
import { words } from '/static/js/stage-words.js';

/** Under the title: the round and how many rooms — it follows the round picked in the plan. */
export function subtitle(item, lang) {
  const w = words(lang);
  const round = (item.options || {}).round || 'pairs';
  const n = (item.rooms || []).length;
  return `${w.rounds[round] || round}${n ? ` · ${w.rooms(n)}` : ''}`;
}

/** Does the grid spill out of the body, or cut a name short? */
function overflows(grid, body) {
  return grid.scrollHeight > body.clientHeight + 1 ||
    [...grid.querySelectorAll('.gr-names span')].some((s) => s.scrollWidth > s.clientWidth + 1);
}

/** The largest type (30px down to 14px) at which the grid fits with `cols` columns. */
function largest(grid, body, cols) {
  grid.style.setProperty('--cols', cols);
  let size = 30;
  grid.style.setProperty('--size', `${size}px`);
  while (overflows(grid, body) && size > 14) {
    size -= 2;
    grid.style.setProperty('--size', `${size}px`);
  }
  return size;
}

/**
 * The column count giving the largest type: the room the camera zone leaves
 * (full width, or beside the strip) decides it, not only the number of rooms.
 */
function fit(body) {
  const grid = body.querySelector('.gr');
  if (!grid || !body.clientHeight) return; // not laid out yet: the observer calls again
  const most = Math.max(1, Math.min(4, Math.floor(body.clientWidth / 280), grid.children.length));
  let best = { size: 0, cols: 1 };
  for (let cols = 1; cols <= most; cols += 1) {
    const size = largest(grid, body, cols);
    if (size >= best.size) best = { size, cols }; // a tie takes more columns: shorter lists read faster
  }
  largest(grid, body, best.cols);
}

export function render(body, result, ctx) {
  const rooms = (ctx.item && ctx.item.rooms) || [];
  if (!rooms.length) {
    body.innerHTML = `<div class="gr-empty">${esc(words(ctx.lang).no_rooms)}</div>`;
    return;
  }
  body.innerHTML = '<div class="gr">' + rooms.map((names, i) =>
    `<div class="gr-room"><span class="gr-n c${i % 4}">${i + 1}</span>` +
    `<div class="gr-names">${names.map((n) => `<span>${esc(n)}</span>`).join('')}</div></div>`).join('') + '</div>';
  if (!body._grObserver) {
    body._grObserver = new ResizeObserver(() => fit(body));
    body._grObserver.observe(body);
  }
  fit(body);
}
