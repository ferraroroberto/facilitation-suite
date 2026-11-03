// Settings → Live tools → Copilot overlay: where the live copilot's overlay window sits (which screen,
// which corner), what it shows (the hint itself, or only a cue that sends the eye to the presenter)
// and how it looks. Every save moves an open overlay at once and puts a sample there, so finding the
// right spot on the prompter screen is a matter of picking and looking.

import { icon } from '/static/_vendored/icons/icons.js';
import { api, esc, toast, switchRow } from '/static/js/ui.js';
import { formDialog } from '/static/js/dialogs.js';

const POSITION_LABEL = {
  'top-left': 'Top left', 'top-center': 'Top centre', 'top-right': 'Top right', center: 'Centre',
  'bottom-left': 'Bottom left', 'bottom-center': 'Bottom centre', 'bottom-right': 'Bottom right',
};
const MODE_LABEL = { full: 'The hint itself', cue: 'Only a cue (the hint is on the presenter)' };
const SHAPE_LABEL = { rounded: 'Rounded', pill: 'Pill', square: 'Square' };

const screenName = (s) => `${s.primary ? 'Primary' : s.id.replace(/^\\\\\.\\/, '')} · ${s.width}×${s.height}${s.primary ? '' : ` at ${s.x}, ${s.y}`}`;
const options = (pairs, value) => pairs.map(([v, label]) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(label)}</option>`).join('');

function screenOptions(d) {
  const pairs = [['', 'Primary screen'], ...d.screens.filter((s) => !s.primary).map((s) => [s.id, screenName(s)])];
  if (d.overlay.screen && !pairs.some(([v]) => v === d.overlay.screen)) pairs.push([d.overlay.screen, `${d.overlay.screen} (not connected)`]);
  return options(pairs, d.overlay.screen);
}

export function overlayCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card overlay-card';
  const head = `<div class="card-head"><h3 class="card-title">${icon('monitor')} Copilot overlay</h3></div>`;
  card.innerHTML = head + '<p class="small muted">Loading…</p>';

  async function save(patch, message = 'Overlay saved — a sample shows where it sits') {
    try {
      draw(await api('/api/copilot/settings', { method: 'PUT', body: { overlay: patch } }));
      toast(message);
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async function editLook(o, d) {
    const v = await formDialog({
      title: 'How the overlay looks',
      fields: [
        { name: 'shape', label: 'Shape', type: 'select', value: o.shape, options: d.shapes.map((k) => ({ value: k, label: SHAPE_LABEL[k] || k })) },
        { name: 'width', label: 'Width (px)', type: 'number', value: o.width, min: 240, max: 2400, required: true },
        { name: 'font', label: 'Font', type: 'select', value: o.font, options: d.fonts.map((f) => ({ value: f, label: f })) },
        { name: 'font_size', label: 'Text size (px)', type: 'number', value: o.font_size, min: 14, max: 96, required: true },
        { name: 'text_color', label: 'Text colour', type: 'color', value: o.text_color },
        { name: 'background', label: 'Background', type: 'color', value: o.background },
        { name: 'opacity', label: 'Background opacity (%)', type: 'number', value: o.opacity, min: 20, max: 100, required: true },
        { name: 'margin', label: 'Distance from the edge (px)', type: 'number', value: o.margin, min: 0, max: 400, required: true },
        { name: 'show_seconds', label: 'Shown for (s)', type: 'number', value: o.show_seconds, min: 2, max: 120, required: true },
      ],
    });
    if (!v) return;
    const num = (k) => Number(v[k]);
    await save({ shape: v.shape, width: num('width'), font: v.font, font_size: num('font_size'), text_color: v.text_color,
      background: v.background, opacity: num('opacity'), margin: num('margin'), show_seconds: num('show_seconds') });
  }

  function draw(d) {
    const o = d.overlay;
    card.innerHTML = head;
    card.appendChild(switchRow(o.enabled, 'Show the hints on an overlay window', (next) => save({ enabled: next }, next ? 'Overlay on — a sample shows where it sits' : 'Overlay off')));
    const body = document.createElement('div');
    body.innerHTML =
      '<div class="font-rows">' +
      `<label class="font-row"><span class="small">Screen</span><select class="select-native" aria-label="Overlay screen" data-ov="screen" data-track="settings.overlay-screen">${screenOptions(d)}</select></label>` +
      `<label class="font-row"><span class="small">Position</span><select class="select-native" aria-label="Overlay position" data-ov="position" data-track="settings.overlay-position">${options(d.positions.map((p) => [p, POSITION_LABEL[p] || p]), o.position)}</select></label>` +
      `<label class="font-row"><span class="small">Shows</span><select class="select-native" aria-label="What the overlay shows" data-ov="mode" data-track="settings.overlay-mode">${options(d.modes.map((m) => [m, MODE_LABEL[m] || m]), o.mode)}</select></label>` +
      '</div>' +
      `<div class="list"><button type="button" class="list-row settings-row" data-ov-look data-track="settings.overlay-look">` +
      `<span class="settings-ico">${icon('palette')}</span><span class="grow"><span class="row-title">Look</span>` +
      `<span class="row-meta">${esc(SHAPE_LABEL[o.shape] || o.shape)} · ${o.width} px · ${esc(o.font)} ${o.font_size} px · ${o.opacity} % · ${o.show_seconds} s</span></span>${icon('chevron-right')}</button></div>` +
      `<div class="row-actions"><button type="button" class="button-tint" data-ov-sample data-track="settings.overlay-sample">${icon('eye')} Show a sample</button></div>` +
      '<p class="small muted settings-note">The overlay stays on top of every app on the screen you pick, lets every click through and shows nothing until Copilot has a hint, then fades. “Only a cue” flashes a small icon instead and puts the words on the presenter, so it never covers the call.</p>';
    card.appendChild(body);
    body.querySelectorAll('[data-ov]').forEach((sel) => sel.addEventListener('change', () => save({ [sel.dataset.ov]: sel.value })));
    body.querySelector('[data-ov-look]').addEventListener('click', () => editLook(o, d));
    body.querySelector('[data-ov-sample]').addEventListener('click', async () => {
      try {
        await api('/api/copilot/sample', { method: 'POST' });
        toast(o.enabled ? 'A sample is on the overlay' : 'The overlay is off — switch it on first');
      } catch (e) {
        toast(e.message, 'error');
      }
    });
  }

  api('/api/copilot/settings').then(draw).catch((e) => {
    card.innerHTML = head + `<p class="small muted">${esc(e.message)}</p>`;
  });
  return card;
}
