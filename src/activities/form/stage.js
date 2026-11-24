// A feedback form on the stage: never its answers. While the form is open and its
// link is reachable it shows the form's QR code and link, large, with the response
// count when the form's "show the count" setting is on; open but not reachable, a
// note sends people to the link in the chat; closed, a thank-you.
// The form's state comes from the live snapshot (ctx.join, src/forms/service.py).

import { esc } from '/static/js/ui.js';
import { words, shortUrl } from '/static/js/stage-words.js';
import { icon } from '/static/_vendored/icons/icons.js';

const SAMPLE_URL = 'join.example.org/play/form';

export function render(body, result, ctx) {
  const w = words(ctx.lang);
  const j = ctx.join;
  const sample = !j && ctx.preview; // a presenter or Plan-tab preview: the layout with placeholders
  const state = j && j.open ? (j.ok ? 'open' : 'off') : sample ? 'sample' : 'closed';
  let root = body.querySelector('[data-form-slide]');
  if (!root) {
    body.innerHTML = '<div class="fm" data-form-slide><div class="rj-qr" data-form-qr></div><div class="rj-side" data-form-side></div></div>';
    root = body.querySelector('[data-form-slide]');
  }
  root.dataset.state = state;
  // The QR is redrawn only when its link changes, so a new response never makes it blink.
  const want = state === 'open' ? `qr:${j.url}` : state;
  const qr = root.querySelector('[data-form-qr]');
  if (qr.dataset.shows !== want) {
    qr.dataset.shows = want;
    qr.classList.toggle('off', state !== 'open');
    qr.innerHTML = state === 'open' ? `<img alt="" src="/api/forms/qr.svg?item=${encodeURIComponent(j.item_id)}&v=${encodeURIComponent(j.url)}">`
      : state === 'sample' ? '<span class="rj-qr-sample">QR</span>' : icon(state === 'closed' ? 'lock' : 'message-square');
  }
  const side = state === 'open' || state === 'sample'
    ? `<p class="rj-label">${esc(w.feedback_open)}</p><p class="rj-url">${esc(state === 'open' ? shortUrl(j.url) : SAMPLE_URL)}</p>`
    : `<p class="rj-note" data-form-note>${icon(state === 'closed' ? 'lock' : 'message-square')}<span>${esc(state === 'closed' ? w.feedback_closed : w.feedback_off)}</span></p>`;
  const count = j && j.open && typeof j.count === 'number'
    ? `<p class="rj-count" data-form-count>${icon('users')}<span>${esc(w.responses(j.count))}</span></p>` : '';
  root.querySelector('[data-form-side]').innerHTML = side + count;
}
