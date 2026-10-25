// Plan tab: the editor's stage preview — the selected item drawn by the real stage
// renderer, with sample answers for a capture activity. plan.js binds it to its
// state through createPreview() and calls previewCard() and updatePreview().

import { api } from '/static/js/ui.js';
import { createStage } from '/static/js/stage-render.js';
import { words } from '/static/js/stage-words.js';
import { fontSeed } from '/static/js/views/plan-fields.js';

const ZONES = {
  camera_strip: [0.583, 0.23, 0.983, 0.77],
  camera_pip: [0.738, 0, 1, 0.262],
  screen_only: null,
};

/**
 * The preview, bound to the Plan tab: `st` (the session being edited), `getEditor` (the editor
 * column, which holds the preview frame), `slideOf` (an item's imported slide) and `titleOf`.
 */
export function createPreview({ st, getEditor, slideOf, titleOf }) {
  let pv = null; // { frame, stage } — rebuilt when the editor re-renders its preview frame
  const previewResults = new Map();
  let previewTimer = null;

  /** A plan item as the live run shows it (src/live/plan.py), for the stage renderer. */
  function runItem(it) {
    const s = slideOf(it);
    const profile = it.profile || (s && s.profile) || 'screen_only';
    const spec = st.types[it.type] || {};
    return {
      id: it.id, kind: it.kind, type: it.type || null, type_label: spec.label,
      capture: it.kind === 'activity' && spec.capture !== false,
      title: titleOf(it), question: it.question || (it.kind === 'activity' ? words(st.session.language).question : ''),
      font: it.font || fontSeed(), options: it.options || {},
      profile, zone: st.zones && profile in st.zones ? st.zones[profile] : ZONES[profile], slide_file: s ? s.file : null,
      timer: it.timer && it.timer.enabled !== false ? it.timer : null,
      ...(it.type === 'groups_reveal' ? { rooms: (st.rounds || {})[(it.options || {}).round || 'pairs'] || [] } : {}),
      ...(it.kind === 'breakout' ? { rooms: (st.rounds || {})[(it.options || {}).round || ''] || [] } : {}),
      ...(s && s.bg_file && s.boxes && it.live_text !== false ? { slide_bg: s.bg_file, text_boxes: s.boxes } : {}),
    };
  }

  function updatePreview(it) {
    const frame = getEditor().querySelector('[data-preview]');
    if (!frame) return;
    if (!pv || pv.frame !== frame) pv = { frame, stage: createStage(frame, { guides: true, slideGuides: true }) };
    const item = runItem(it);
    const plan = { rev: 0, active: true, session: { id: st.sid }, run: { chat_hint: st.session.chat_hint, language: st.session.language, theme: st.session.theme } };
    const opts = Object.assign({}, ...((st.types[item.type] || {}).options || []).map((o) => ({ [o.key]: o.default })), item.options);
    const key = JSON.stringify([item.type, item.options]);
    const draw = () => pv.stage.render(item, {
      plan, state: { timers: {}, blackout: false }, now: Date.now(),
      names: !!opts.show_names, result: previewResults.get(key) || null,
    });
    draw();
    if (!item.capture || previewResults.has(key)) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const res = await api(`/api/activities/${encodeURIComponent(item.type)}/preview`, { method: 'POST', body: { options: item.options } });
        previewResults.set(key, res.result);
        if (pv && pv.frame === frame && frame.isConnected) draw();
      } catch (e) { /* the preview stays without sample answers */ }
    }, 250);
  }

  function previewCard(it) {
    const card = document.createElement('div');
    card.className = 'card preview-card';
    card.innerHTML =
      `<div class="preview-frame stage-host" data-preview></div>` +
      `<div class="preview-text"><b>Stage preview</b><p class="muted small">${it.kind === 'slide'
        ? 'The slide as imported. The dashed box is where the camera goes for the chosen layout.'
        : it.type === 'groups_reveal'
          ? 'Drawn by the real stage with the rooms from the Groups tab — a new shuffle redraws it.'
          : it.kind === 'activity'
          ? 'Drawn by the real stage with sample answers, at the size Zoom will see it. The dashed box is the camera.'
          : it.kind === 'breakout'
            ? 'The breakout as the stage shows it: the round and its rooms from the Groups tab, and the clock.'
            : 'The break as the stage shows it.'}</p></div>`;
    setTimeout(() => updatePreview(it), 0);
    return card;
  }

  return { previewCard, updatePreview };
}
