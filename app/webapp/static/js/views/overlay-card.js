// Settings → Live tools → Copilot overlay: where the live copilot's overlay window sits (which screen,
// which corner), what it shows (the hint itself, or only a cue that sends the eye to the presenter)
// and how it looks. Every save moves an open overlay at once and puts a sample there, so finding the
// right spot on the prompter screen is a matter of picking and looking.

import { api, esc, toast } from '/static/js/ui.js';
import { formDialog } from '/static/js/dialogs.js';
import { actionRow, factRow, selectRow, settingsGroup, toggleRow, valueRow } from '/static/js/views/settings-rows.js';

export const POSITION_LABEL = {
  'top-left': 'Top left', 'top-center': 'Top centre', 'top-right': 'Top right', center: 'Centre',
  'bottom-left': 'Bottom left', 'bottom-center': 'Bottom centre', 'bottom-right': 'Bottom right',
};
export const MODE_LABEL = { full: 'The hint itself', cue: 'Only a cue (the hint is on the presenter)' };
const SHAPE_LABEL = { rounded: 'Rounded', pill: 'Pill', square: 'Square' };

export const screenName = (s) => `${s.primary ? 'Primary' : s.id.replace(/^\\\\\.\\/, '')} · ${s.width}×${s.height}${s.primary ? '' : ` at ${s.x}, ${s.y}`}`;
const options = (pairs, value) => pairs.map(([v, label]) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(label)}</option>`).join('');

function screenOptions(d) {
  const pairs = [['', 'Primary screen'], ...d.screens.filter((s) => !s.primary).map((s) => [s.id, screenName(s)])];
  if (d.overlay.screen && !pairs.some(([v]) => v === d.overlay.screen)) pairs.push([d.overlay.screen, `${d.overlay.screen} (not connected)`]);
  return options(pairs, d.overlay.screen);
}

/** Put a sample on the overlay and toast what happened; `offHint` says where to switch the overlay on. */
export async function showOverlaySample(offHint) {
  try {
    const d = await api('/api/copilot/sample', { method: 'POST' });
    if (!d.overlay.enabled) toast(`The overlay is off — switch it on ${offHint}`);
    else if (d.delivered) toast('A sample is on the overlay');
    else toast('The overlay is not running — see data/logs/facilitation-suite.log, then restart the app', 'error');
  } catch (e) {
    toast(e.message, 'error');
  }
}

export function overlayGroup() {
  const { frag, card, foot } = settingsGroup('overlay', 'Copilot overlay', [], '');
  card.classList.add('overlay-card');
  const message = (text) => { card.replaceChildren(factRow({ glyph: 'monitor', label: 'Overlay', value: text })); };
  message('Loading…');

  async function save(patch, message = 'Overlay saved — a sample shows where it sits') {
    try {
      const d = await api('/api/copilot/settings', { method: 'PUT', body: { overlay: patch } });
      draw(d);
      if (d.delivered === false) toast('Overlay saved, but it is not running — see data/logs/facilitation-suite.log, then restart the app', 'error');
      else toast(message);
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
    const pick = (key, { row, select }) => {
      select.dataset.ov = key;
      select.addEventListener('change', () => save({ [key]: select.value }));
      return row;
    };
    const look = `${SHAPE_LABEL[o.shape] || o.shape} · ${o.width} px · ${o.font} ${o.font_size} px · ${o.opacity} % · ${o.show_seconds} s`;
    card.replaceChildren(
      toggleRow({ glyph: 'monitor', label: 'Show hints on an overlay', on: o.enabled, track: 'settings.overlay-enabled',
        onToggle: (next) => save({ enabled: next }, next ? 'Overlay on — a sample shows where it sits' : 'Overlay off') }),
      pick('screen', selectRow({ glyph: 'layout-grid', label: 'Screen', aria: 'Overlay screen', track: 'settings.overlay-screen', optionsHtml: screenOptions(d) })),
      pick('position', selectRow({ glyph: 'map-pin', label: 'Position', aria: 'Overlay position', track: 'settings.overlay-position',
        optionsHtml: options(d.positions.map((p) => [p, POSITION_LABEL[p] || p]), o.position) })),
      pick('mode', selectRow({ glyph: 'eye', label: 'Shows', aria: 'What the overlay shows', track: 'settings.overlay-mode',
        optionsHtml: options(d.modes.map((m) => [m, MODE_LABEL[m] || m]), o.mode) })),
      valueRow({ glyph: 'palette', label: 'Look', value: look, track: 'settings.overlay-look', onOpen: () => editLook(o, d) }),
      actionRow({ glyph: 'play', label: 'Show a sample', track: 'settings.overlay-sample', onTap: () => showOverlaySample('first') }),
    );
    foot.textContent = 'Stays on top of every app on the screen you pick, lets every click through and shows nothing until Copilot has a hint. “Only a cue” puts the words on the presenter.';
    foot.hidden = false;
  }

  api('/api/copilot/settings').then(draw).catch((e) => message(e.message));
  return frag;
}
