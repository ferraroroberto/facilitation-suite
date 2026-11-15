// Settings → Live tools → Copilot recordings: how long a session keeps the audio Copilot recorded.
// "Keep" (the default) removes nothing; with a number of days, recordings older than that are removed
// when the session is opened. Transcripts and hints are never removed.

import { api, esc, toast } from '/static/js/ui.js';
import { factRow, selectRow, settingsGroup } from '/static/js/views/settings-rows.js';

const PRESETS = [0, 7, 14, 30, 90, 365];
const label = (days) => (days === 0 ? 'Keep recordings' : `Remove after ${days} days`);

export function recordingGroup() {
  const { frag, card, foot } = settingsGroup('recordings', 'Copilot recordings', [], '');
  card.classList.add('recording-card');
  const message = (text) => { card.replaceChildren(factRow({ glyph: 'hard-drive', label: 'Retention', value: text })); };
  message('Loading…');

  function draw(d) {
    const days = d.recording_retention_days;
    const values = PRESETS.includes(days) ? PRESETS : [...PRESETS, days].sort((a, b) => a - b);
    const { row, select } = selectRow({
      glyph: 'hard-drive', label: 'Retention', aria: 'How long recordings are kept', track: 'settings.recording-retention',
      optionsHtml: values.map((v) => `<option value="${v}"${v === days ? ' selected' : ''}>${esc(label(v))}</option>`).join(''),
    });
    select.dataset.recDays = '';
    card.replaceChildren(row);
    foot.textContent = 'Applies to the audio files in each session’s Copilot folder, when that session is opened. Transcripts and hints are kept.';
    foot.hidden = false;
    select.addEventListener('change', async () => {
      try {
        draw(await api('/api/copilot/settings', { method: 'PUT', body: { recording_retention_days: Number(select.value) } }));
        toast(Number(select.value) ? 'Recordings are removed after that many days' : 'Recordings are kept');
      } catch (err) {
        toast(err.message, 'error');
        draw(d);
      }
    });
  }

  api('/api/copilot/settings').then(draw).catch((e) => message(e.message));
  return frag;
}
