// Sessions view — placeholder until its step lands.

import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { pageHead } from '/static/js/ui.js';

export function mount(el) {
  el.appendChild(pageHead({ glyph: 'calendar-days', title: 'Sessions' }));
  const card = document.createElement('div');
  card.className = 'card';
  card.appendChild(emptyStateEl('calendar-days', 'No sessions yet — sessions arrive in step 2.'));
  el.appendChild(card);
}
