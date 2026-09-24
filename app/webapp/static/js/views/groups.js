// Groups view — placeholder until its step lands.

import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { pageHead } from '/static/js/ui.js';

export function mount(el) {
  el.appendChild(pageHead({ glyph: 'shuffle', title: 'Groups' }));
  const card = document.createElement('div');
  card.className = 'card';
  card.appendChild(emptyStateEl('shuffle', 'Groups arrive with the roster import.'));
  el.appendChild(card);
}
