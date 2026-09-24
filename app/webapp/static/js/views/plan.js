// Plan view — placeholder until its step lands.

import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { pageHead } from '/static/js/ui.js';

export function mount(el) {
  el.appendChild(pageHead({ glyph: 'presentation', title: 'Plan' }));
  const card = document.createElement('div');
  card.className = 'card';
  card.appendChild(emptyStateEl('presentation', 'Open a session to plan it.'));
  el.appendChild(card);
}
