// Plan tab: re-import the deck (every slide, or the selected ones) and import a
// Kahoot spreadsheet. plan.js keeps the edit state and passes it in at click time,
// because the toolbar and the review note do not exist until mount().

import { icon } from '/static/_vendored/icons/icons.js';
import { api, toast } from '/static/js/ui.js';
import { formDialog, confirmDialog } from '/static/js/dialogs.js';
import { importDialog } from '/static/js/importer.js';
import { openReview } from '/static/js/reimport.js';

/** Deck indexes of the slide items in the current selection. */
function selectedSlideIndexes(c) {
  const indexes = [];
  for (const id of c.st.picked) {
    const found = c.findItem(id);
    if (!found || found.it.kind !== 'slide') continue;
    const slide = c.st.slides.get(found.it.slide_id);
    if (slide) indexes.push(slide.index);
  }
  return [...new Set(indexes)].sort((a, b) => a - b);
}

/** Show "Re-import selected" only when the selection contains a slide. */
export function syncSelectedImport(c) {
  const btn = c.toolbar.querySelector('[data-reimport-selected]');
  if (!btn) return;
  const n = selectedSlideIndexes(c).length;
  btn.hidden = n === 0;
  btn.title = n === 1 ? 'Export this slide and review it' : `Export these ${n} slides and review them`;
}

export function showReview(c) {
  c.split.hidden = true;
  c.reviewHost.hidden = false;
  window.scrollTo(0, 0);
  openReview(c.reviewHost, c.st.sid, {
    onClose: (changed) => {
      c.reviewHost.hidden = true;
      c.reviewHost.innerHTML = '';
      c.split.hidden = false;
      if (changed) c.load(); else renderReviewNote(c);
    },
  });
}

/** A waiting review, else a file-stat notice when the deck is newer or gone. */
export function renderReviewNote(c) {
  c.reviewNote.innerHTML = '';
  if (c.st.pending) {
    const b = document.createElement('div');
    b.className = 'banner warn review-banner';
    b.innerHTML = `${icon('refresh-cw')}<span class="grow">A re-import is waiting for your review — nothing has changed yet.</span>` +
      '<button type="button" class="banner-action" data-review data-track="plan.review">Review</button>';
    b.querySelector('[data-review]').addEventListener('click', () => showReview(c));
    c.reviewNote.appendChild(b);
    return;
  }
  const status = c.st.deck && c.st.deck.deck_status;
  if (status !== 'newer' && status !== 'missing') return;
  const missing = status === 'missing';
  const b = document.createElement('div');
  b.className = 'banner warn deck-banner';
  b.setAttribute('role', 'status');
  b.innerHTML = `${icon(missing ? 'triangle-alert' : 'refresh-cw')}<span class="grow">${missing
    ? 'The PowerPoint file is missing.'
    : 'This PowerPoint is newer than the import.'}</span>`;
  c.reviewNote.appendChild(b);
}

export async function reimportSelected(c) {
  const slides = selectedSlideIndexes(c);
  if (!slides.length) {
    toast('Select the slides to re-import');
    return;
  }
  return reimport(c, slides);
}

/** `slides` omitted exports the whole deck. A click event is not a slide list. */
export async function reimport(c, slides) {
  if (c.st.dirty) {
    const ok = await confirmDialog({ title: 'Unsaved changes', message: 'Save or discard your plan edits before importing.', actionLabel: 'Discard edits and continue', danger: true });
    if (!ok) return;
  }
  const had = c.st.deck && c.st.deck.slides.length > 0;
  const only = Array.isArray(slides) ? slides : null;
  const done = await importDialog(c.st.sid, {
    lastPath: (c.st.session.source && c.st.session.source.pptx) || (c.st.deck && c.st.deck.source) || '',
    reimport: had,
    slides: only,
  });
  if (!done) return;
  c.st.dirty = false;
  if (done.review) c.ctx.reviewFor = c.st.sid;
  await c.load();
}

/**
 * A quiz from Kahoot's spreadsheet template: pick the .xlsx on this PC, name
 * the quiz (default: the file's name), and the server adds a section with the
 * lobby, one question per row and the podium, saved — then the plan reloads.
 */
export async function importKahoot(c) {
  if (c.st.dirty) {
    const ok = await confirmDialog({ title: 'Unsaved changes', message: 'Save or discard your plan edits before importing.', actionLabel: 'Discard edits and continue', danger: true });
    if (!ok) return;
  }
  try {
    const picked = await api('/api/pick', { method: 'POST', body: { kind: 'xlsx' } });
    if (!picked.path) return;
    const stem = picked.path.split(/[\\/]/).pop().replace(/\.xlsx$/i, '');
    const v = await formDialog({
      title: 'Import Kahoot quiz',
      fields: [{ name: 'title', label: 'Quiz name', value: stem, required: true, hint: 'The new section and the quiz lobby take this name.' }],
      saveLabel: 'Import',
    });
    if (!v) return;
    const res = await api(`/api/sessions/${c.st.sid}/quiz-import`, { method: 'POST', body: { path: picked.path, title: v.title.trim() } });
    c.st.dirty = false;
    c.st.selected = c.st.anchor = res.lobby_id;
    c.st.picked = new Set([res.lobby_id]);
    await c.load();
    toast(`${res.questions} questions imported into "${res.section}"`);
  } catch (e) {
    toast(e.message, 'error');
  }
}
