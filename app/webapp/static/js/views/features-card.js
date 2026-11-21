// Settings → Available features: one switch per feature block (src/features.py), saved for this PC in
// config.json. A block switched off hides its parts on every page and stops its background work; a
// block that cannot run on this PC still has its switch and says why under its name.

import { api, toast } from '/static/js/ui.js';
import { settingsGroup, toggleRow } from '/static/js/views/settings-rows.js';

const GLYPHS = {
  chat_reader: 'message-square', obs: 'video', copilot: 'wand-sparkles', music: 'music', quiz_public: 'rocket', remote: 'smartphone',
};

/** The blocks switched off, from a Settings payload's `features` list. */
export const offKeys = (list) => new Set((list || []).filter((f) => !f.enabled).map((f) => f.key));

/** The group: `list` is the payload's `features`; `onSaved(payload)` gets the Settings payload after a switch. */
export function featuresGroup(list, onSaved) {
  const rows = list.map((f) => {
    const hint = f.available ? f.covers : `Can't run on this PC: ${f.reason}.`;
    const row = toggleRow({
      glyph: GLYPHS[f.key] || 'plug', label: f.title, hint, on: f.enabled, track: 'settings.feature',
      onToggle: async (next) => {
        try {
          onSaved(await api('/api/settings/features', { method: 'PUT', body: { [f.key]: next } }));
          toast(`${f.title} ${next ? 'on' : 'off'}${f.restart ? ` — restart ${f.restart} to apply` : ''}`);
        } catch (e) { toast(e.message, 'error'); }
      },
    });
    row.dataset.featureKey = f.key; // not data-feature: that attribute hides an element while its block is off
    return row;
  });
  return settingsGroup('features', 'Available features', rows,
    'What this PC runs. Switching a block off hides it everywhere and stops its work; the choice is saved for this PC only.').frag;
}
