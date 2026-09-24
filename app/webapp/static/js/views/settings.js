// Settings view — the credits now; OBS and Stream Deck settings land with their steps.

import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { icon } from '/static/_vendored/icons/icons.js';
import { pageHead } from '/static/js/ui.js';

const CREDITS = [
  ['GeoNames', 'https://www.geonames.org', 'the map\'s cities and countries (cities15000), licensed CC BY 4.0'],
  ['Natural Earth · world-atlas', 'https://github.com/topojson/world-atlas', 'the country outlines, public domain'],
  ['Unicode CLDR', 'https://cldr.unicode.org', 'country names in Spanish and English'],
  ['Patrick Hand', 'https://fonts.google.com/specimen/Patrick+Hand', 'the stage lettering, SIL Open Font License'],
  ['Lucide', 'https://lucide.dev', 'icons, ISC license'],
];

export function mount(el) {
  el.appendChild(pageHead({ glyph: 'settings', title: 'Settings' }));
  const card = document.createElement('div');
  card.className = 'card';
  card.appendChild(emptyStateEl('settings', 'OBS and Stream Deck settings arrive with their steps.'));
  el.appendChild(card);

  const credits = document.createElement('div');
  credits.className = 'card credits';
  credits.innerHTML = `<div class="card-head"><h3 class="card-title">${icon('file-text')} Credits</h3></div>` +
    `<ul class="credit-list">${CREDITS.map(([name, url, what]) =>
      `<li><a href="${url}" target="_blank" rel="noopener">${name}</a> — ${what}</li>`).join('')}</ul>`;
  el.appendChild(credits);
}
