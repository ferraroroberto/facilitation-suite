// /presenter — the chat's excluded senders: how a name is compared and the "Not counted" bar
// above the chat list, each excluded sender one click from counting again.

import { icon } from '/static/_vendored/icons/icons.js';
import { esc } from '/static/js/ui.js';

/** The same name as the server reads it: spacing and case do not make another sender. */
export const senderKey = (name) => String(name).split(/\s+/).filter(Boolean).join(' ').toLowerCase();

/** Fill the bar with one chip per excluded sender (hidden when there are none). */
export function drawExcluded(bar, names) {
  bar.hidden = !names.length;
  bar.innerHTML = names.length
    ? `<span class="small muted">Not counted</span>` + names.map((n) =>
      `<button type="button" class="chip" data-restore="${esc(n)}" data-track="live.include-sender" title="Count ${esc(n)} again">${esc(n)} ${icon('x')}</button>`).join('')
    : '';
}
