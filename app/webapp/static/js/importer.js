// The PowerPoint import dialog: a path (typed, or picked with the native
// Windows dialog on this PC), then the job's progress until done or failed.

import { icon } from '/static/_vendored/icons/icons.js';
import { api, esc, toast } from '/static/js/ui.js';

/** Resolves true when an import finished (the caller refreshes), else false. */
export function importDialog(sid, { lastPath = '', reimport = false } = {}) {
  return new Promise((resolve) => {
    const dlg = document.createElement('dialog');
    dlg.className = 'detail-dialog';
    dlg.innerHTML =
      `<div class="detail-card">` +
      `<div class="detail-header"><h2>${reimport ? 'Re-import PowerPoint' : 'Import PowerPoint'}</h2>` +
      `<button type="button" class="detail-close" aria-label="Close" data-close>${icon('x')}</button></div>` +
      `<label class="row"><span>Deck</span><span class="pick-row">` +
      `<input class="input-native" name="pptx" type="text" placeholder="C:\\…\\deck.pptx" value="${esc(lastPath)}">` +
      `<button type="button" class="button-surface" data-browse>${icon('folder-open')} Browse</button></span></label>` +
      `<p class="dialog-hint">${reimport
        ? 'Slides are matched by PowerPoint\'s own slide id: your plan order, activities and timers stay; removed slides drop out, new slides are inserted after the slide before them.'
        : 'Every slide becomes an image with its title and notes, and gets an OBS profile from where the grey camera box sits. Slides titled "activity – question" become activities.'}</p>` +
      `<div class="import-progress" hidden><div class="bar"><span></span></div><p class="small muted" data-msg></p></div>` +
      `<div class="detail-actions"><button type="button" class="detail-save-btn" data-go>${reimport ? 'Re-import' : 'Import'}</button></div>` +
      `</div>`;
    document.body.appendChild(dlg);
    const input = dlg.querySelector('[name=pptx]');
    const go = dlg.querySelector('[data-go]');
    const prog = dlg.querySelector('.import-progress');
    const bar = dlg.querySelector('.bar span');
    const msg = dlg.querySelector('[data-msg]');
    let finished = false;
    let running = false;
    const sync = () => { go.disabled = running || !input.value.trim(); };
    input.addEventListener('input', sync);
    sync();

    dlg.querySelector('[data-browse]').addEventListener('click', async () => {
      try {
        const r = await api('/api/pick', { method: 'POST', body: { kind: 'pptx' } });
        if (r.path) { input.value = r.path; sync(); }
      } catch (e) { toast(e.message, 'error'); }
    });

    go.addEventListener('click', async () => {
      running = true;
      sync();
      prog.hidden = false;
      msg.textContent = 'Starting…';
      let job;
      try {
        job = await api(`/api/sessions/${sid}/import`, { method: 'POST', body: { pptx: input.value.trim() } });
      } catch (e) {
        msg.textContent = e.message;
        prog.classList.add('failed');
        running = false;
        sync();
        return;
      }
      while (job.state === 'queued' || job.state === 'running') {
        bar.style.width = job.total ? `${Math.round((job.done / job.total) * 100)}%` : '4%';
        msg.textContent = job.message;
        await new Promise((r) => setTimeout(r, 400));
        try { job = await api(`/api/imports/${job.id}`); } catch (e) { msg.textContent = e.message; break; }
      }
      running = false;
      if (job.state === 'done') {
        bar.style.width = '100%';
        const r = job.result;
        finished = true;
        toast(r.first_import
          ? `Imported ${r.slides} slides into ${r.sections} sections`
          : `Re-imported ${r.slides} slides · ${r.added} new · ${r.removed} removed`);
        dlg.close();
      } else {
        prog.classList.add('failed');
        msg.textContent = job.message || 'The import failed';
        sync();
      }
    });

    dlg.querySelector('[data-close]').addEventListener('click', () => { if (!running) dlg.close(); });
    dlg.addEventListener('cancel', (e) => { if (running) e.preventDefault(); });
    dlg.addEventListener('close', () => { dlg.remove(); resolve(finished); });
    dlg.showModal();
    input.focus();
  });
}
