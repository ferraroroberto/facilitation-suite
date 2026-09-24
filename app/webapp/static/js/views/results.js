// Results view — placeholder until its step lands.

import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { pageHead } from '/static/js/ui.js';

export function mount(el) {
  el.appendChild(pageHead({ glyph: 'chart-column', title: 'Results' }));
  const card = document.createElement('div');
  card.className = 'card';
  card.appendChild(emptyStateEl('chart-column', 'Results appear after a live session.'));
  el.appendChild(card);
}
