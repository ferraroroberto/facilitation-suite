// Settings view — placeholder until its step lands.

import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { pageHead } from '/static/js/ui.js';

export function mount(el) {
  el.appendChild(pageHead({ glyph: 'settings', title: 'Settings' }));
  const card = document.createElement('div');
  card.className = 'card';
  card.appendChild(emptyStateEl('settings', 'Settings arrive with OBS and Stream Deck.'));
  el.appendChild(card);
}
