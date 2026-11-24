// The Join slide on the stage: the session room's QR code, large, with the join
// link and the PIN, and how many phones have joined. While phones cannot reach
// the link (the room closed, no public URL, the last public-link check not ok)
// the QR gives way to a note that sends people to the chat instead.
// The join state comes from the live snapshot (ctx.join, src/room/polls.py).

import { esc } from '/static/js/ui.js';
import { words, spacedPin, shortUrl } from '/static/js/stage-words.js';
import { icon } from '/static/_vendored/icons/icons.js';

const SAMPLE_PIN = '123456';
const SAMPLE_URL = 'join.example.org/play';

export function render(body, result, ctx) {
  const w = words(ctx.lang);
  const j = ctx.join;
  const ok = !!(j && j.ok);
  const sample = !j && ctx.preview; // a presenter or Plan-tab preview: the layout with placeholders
  let root = body.querySelector('[data-join-slide]');
  if (!root) {
    body.innerHTML = '<div class="rj" data-join-slide><div class="rj-qr" data-join-big></div><div class="rj-side" data-join-side></div></div>';
    root = body.querySelector('[data-join-slide]');
  }
  root.dataset.ok = ok ? '1' : '0';
  // The QR is redrawn only when what it shows changes, so a phone joining never makes it blink.
  const want = ok ? `qr:${j.pin}` : sample ? 'sample' : 'off';
  const qr = root.querySelector('[data-join-big]');
  if (qr.dataset.shows !== want) {
    qr.dataset.shows = want;
    qr.classList.toggle('off', !ok);
    qr.innerHTML = ok ? `<img alt="" src="/api/quiz/qr.svg?pin=${encodeURIComponent(j.pin)}">`
      : sample ? '<span class="rj-qr-sample">QR</span>' : icon('smartphone');
  }
  const side = ok || sample
    ? `<p class="rj-label">${esc(w.join_link)}</p><p class="rj-url">${esc(ok ? shortUrl(j.url) : SAMPLE_URL)}</p>` +
      `<p class="rj-label">PIN</p><p class="rj-pin" data-join-pin>${esc(spacedPin(ok ? j.pin : SAMPLE_PIN))}</p>`
    : `<p class="rj-note" data-join-off>${icon('message-square')}<span>${esc(w.join_off)}</span></p>`;
  const count = j && j.member_count ? `<p class="rj-count">${icon('users')}<span>${esc(w.joined(j.member_count))}</span></p>` : '';
  root.querySelector('[data-join-side]').innerHTML = side + count;
}
