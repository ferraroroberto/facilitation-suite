// Sessions tab: the ledger (list); a session opened from it takes the whole
// pane (#150) — folder, plan summary, its own settings (details and stage look,
// #151), readiness, go-live buttons — until its X, Esc or Back returns to the
// list. /#sessions/<id> opens one.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, pageHead, setStatus, toast, fmtMinutes } from '/static/js/ui.js';
import { formDialog, confirmDialog, rowMenu } from '/static/js/dialogs.js';
import { importDialog } from '/static/js/importer.js';
import { applySessionTheme } from '/static/js/stage-render.js';
import { words, LANGUAGES } from '/static/js/stage-words.js';
import { fontEditorHtml, letteringLabel, themeOptions, wireFontEditor } from '/static/js/font-editor.js';

let root;
let ctx;
let head;
let listEl;
let listWrap;
let detailEl;
let sessions = [];
let sessionRoot = '';
let justMounted = false;
/** The session shown full screen; null = the list. It stays ctx.sessionId after closing. */
let openId = null;
/** Where the list was scrolled when a session opened, put back when it closes. */
let listScroll = null;

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
  root.dataset.mode = 'list';
  head = pageHead({ glyph: 'calendar-days', title: 'Sessions', status: 'Loading…' });
  listWrap = document.createElement('div');
  listWrap.className = 'sessions-list';
  detailEl = document.createElement('div');
  detailEl.className = 'sessions-detail';
  detailEl.hidden = true;
  root.append(head, listWrap, detailEl);

  listEl = document.createElement('div');
  listEl.className = 'card list-card';
  listWrap.appendChild(listEl);

  const actions = document.createElement('div');
  actions.className = 'stack-actions';
  actions.innerHTML =
    `<button type="button" class="button-tint big-action" data-new>${icon('plus')} New session</button>` +
    `<button type="button" class="button-ghost wide-ghost" data-add>Add an existing session folder</button>` +
    `<p class="muted small">This list is only a ledger of names and folders. Each session lives in its own folder with its own session.yaml. Duplicate a past session from its menu.</p>`;
  listWrap.appendChild(actions);
  actions.querySelector('[data-new]').addEventListener('click', newSession);
  actions.querySelector('[data-add]').addEventListener('click', addExisting);

  document.addEventListener('keydown', onEscape);
  window.addEventListener('popstate', onHistory);
  await refresh();
  justMounted = true;
}

export function show() {
  if (openId) setHash(openId);  // another tab dropped the deep link; it is this tab's again
  if (justMounted) { justMounted = false; return; }
  refresh();
}

const hashOf = (id) => `#sessions/${encodeURIComponent(id)}`;
function setHash(id) {
  if (location.hash !== hashOf(id)) history.replaceState(history.state, '', location.pathname + location.search + hashOf(id));
}

/** The app shell's scroll: the window, or .app in an installed PWA (design.md Navigation). */
function scrollPos() {
  const app = document.querySelector('.app');
  return { win: window.scrollY, app: app ? app.scrollTop : 0 };
}
function scrollBack(pos) {
  const app = document.querySelector('.app');
  window.scrollTo(0, pos.win);
  if (app) app.scrollTop = pos.app;
}

/**
 * Open a session full screen (#150): it becomes the selected session for every
 * tab and its detail replaces the list. `push` adds a history entry, so Back
 * closes it; a deep link (/#sessions/<id>) keeps the entry it arrived on.
 */
export function openSession(id, { push = true } = {}) {
  if (!sessions.some((s) => s.id === id)) {
    toast('That session is not in your ledger', 'error');
    closeSession();
    return;
  }
  if (openId === null) listScroll = scrollPos();
  if (push && openId === null && location.hash !== hashOf(id)) {
    history.pushState({ fsSession: id }, '', location.pathname + location.search + hashOf(id));
  } else {
    setHash(id);
  }
  openId = id;
  if (ctx.sessionId !== id) ctx.setSession(id);
  root.dataset.mode = 'open';
  listWrap.hidden = true;
  detailEl.hidden = false;
  scrollBack({ win: 0, app: 0 });
  renderList();
  renderDetail();
}

/**
 * Back to the list, where it was scrolled; the session stays the selected one.
 * `fromHistory`: the browser already left the entry (Back), so history is not touched.
 */
export function closeSession({ fromHistory = false } = {}) {
  const wasOpen = openId;
  openId = null;
  root.dataset.mode = 'list';
  detailEl.hidden = true;
  detailEl.innerHTML = '';
  listWrap.hidden = false;
  if (!fromHistory && location.hash.startsWith('#sessions/')) {
    if (history.state && history.state.fsSession) history.back();
    else history.replaceState(null, '', location.pathname + location.search);
  }
  if (!wasOpen) return;
  renderList();
  if (listScroll) scrollBack(listScroll);
  listScroll = null;
  const row = listEl.querySelector('.session-row.selected');
  if (row) row.focus({ preventScroll: true });
}

/** Esc closes an open session — not while a dialog or menu owns it, or a field is being edited. */
function onEscape(e) {
  if (e.key !== 'Escape' || !openId || e.defaultPrevented) return;
  if (root.closest('.pane').hidden) return;
  if (document.querySelector('dialog[open], .row-menu')) return;
  if (e.target.closest && e.target.closest('input, select, textarea, [contenteditable]')) return;
  e.preventDefault();
  closeSession();
}

/** Back (or Forward) to an entry without the deep link closes the open session. */
function onHistory() {
  if (openId && !location.hash.startsWith('#sessions/')) closeSession({ fromHistory: true });
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
  if (openId && !sessions.some((s) => s.id === openId)) closeSession();
  renderList();
  if (openId) renderDetail();
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
  const select = () => openSession(s.id);
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
  const sid = openId;
  if (!sid) return;
  detailEl.innerHTML = '';
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
  if (sid !== openId) return;
  detailEl.innerHTML = '';
  const s = data.session;
  const f = data.folder;

  const title = document.createElement('div');
  title.className = 'detail-title';
  title.innerHTML = `<h1 data-title></h1><p class="muted" data-summary></p>` +
    `<button type="button" class="detail-close" aria-label="Close" title="Close (Esc)" data-close-session>${icon('x')}</button>`;
  title.querySelector('[data-close-session]').addEventListener('click', () => closeSession());
  paintTitle(title, s);
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

  detailEl.appendChild(settingsCard(sid, s, data.look, () => paintTitle(title, s)));

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

/** The header: the title and the one-line summary (date · duration · language). */
function paintTitle(el, s) {
  const when = s.date ? `${fmtDate(s.date)} · ${timeRange(s.date, s.duration_minutes)}` : 'No date yet';
  el.querySelector('[data-title]').textContent = s.title;
  el.querySelector('[data-summary]').textContent = `${when} · ${fmtMinutes(s.duration_minutes)} planned · ${languageLabel(s.language)} on stage`;
}

const languageLabel = (code) => (LANGUAGES.find(([k]) => k === code) || LANGUAGES[0])[1];
const hintOf = (s) => s.chat_hint || words(s.language).chat_hint;

/** An ISO date as a datetime-local value, in this PC's time. */
function localValue(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * The session's own settings (#151), one card: its details, edited inline and
 * saved on change or blur, and its stage look — the theme and lettering (the
 * shared editor in font-editor.js) every item follows unless it sets its own in
 * the Plan tab. A new session starts from Settings → Stage defaults; the card
 * says whether it still uses them and can put them back (#110). Global settings
 * stay on the Settings page.
 */
function settingsCard(sid, s, look, onDetails) {
  applySessionTheme(sid, s.theme, JSON.stringify([s.theme, s.font || {}]));
  const card = document.createElement('div');
  card.className = 'card session-settings-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('sliders-horizontal')} Session settings</h3></div>` +
    `<div class="font-rows">` +
    `<label class="font-row"><span class="small">Title</span><input class="input" data-meta="title" value="${esc(s.title)}" required aria-label="Title"></label>` +
    `<label class="font-row"><span class="small">Date and time</span><input class="input" type="datetime-local" data-meta="date" value="${esc(localValue(s.date))}" aria-label="Date and time"></label>` +
    `<label class="font-row"><span class="small">Duration</span><span class="inline-controls"><input class="input dur-min" type="number" min="1" step="5" data-meta="duration_minutes" value="${esc(s.duration_minutes)}" aria-label="Duration in minutes"><span class="small muted">min</span></span></label>` +
    `<label class="font-row"><span class="small">Stage language</span><span class="meta-control"><select class="select-native" data-meta="language" aria-label="Language on the stage">` +
    LANGUAGES.map(([v, l]) => `<option value="${esc(v)}"${v === (s.language || 'en') ? ' selected' : ''}>${esc(l)}</option>`).join('') +
    `</select><span class="small muted">The words the stage says by itself: the chat hint, default titles, breakout rooms, the quiz.</span></span></label>` +
    `<label class="font-row"><span class="small">Stage hint</span><span class="meta-control"><input class="input" data-meta="chat_hint" value="${esc(s.chat_hint || '')}" placeholder="${esc(words(s.language).chat_hint)}" aria-label="Stage hint under activities">` +
    `<span class="small muted">Under activities. Empty = the language's own: “${esc(words('en').chat_hint)}” · “${esc(words('es').chat_hint)}”.</span></span></label>` +
    `</div>` +
    `<div class="settings-sub"><h4 class="settings-sub-title">${icon('type')} Stage look</h4>` +
    `<span class="chip ${look.uses_defaults ? 'ok' : ''}" data-look-state>${look.uses_defaults ? 'Using the default' : 'Overridden'}</span>` +
    (look.uses_defaults ? '' : `<button type="button" class="button-ghost" data-look-reset>${icon('rotate-ccw')} Reset to default</button>`) +
    `<span class="card-head-meta" data-lettering>${esc(letteringLabel(s.font))}</span></div>` +
    `<p class="muted small">Two fonts: the <b>title font</b> for titles and questions, the <b>text font</b> for the rest. Below, each kind of text can take either, in capitals or as typed; an item can set its own in the Plan tab. New sessions start from Settings → Stage defaults.</p>` +
    `<div class="font-rows"><label class="font-row"><span class="small">Stage theme</span><select class="select-native" aria-label="Stage theme" data-theme-select>` +
    themeOptions(look.themes, s.theme) +
    `</select></label></div>` +
    fontEditorHtml(s.font, { hint: hintOf(s), library: look.fonts });

  // One save at a time, in order, each from the session as the edits before it
  // left it: a field saved on blur and a lettering click right after never
  // overwrite each other. The change applies at once; a refused save undoes it.
  let queue = Promise.resolve();
  const saveSession = (change) => {
    const before = {};
    Object.keys(change).forEach((k) => { before[k] = s[k]; });
    Object.assign(s, change);
    const session = Object.assign({}, s);
    if (!session.font) delete session.font;  // the theme's lettering: no font block in session.yaml
    if (!session.date) delete session.date;
    const run = queue.then(async () => {
      try {
        await api(`/api/sessions/${sid}`, { method: 'PUT', body: { session } });
        return true;
      } catch (e) {
        Object.assign(s, before);
        toast(e.message, 'error');
        return false;
      }
    });
    queue = run;
    return run;
  };

  // -- the details: each field saves on its own
  const field = (name) => card.querySelector(`[data-meta="${name}"]`);
  const readField = {
    title: (el) => {
      const v = el.value.trim();
      if (!v) throw new Error('The session needs a title');
      return v;
    },
    date: (el) => (el.value ? new Date(el.value).toISOString() : null),
    duration_minutes: (el) => {
      const v = parseInt(el.value, 10);
      if (!(v >= 1)) throw new Error('The duration is at least 1 minute');
      return v;
    },
    language: (el) => el.value,
    chat_hint: (el) => el.value.trim(),
  };
  const showField = {
    title: (el) => { el.value = s.title; },
    date: (el) => { el.value = localValue(s.date); },
    duration_minutes: (el) => { el.value = s.duration_minutes; },
    language: (el) => { el.value = s.language || 'en'; },
    chat_hint: (el) => { el.value = s.chat_hint || ''; },
  };
  const sameDate = (a, b) => (a ? new Date(a).getTime() : null) === (b ? new Date(b).getTime() : null);
  const commit = async (name) => {
    const el = field(name);
    let value;
    try { value = readField[name](el); } catch (e) {
      toast(e.message, 'error');
      showField[name](el);
      return;
    }
    const now = name === 'language' ? (s.language || 'en') : s[name];
    if (name === 'date' ? sameDate(value, now) : value === (now == null ? '' : now)) return;
    const ok = await saveSession({ [name]: value });
    showField[name](el);
    if (!ok) return;
    toast('Saved to session.yaml');
    onDetails();
    if (name === 'title' || name === 'date') {  // the list, behind
      const row = sessions.find((x) => x.id === sid);
      if (row) { row.title = s.title; row.date = s.date || null; renderList(); }
    }
    if (name === 'language' || name === 'chat_hint') {  // the stage preview says it at once
      field('chat_hint').placeholder = words(s.language).chat_hint;
      card.querySelector('.font-sample .st-hint').innerHTML = icon('message-square') + esc(hintOf(s));
    }
  };
  Object.keys(readField).forEach((name) => {
    const el = field(name);
    if (el.tagName === 'SELECT') { el.addEventListener('change', () => commit(name)); return; }
    // text fields: on blur (Enter too); a date field would save every segment typed on 'change'
    el.addEventListener('blur', () => commit(name));
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
      if (e.key === 'Escape') { showField[name](el); el.blur(); }
    });
  });

  // -- the stage look
  wireFontEditor(card, {
    current: () => s.font,
    save: (next) => saveSession({ font: next }),
    redraw: renderDetail,
    pickFile: async () => {
      try { return (await api('/api/pick', { method: 'POST', body: { kind: 'font' } })).path; } catch (e) { toast(e.message, 'error'); return ''; }
    },
  });
  card.querySelector('[data-theme-select]').addEventListener('change', async (e) => {
    if (await saveSession({ theme: e.target.value })) renderDetail();
  });
  const reset = card.querySelector('[data-look-reset]');
  if (reset) {
    reset.addEventListener('click', async () => {
      try {
        await api(`/api/sessions/${sid}/look/reset`, { method: 'POST' });
        toast('Stage look reset to the defaults');
      } catch (err) { toast(err.message, 'error'); }
      renderDetail();
    });
  }
  return card;
}

function readyRow(c) {
  const labels = { import: 'Import', pin: 'Keep on PC', test_reader: 'Test', confirm_zoom_update: 'Mark done', check_quiz_reach: 'Check' };
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
  if (action === 'check_quiz_reach') {
    toast('Checking the public quiz link…');
    try {
      const r = await api('/api/quiz/reach', { method: 'POST' });
      toast(`Quiz public link: ${r.label}`, r.state === 'ok' ? 'info' : 'error');
    } catch (e) { toast(e.message, 'error'); }
    return renderDetail();
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
    toast('Session folder created');
    await refresh();
    openSession(created.id);
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
    await refresh();
    openSession(added.id);
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
    toast('Session duplicated');
    await refresh();
    openSession(dup.id);
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
