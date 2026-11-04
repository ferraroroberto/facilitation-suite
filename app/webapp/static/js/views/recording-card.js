// Settings → Live tools → Copilot recordings: how long a session keeps the audio Copilot recorded.
// "Keep" (the default) removes nothing; with a number of days, recordings older than that are removed
// when the session is opened. Transcripts and hints are never removed.

import { icon } from '/static/_vendored/icons/icons.js';
import { api, esc, toast } from '/static/js/ui.js';

const PRESETS = [0, 7, 14, 30, 90, 365];
const label = (days) => (days === 0 ? 'Keep recordings' : `Remove after ${days} days`);

export function recordingCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card recording-card';
  const head = `<div class="card-head"><h3 class="card-title">${icon('hard-drive')} Copilot recordings</h3></div>`;
  card.innerHTML = head + '<p class="small muted">Loading…</p>';

  function draw(d) {
    const days = d.recording_retention_days;
    const values = PRESETS.includes(days) ? PRESETS : [...PRESETS, days].sort((a, b) => a - b);
    card.innerHTML = head +
      '<div class="font-rows">' +
      `<label class="font-row"><span class="small">Retention</span><select class="select-native" aria-label="How long recordings are kept" data-rec-days data-track="settings.recording-retention">` +
      values.map((v) => `<option value="${v}"${v === days ? ' selected' : ''}>${esc(label(v))}</option>`).join('') +
      '</select></label></div>' +
      '<p class="small muted settings-note">Applies to the audio files in each session’s Copilot folder, when that session is opened. Transcripts and hints are kept.</p>';
    card.querySelector('[data-rec-days]').addEventListener('change', async (e) => {
      try {
        draw(await api('/api/copilot/settings', { method: 'PUT', body: { recording_retention_days: Number(e.target.value) } }));
        toast(Number(e.target.value) ? 'Recordings are removed after that many days' : 'Recordings are kept');
      } catch (err) {
        toast(err.message, 'error');
        draw(d);
      }
    });
  }

  api('/api/copilot/settings').then(draw).catch((e) => {
    card.innerHTML = head + `<p class="small muted">${esc(e.message)}</p>`;
  });
  return card;
}
