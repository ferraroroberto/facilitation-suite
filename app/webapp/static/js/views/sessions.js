// Sessions tab: the ledger (list) and the selected session (detail) side by side
// on a wide screen — folder, plan summary, readiness checklist, go-live buttons.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, pageHead, setStatus, toast, fmtMinutes } from '/static/js/ui.js';
import { formDialog, confirmDialog, rowMenu } from '/static/js/dialogs.js';
import { importDialog } from '/static/js/importer.js';

let root;
let ctx;
let head;
let listEl;
let detailEl;
let sessions = [];
let sessionRoot = '';
let justMounted = false;

const STATE_ICON = { ok: 'circle-check', warn: 'triangle-alert', todo: 'circle-plus', unknown: 'triangle-alert' };

function fmtDate(iso, withTime = false) {
  if (!iso) return 'No date yet';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const day = d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: withTime ? 'numeric' : undefined });
  if (!withTime) return day;
  return day + ' · ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function timeRange(iso, minutes) {
  if (!iso) return '';
  const a = new Date(iso);
  const b = new Date(a.getTime() + (minutes || 0) * 60000);
  const t = (d) => d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  return `${t(a)}–${t(b)}`;
}

export async function mount(el, context) {
  root = el;
  ctx = context;
  root.innerHTML = '';
  const split = document.createElement('div');
  split.className = 'split';
  const list = document.createElement('div');
  list.className = 'split-list';
  detailEl = document.createElement('div');
  detailEl.className = 'split-detail';
  split.append(list, detailEl);
  root.appendChild(split);

  head = pageHead({ glyph: 'calendar-days', title: 'Sessions', status: 'Loading…' });
  list.appendChild(head);
  listEl = document.createElement('div');
  listEl.className = 'card list-card';
  list.appendChild(listEl);

  const actions = document.createElement('div');
  actions.className = 'stack-actions';
  actions.innerHTML =
    `<button type="button" class="button-tint big-action" data-new>${icon('plus')} New session</button>` +
    `<button type="button" class="button-ghost wide-ghost" data-add>Add an existing session folder</button>` +
    `<p class="muted small">This list is only a ledger of names and folders. Each session lives in its own folder with its own session.yaml. Duplicate a past session from its menu.</p>`;
  list.appendChild(actions);
  actions.querySelector('[data-new]').addEventListener('click', newSession);
  actions.querySelector('[data-add]').addEventListener('click', addExisting);

  await refresh();
  justMounted = true;
}

export function show() {
  if (justMounted) { justMounted = false; return; }
  refresh();
}

async function refresh() {
  try {
    const data = await api('/api/sessions');
    sessions = data.sessions;
    sessionRoot = data.session_root;
  } catch (e) {
    listEl.innerHTML = '';
    listEl.appendChild(emptyStateEl('triangle-alert', 'The session ledger could not be read.', { actionLabel: 'Retry', onAction: refresh }));
    setStatus(head, 'ledger unavailable');
    return;
  }
  setStatus(head, `${sessions.length} in your ledger`);
  if (ctx.sessionId && !sessions.some((s) => s.id === ctx.sessionId)) ctx.setSession(null);
  if (!ctx.sessionId && sessions.length) ctx.setSession(pickDefault().id);
  renderList();
  renderDetail();
}

function pickDefault() {
  const now = Date.now();
  const upcoming = sessions.filter((s) => s.date && new Date(s.date).getTime() >= now - 86400000)
    .sort((a, b) => new Date(a.date) - new Date(b.date));
  return upcoming[0] || sessions[0];
}

function renderList() {
  listEl.innerHTML = '';
  if (!sessions.length) {
    listEl.appendChild(emptyStateEl('calendar-days', 'No sessions yet.', { actionLabel: 'New session', onAction: newSession }));
    return;
  }
  const now = Date.now() - 86400000;
  const upcoming = sessions.filter((s) => !s.date || new Date(s.date).getTime() >= now)
    .sort((a, b) => (a.date ? new Date(a.date) : Infinity) - (b.date ? new Date(b.date) : Infinity));
  const past = sessions.filter((s) => s.date && new Date(s.date).getTime() < now)
    .sort((a, b) => new Date(b.date) - new Date(a.date));
  const group = (label, rows) => {
    if (!rows.length) return;
    const h = document.createElement('p');
    h.className = 'overline list-group';
    h.textContent = label;
    listEl.appendChild(h);
    rows.forEach((s) => listEl.appendChild(sessionRow(s)));
  };
  group('Upcoming', upcoming);
  group('Past', past);
}

function sessionRow(s) {
  const row = document.createElement('div');
  row.className = 'session-row' + (s.id === ctx.sessionId ? ' selected' : '');
  row.tabIndex = 0;
  row.setAttribute('role', 'button');
  const bad = s.status !== 'ok';
  row.innerHTML =
    `<div class="grow"><div class="row-title">${esc(s.title)}</div>` +
    `<div class="row-meta">${bad ? `<span class="chip bad">${esc(s.status === 'missing' ? 'folder missing' : 'unreadable')}</span> ` : ''}` +
    `${esc(fmtDate(s.date))} · ${esc(s.crumbs.slice(-3).join(' › '))}</div></div>` +
    `<button type="button" class="kebab hit-target" aria-label="More actions">${icon('ellipsis-vertical')}</button>`;
  const select = () => { ctx.setSession(s.id); renderList(); renderDetail(); };
  row.addEventListener('click', (e) => { if (!e.target.closest('.kebab')) select(); });
  row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
  row.querySelector('.kebab').addEventListener('click', (e) => {
    e.stopPropagation();
    rowMenu(e.currentTarget, [
      { label: 'Duplicate', icon: 'copy', onClick: () => duplicate(s) },
      { label: 'Open folder', icon: 'folder-open', onClick: () => openTarget(s.id, 'folder') },
      { label: 'Remove from ledger', icon: 'trash-2', danger: true, onClick: () => removeFromLedger(s) },
    ]);
  });
  return row;
}

async function renderDetail() {
  const sid = ctx.sessionId;
  detailEl.innerHTML = '';
  if (!sid) {
    const c = document.createElement('div');
    c.className = 'card';
    c.appendChild(emptyStateEl('presentation', 'Create a session to start planning.'));
    detailEl.appendChild(c);
    return;
  }
  const loading = document.createElement('div');
  loading.className = 'card';
  loading.appendChild(emptyStateEl('refresh-cw', 'Reading the session folder…'));
  detailEl.appendChild(loading);
  let data;
  try {
    data = await api(`/api/sessions/${sid}`);
  } catch (e) {
    detailEl.innerHTML = '';
    const c = document.createElement('div');
    c.className = 'card';
    c.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Retry', onAction: renderDetail }));
    detailEl.appendChild(c);
    return;
  }
  if (sid !== ctx.sessionId) return;
  detailEl.innerHTML = '';
  const s = data.session;
  const f = data.folder;

  const title = document.createElement('div');
  title.className = 'detail-title';
  const when = s.date ? `${fmtDate(s.date)} · ${timeRange(s.date, s.duration_minutes)}` : 'No date yet';
  title.innerHTML = `<h1>${esc(s.title)}</h1><p class="muted">${esc(when)} · ${fmtMinutes(s.duration_minutes)} planned</p>` +
    `<button type="button" class="button-surface" data-edit-meta>${icon('pencil')} Edit</button>`;
  title.querySelector('[data-edit-meta]').addEventListener('click', () => editMeta(sid, s));
  detailEl.appendChild(title);

  const top = document.createElement('div');
  top.className = 'detail-grid';
  detailEl.appendChild(top);

  // -- folder card
  const off = f.offline;
  const offLine = off.state === 'ok'
    ? `<p class="status-line ok">${icon('check')} ${off.local} of ${off.total} files on this PC${off.pinned ? ' · always kept offline' : ''}</p>`
    : off.state === 'cloud_only'
      ? `<p class="status-line warn">${icon('cloud-off')} ${esc(off.detail)} <button type="button" class="button-ghost" data-pin>Keep on this PC</button></p>`
      : `<p class="status-line unknown">${icon('triangle-alert')} Offline status unknown · ${esc(off.detail)}</p>`;
  const folder = document.createElement('div');
  folder.className = 'card';
  folder.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('folder')} Session folder</h3></div>` +
    `<p class="crumbs">${f.crumbs.slice(0, -1).map(esc).join(' › ')} › <b>${esc(f.crumbs[f.crumbs.length - 1])}</b></p>` +
    `<div class="file-box mono">session.yaml${f.roster ? ' · roster.xlsx' : ''}${f.groups ? ' · groups.yaml' : ''}<br>` +
    `slides/ · ${f.slides ? f.slides + ' images, titles, notes' : 'not imported yet'}<br>live/ · chat log, captured visuals<br>exports/ · PDF, Excel</div>` +
    offLine +
    `<div class="row-actions"><button type="button" class="button-surface" data-open="folder">${icon('folder-open')} Open folder</button>` +
    `<button type="button" class="button-surface" data-open="yaml">${icon('file-text')} Open session.yaml</button>` +
    `<button type="button" class="button-surface" data-import>${icon('upload')} ${f.slides ? 'Re-import PowerPoint' : 'Import PowerPoint'}</button></div>`;
  folder.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => openTarget(sid, b.dataset.open)));
  folder.querySelector('[data-import]').addEventListener('click', () => runImport(sid, s, f.slides > 0));
  const pinBtn = folder.querySelector('[data-pin]');
  if (pinBtn) pinBtn.addEventListener('click', () => pin(sid));
  top.appendChild(folder);

  // -- plan card
  const plan = document.createElement('div');
  plan.className = 'card';
  const total = s.sections.reduce((a, x) => a + (x.minutes || 0), 0);
  plan.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('presentation')} Plan · ${fmtMinutes(total)}</h3>` +
    `<button type="button" class="button-surface" data-edit-plan>Edit plan</button></div>` +
    (s.sections.length
      ? `<div class="list">${s.sections.map((x) => `<div class="list-row plan-row"><span class="grow">${esc(x.name)}</span><span class="muted small">${x.minutes} min</span></div>`).join('')}</div>`
      : `<p class="muted small">No sections yet — import the PowerPoint, then plan.</p>`);
  plan.querySelector('[data-edit-plan]').addEventListener('click', () => ctx.goTo('plan'));
  top.appendChild(plan);

  // -- readiness
  const ready = document.createElement('div');
  ready.className = 'card ready-card';
  const okCount = data.readiness.filter((c) => c.state === 'ok').length;
  ready.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('circle-check')} Ready for the live session</h3>` +
    `<span class="card-head-meta">${okCount} of ${data.readiness.length}</span></div>` +
    `<div class="list">${data.readiness.map((c) => readyRow(c)).join('')}</div>`;
  ready.querySelectorAll('[data-action]').forEach((b) => b.addEventListener('click', () => readinessAction(sid, s, b.dataset.action)));
  detailEl.appendChild(ready);

  const go = document.createElement('div');
  go.className = 'go-row';
  go.innerHTML =
    `<a class="button-primary go-primary" href="/presenter?session=${encodeURIComponent(sid)}" target="fs-presenter">${icon('monitor')} Open presenter</a>` +
    `<a class="button-tint go-secondary" href="/stage" target="fs-stage">${icon('presentation')} Open stage window</a>`;
  detailEl.appendChild(go);
}

function readyRow(c) {
  const labels = { import: 'Import', pin: 'Keep on PC', test_reader: 'Test', confirm_zoom_update: 'Mark done' };
  const btn = c.action && labels[c.action]
    ? `<button type="button" class="button-surface" data-action="${c.action}">${labels[c.action]}</button>` : '';
  return `<div class="list-row ready-row state-${c.state}">` +
    `<span class="ready-icon">${icon(STATE_ICON[c.state] || 'circle-plus')}</span>` +
    `<span class="grow"><b>${esc(c.label)}</b> <span class="muted small">${esc(c.detail)}</span></span>${btn}</div>`;
}

async function readinessAction(sid, s, action) {
  if (action === 'pin') return pin(sid);
  if (action === 'import') return runImport(sid, s, false);
  if (action === 'test_reader') {
    try {
      await api('/api/chat/reader/start', { method: 'POST' });
      toast('Chat reader started — pop out the Zoom meeting chat; this check turns green once it has read it');
      setTimeout(renderDetail, 4000);
    } catch (e) { toast(e.message, 'error'); }
    return;
  }
  if (action === 'confirm_zoom_update') {
    const next = Object.assign({}, s, { checklist: Object.assign({}, s.checklist, { zoom_autoupdate_off: true }) });
    await save(sid, next);
  }
}

async function runImport(sid, s, reimport) {
  const done = await importDialog(sid, { lastPath: (s.source && s.source.pptx) || '', reimport });
  if (done) await refresh();
}

async function save(sid, session) {
  try {
    await api(`/api/sessions/${sid}`, { method: 'PUT', body: { session } });
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
}

async function editMeta(sid, s) {
  const date = s.date ? new Date(s.date) : null;
  const pad = (n) => String(n).padStart(2, '0');
  const local = date ? `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}` : '';
  const v = await formDialog({
    title: 'Session details',
    fields: [
      { name: 'title', label: 'Title', value: s.title, required: true },
      { name: 'date', label: 'Date and time', type: 'datetime-local', value: local },
      { name: 'duration', label: 'Duration (min)', type: 'number', value: s.duration_minutes },
      { name: 'chat_hint', label: 'Stage hint under activities', value: s.chat_hint || '',
        hint: 'Shown on the stage in the language of the session, e.g. "Escribe tu respuesta en el chat de Zoom".' },
    ],
  });
  if (!v) return;
  const next = Object.assign({}, s, {
    title: v.title.trim(),
    chat_hint: v.chat_hint.trim() || 'Write your answer in the Zoom chat',
    date: v.date ? new Date(v.date).toISOString() : null,
    duration_minutes: Math.max(1, parseInt(v.duration, 10) || s.duration_minutes),
  });
  if (!next.date) delete next.date;
  await save(sid, next);
}

async function newSession() {
  const v = await formDialog({
    title: 'New session',
    saveLabel: 'Create session folder',
    fields: [
      { name: 'title', label: 'Title', placeholder: 'Ice break · cohort', required: true },
      { name: 'workshop', label: 'Workshop folder', placeholder: 'icebreak', required: true },
      { name: 'folder', label: 'Session folder', placeholder: 'cohort-a', required: true,
        hint: `Created under ${sessionRoot}\\<workshop>\\<session>` },
      { name: 'date', label: 'Date and time', type: 'datetime-local' },
      { name: 'duration', label: 'Duration (min)', type: 'number', value: 120 },
    ],
  });
  if (!v) return;
  try {
    const created = await api('/api/sessions', {
      method: 'POST',
      body: {
        title: v.title.trim(), workshop: v.workshop.trim(), folder: v.folder.trim(),
        date: v.date ? new Date(v.date).toISOString() : null,
        duration_minutes: Math.max(1, parseInt(v.duration, 10) || 120),
      },
    });
    ctx.setSession(created.id);
    toast('Session folder created');
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
}

async function addExisting() {
  const v = await formDialog({
    title: 'Add an existing session folder',
    saveLabel: 'Add to ledger',
    fields: [{ name: 'path', label: 'Folder', placeholder: 'C:\\…\\workshop\\session', required: true,
      hint: 'The folder that holds session.yaml.' }],
  });
  if (!v) return;
  try {
    const added = await api('/api/sessions/add', { method: 'POST', body: { path: v.path } });
    ctx.setSession(added.id);
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
}

async function duplicate(s) {
  const v = await formDialog({
    title: 'Duplicate session',
    saveLabel: 'Duplicate',
    fields: [
      { name: 'title', label: 'Title', value: s.title + ' (copy)', required: true },
      { name: 'folder', label: 'Session folder', value: '', placeholder: 'cohort-b', required: true,
        hint: 'Created beside the original. Copies the plan, slides, roster and theme — not live data or exports.' },
    ],
  });
  if (!v) return;
  try {
    const dup = await api(`/api/sessions/${s.id}/duplicate`, { method: 'POST', body: { title: v.title.trim(), folder: v.folder.trim() } });
    ctx.setSession(dup.id);
    toast('Session duplicated');
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
}

async function removeFromLedger(s) {
  const ok = await confirmDialog({
    title: 'Remove from ledger?',
    message: `"${s.title}" disappears from this list. Its folder and files stay where they are; add it back any time.`,
    actionLabel: 'Remove from ledger', danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/sessions/${s.id}`, { method: 'DELETE' });
    if (ctx.sessionId === s.id) ctx.setSession(null);
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
}

async function openTarget(sid, what) {
  try { await api(`/api/sessions/${sid}/open`, { method: 'POST', body: { what } }); }
  catch (e) { toast(e.message, 'error'); }
}

async function pin(sid) {
  try {
    const r = await api(`/api/sessions/${sid}/pin`, { method: 'POST' });
    toast(r.message);
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
}
