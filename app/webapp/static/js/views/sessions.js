// Sessions tab: the ledger (list) and the selected session (detail) side by side
// on a wide screen — folder, plan summary, readiness checklist, go-live buttons.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, pageHead, setStatus, toast, fmtMinutes } from '/static/js/ui.js';
import { formDialog, confirmDialog, rowMenu } from '/static/js/dialogs.js';
import { importDialog } from '/static/js/importer.js';
import { applySessionTheme } from '/static/js/stage-render.js';
import { words, LANGUAGES } from '/static/js/stage-words.js';
import { ROLES, TEXT_FONTS, sessionRole } from '/static/js/lettering.js';

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
  const language = (LANGUAGES.find(([k]) => k === s.language) || LANGUAGES[0])[1];
  title.innerHTML = `<h1>${esc(s.title)}</h1><p class="muted">${esc(when)} · ${fmtMinutes(s.duration_minutes)} planned · ${esc(language)} on stage</p>` +
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

  detailEl.appendChild(fontCard(sid, s));

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

// -- stage lettering: the font (a file on this PC or an installed one), its
// weight and line thickness, and capitals — every item follows it unless it
// sets its own in the Plan tab.
const STAGE_FONTS = [
  ['', 'Patrick Hand (theme)'],
  ['system-ui', 'System sans'],
  ['Georgia', 'Georgia (serif)'],
  ['Segoe Print', 'Segoe Print (handwriting)'],
];

/** Two buttons (or more) as range tabs; `value` is the active one. */
function tabs(label, attr, options, value) {
  return `<div class="range-tabs ed-tabs" role="group" aria-label="${esc(label)}">${options.map(([v, l]) =>
    `<button type="button" class="range-tab${v === value ? ' active' : ''}" aria-pressed="${v === value}" ${attr}="${esc(String(v))}">${esc(l)}</button>`).join('')}</div>`;
}

/**
 * The session's stage lettering: the title font (its own file or an installed
 * font, weight, line thickness), the text font (every other text; by default
 * the chat hint's plain sans) and, for each kind of text, which of the two it
 * uses and whether it is in capitals. An item can set its own in the Plan tab.
 */
const FONT_DEFAULTS = { file: '', family: '', weight: 400, stroke_px: 0, caps: true, text_family: '', text_weight: 400, roles: {} };

function fontCard(sid, s) {
  const font = Object.assign({}, FONT_DEFAULTS, s.font || {});
  const file = (font.file || '').trim();
  const name = file ? file.split(/[\\/]/).pop() : '';
  const stroke = Number(font.stroke_px) || 0;
  const familyLabel = file ? name : (STAGE_FONTS.find(([v]) => v === font.family) || [null, font.family || 'Patrick Hand (theme)'])[1];
  const hint = s.chat_hint || words(s.language).chat_hint;
  applySessionTheme(sid, s.theme, JSON.stringify(font));
  const card = document.createElement('div');
  card.className = 'card font-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('type')} Stage lettering</h3>` +
    `<span class="card-head-meta">${esc(familyLabel)}</span></div>` +
    `<div class="font-sample-host"><div class="stage-canvas font-sample" style="--st-font-stroke:${stroke}px">` +
    `<h1 class="st-question">Hello, group!</h1>` +
    `<div class="font-sample-row"><span class="st-hint">${icon('message-square')}${esc(hint)}</span>` +
    `<div class="st-body"><span class="font-sample-words">meetings · focus</span></div></div></div></div>` +
    `<p class="muted small">Two fonts: the <b>title font</b> for titles and questions, the <b>text font</b> for the rest. Below, each kind of text can take either, in capitals or as typed; an item can set its own in the Plan tab.</p>` +
    `<h4 class="font-group">Title font</h4><div class="font-rows">` +
    `<label class="font-row"><span class="small">Font</span><span class="inline-controls"><select class="select-native" aria-label="Title font" data-title-family>` +
    STAGE_FONTS.map(([v, l]) => `<option value="${esc(v)}"${!file && v === font.family ? ' selected' : ''}>${esc(l)}</option>`).join('') +
    (file ? `<option value="__file" selected>File · ${esc(name)}</option>` : '') +
    `</select><button type="button" class="button-surface" data-font-pick>${icon('folder-open')} ${file ? 'Change file…' : 'Font file…'}</button></span></label>` +
    `<div class="font-row"><span class="small">Weight</span>${tabs('Title weight', 'data-weight', [[400, 'Regular'], [700, 'Bold']], font.weight)}</div>` +
    `<label class="font-row font-stroke"><span class="small">Line thickness</span>` +
    `<input type="range" min="0" max="6" step="0.25" value="${stroke}" aria-label="Line thickness in stage px">` +
    `<output class="mono small">${stroke} px</output></label></div>` +
    (file ? `<p class="mono small muted font-path" title="${esc(file)}">${esc(file)}</p>` : '') +
    `<h4 class="font-group">Text font</h4><div class="font-rows">` +
    `<label class="font-row"><span class="small">Font</span><select class="select-native" aria-label="Text font" data-text-family>` +
    TEXT_FONTS.map(([v, l]) => `<option value="${esc(v)}"${v === font.text_family ? ' selected' : ''}>${esc(l)}</option>`).join('') +
    (font.text_family && !TEXT_FONTS.some(([v]) => v === font.text_family) ? `<option value="${esc(font.text_family)}" selected>${esc(font.text_family)}</option>` : '') +
    `</select></label>` +
    `<div class="font-row"><span class="small">Weight</span>${tabs('Text weight', 'data-text-weight', [[400, 'Regular'], [700, 'Bold']], font.text_weight)}</div></div>` +
    `<h4 class="font-group">Each kind of text</h4><div class="font-rows role-rows">` +
    ROLES.map((r) => {
      const now = sessionRole(font, r.key);
      return `<div class="role-row" data-role="${r.key}"><span class="small">${esc(r.label)}</span>` +
        tabs(`${r.label}: font`, 'data-role-font', [['title', 'Title font'], ['text', 'Text font']], now.font === 'title' ? 'title' : 'text') +
        tabs(`${r.label}: capitals`, 'data-role-caps', [['true', 'ALL CAPS'], ['false', 'As typed']], String(now.caps)) + '</div>';
    }).join('') + `</div>`;
  const sample = card.querySelector('.font-sample');
  const range = card.querySelector('input[type=range]');
  const out = card.querySelector('output');
  const saveFont = async (change) => {
    // from what is saved now: the thickness slider saves without drawing the card again
    const next = Object.assign({}, FONT_DEFAULTS, s.font || {}, change);
    const session = Object.assign({}, s, { font: next });
    // all defaults: no font block in session.yaml
    if (!next.file && !next.family && next.weight === 400 && !next.stroke_px && next.caps &&
        !next.text_family && next.text_weight === 400 && !Object.keys(next.roles || {}).length) delete session.font;
    try {
      await api(`/api/sessions/${sid}`, { method: 'PUT', body: { session } });
      s.font = session.font;
      return true;
    } catch (e) { toast(e.message, 'error'); return false; }
  };
  // one kind of text: back to its default = no entry for it
  const setRole = (key, change) => {
    const role = ROLES.find((r) => r.key === key);
    const roles = Object.assign({}, (s.font || {}).roles);
    const mine = Object.assign({}, roles[key], change);
    if (!mine.font || mine.font === role.font) delete mine.font;
    if (mine.caps !== true) delete mine.caps;
    if (Object.keys(mine).length) roles[key] = mine; else delete roles[key];
    return saveFont({ roles });
  };
  const redraw = async (p) => { if (await p) renderDetail(); };
  card.querySelector('[data-title-family]').addEventListener('change', (e) => {
    if (e.target.value !== '__file') redraw(saveFont({ file: '', family: e.target.value }));
  });
  card.querySelector('[data-text-family]').addEventListener('change', (e) => redraw(saveFont({ text_family: e.target.value })));
  card.querySelectorAll('[data-weight]').forEach((b) => b.addEventListener('click', () => redraw(saveFont({ weight: Number(b.dataset.weight) }))));
  card.querySelectorAll('[data-text-weight]').forEach((b) => b.addEventListener('click', () => redraw(saveFont({ text_weight: Number(b.dataset.textWeight) }))));
  card.querySelectorAll('.role-row').forEach((row) => {
    const key = row.dataset.role;
    row.querySelectorAll('[data-role-font]').forEach((b) => b.addEventListener('click', () => redraw(setRole(key, { font: b.dataset.roleFont }))));
    row.querySelectorAll('[data-role-caps]').forEach((b) => b.addEventListener('click', () => {
      const caps = b.dataset.roleCaps === 'true';
      redraw(key === 'title' ? saveFont({ caps }) : setRole(key, { caps }));  // a title's capitals are font.caps
    }));
  });
  range.addEventListener('input', () => {
    out.textContent = `${range.value} px`;
    sample.style.setProperty('--st-font-stroke', `${range.value}px`);
  });
  range.addEventListener('change', async () => {
    if (await saveFont({ stroke_px: Number(range.value) })) toast(`Line thickness ${range.value} px saved`);
  });
  card.querySelector('[data-font-pick]').addEventListener('click', async () => {
    let picked;
    try { picked = await api('/api/pick', { method: 'POST', body: { kind: 'font' } }); } catch (e) { toast(e.message, 'error'); return; }
    if (!picked.path) return;
    if (await saveFont({ file: picked.path })) { toast('Stage font saved'); renderDetail(); }
  });
  return card;
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
  if (!done) return;
  if (done.review) {
    if (ctx.sessionId !== sid) ctx.setSession(sid);
    ctx.reviewFor = sid;  // the Plan tab opens the review
    ctx.goTo('plan');
    return;
  }
  await refresh();
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
      { name: 'language', label: 'Language on the stage', type: 'select', value: s.language || 'en', options: LANGUAGES.map(([value, label]) => ({ value, label })),
        hint: 'The words the stage says by itself: the chat hint, default titles, breakout rooms.' },
      { name: 'chat_hint', label: 'Stage hint under activities', value: s.chat_hint || '',
        placeholder: words(s.language).chat_hint,
        hint: `Empty = the language's own: "${words('en').chat_hint}" · "${words('es').chat_hint}".` },
    ],
  });
  if (!v) return;
  const next = Object.assign({}, s, {
    title: v.title.trim(),
    language: v.language,
    chat_hint: v.chat_hint.trim(),
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
