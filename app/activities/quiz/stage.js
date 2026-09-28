// A quiz question on the stage (minimal until Step 5 of #34): the question is
// the title; the body shows its answers as four coloured tiles, A to D. The
// correct answer is not marked: the stage shows the question as players see it.

import { esc } from '/static/js/ui.js';

const KEYS = ['A', 'B', 'C', 'D'];

export function render(body, result, ctx) {
  const o = ctx.options || {};
  const tiles = KEYS.map((key, i) => ({ key, i, text: String(o[`answer_${i + 1}`] ?? '').trim() })).filter((a) => a.text);
  body.innerHTML = '<div class="qz-answers">' + tiles.map((a) =>
    `<div class="qz-answer c${a.i}"><span class="qz-key">${a.key}</span><span class="qz-text">${esc(a.text)}</span></div>`).join('') + '</div>';
}
