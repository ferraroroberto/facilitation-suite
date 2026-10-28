// Results tab: every captured activity and quiz game in the order it happened
// (left) and the selected one (right) — the visual exactly as the stage showed
// it at the stop, its top items, every answer with the person's name; for a
// quiz, its podium, leaderboard and each question's distribution — plus the
// check against Zoom's saved chat, the session PDF and the Excel report.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, pageHead, setStatus, toast } from '/static/js/ui.js';
import { analyzeCard } from '/static/js/views/analyze-card.js';
import { rebuildCard } from '/static/js/views/rebuild-card.js';

let root;
let ctx;
let head;
let checkEl;
let peopleEl;
let rebuildEl;
let rebuildSid = null; // the card keeps its own state across the reloads a rebuild or an undo causes
let listEl;
let detailEl;
let data = null;
let selected = null;
let analyzeEl = null; // built once per load, so choosing another activity does not re-read the session
let busy = false;

export async function mount(el, context) {
  root = el;
  ctx = context;
  root.innerHTML = '';
  const split = document.createElement('div');
  split.className = 'split results-split';
  const list = document.createElement('div');
  list.className = 'split-list';
  detailEl = document.createElement('div');
  detailEl.className = 'split-detail';
  split.append(list, detailEl);
  root.appendChild(split);

  head = pageHead({ glyph: 'chart-column', title: 'Results', status: '' });
  checkEl = document.createElement('div');
  listEl = document.createElement('div');
  listEl.className = 'card list-card';
  peopleEl = document.createElement('div');
  rebuildEl = document.createElement('div');
  list.append(head, checkEl, peopleEl, listEl, rebuildEl);

  ctx.onSession(() => { selected = null; rebuildSid = null; load(); });
  await load();
}

export function show() { load(); }

const base = () => `/api/sessions/${ctx.sessionId}`;

async function load() {
  if (!ctx.sessionId) {
    data = null;
    setStatus(head, '');
    checkEl.innerHTML = '';
    peopleEl.innerHTML = '';
    rebuildEl.hidden = true;
    rebuildSid = null;
    listEl.innerHTML = '';
    listEl.appendChild(emptyStateEl('calendar-days', 'Pick a session in the Sessions tab first.', { actionLabel: 'Sessions', onAction: () => ctx.goTo('sessions') }));
    detailEl.innerHTML = '';
    return;
  }
  try {
    data = await api(`${base()}/results`);
  } catch (e) {
    listEl.innerHTML = '';
    listEl.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Retry', onAction: load }));
    return;
  }
  const keys = entries().map((e) => e.key);
  if (!keys.includes(selected)) selected = keys.length ? keys[0] : null;
  render();
}

function shortDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

function stamp(ms) {
  return new Date(ms).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function render() {
  analyzeEl = analyzeCard(ctx.sessionId);
  setStatus(head, [data.session.title, shortDate(data.session.date)].filter(Boolean).join(' · '));
  renderCheck();
  renderPeople();
  renderRebuild();
  renderList();
  renderDetail();
}

// A rebuild only makes sense once something was frozen; the card is built once per session.
function renderRebuild() {
  rebuildEl.hidden = !data.activities.length;
  if (rebuildEl.hidden || rebuildSid === ctx.sessionId) return;
  rebuildSid = ctx.sessionId;
  rebuildEl.replaceChildren(rebuildCard(ctx.sessionId, data.rebuild_backup, load));
}

// ---- one person under several names -------------------------------------------

const joinNames = (names) => names.join(', ');

// Likely groups wait for a click (nothing merges on its own); a merge is saved as aliases.yaml in the session folder.
function renderPeople() {
  peopleEl.innerHTML = '';
  const p = data.people;
  if (!data.went_live || !p) return;
  const merges = Object.entries(p.merges);
  if (!p.suggestions.length && !merges.length && p.names.length < 2) return;
  const count = [p.suggestions.length ? `${p.suggestions.length} suggested` : '', merges.length ? `${merges.length} merged` : '']
    .filter(Boolean).join(' · ');
  const det = document.createElement('details');
  det.className = 'card card--collapsible';
  det.open = p.suggestions.length > 0;
  det.innerHTML = `<summary class="collapse-summary"><span class="collapse-main">${icon('users')}` +
    `<h3 class="collapse-title">People under several names</h3><span class="collapse-count">${esc(count)}</span></span>` +
    `<span class="collapse-chevron" aria-hidden="true">›</span></summary><div class="collapse-body"></div>`;
  const body = det.querySelector('.collapse-body');
  const rows = [
    ...p.suggestions.map((g, i) => `<div class="list-row diff-row"><span class="chip">Suggested</span>` +
      `<div class="grow"><div class="row-title">${esc(g.canonical)}</div><div class="row-meta">also ${esc(joinNames(g.aliases))}</div></div>` +
      `<button type="button" class="button-tint" data-merge="${i}" data-track="results.merge-names">Merge</button></div>`),
    ...merges.map(([canonical, names]) => `<div class="list-row diff-row"><span class="chip ok">Merged</span>` +
      `<div class="grow"><div class="row-title">${esc(canonical)}</div><div class="row-meta">also ${esc(joinNames(names))}</div></div>` +
      `<button type="button" class="button-surface" data-unmerge="${esc(canonical)}" data-track="results.unmerge-names">Undo</button></div>`),
  ];
  body.innerHTML = `<div class="list">${rows.join('')}</div>` +
    (p.names.length > 1
      ? `<div class="merge-manual"><span class="small muted">Merge by hand</span>` +
        `<select class="select-native" data-from aria-label="Name to merge">${p.names.map((n) => `<option>${esc(n)}</option>`).join('')}</select>` +
        `<span class="small muted">into</span>` +
        `<select class="select-native" data-into aria-label="Name to merge into">${p.names.map((n, i) => `<option${i === 1 ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>` +
        `<button type="button" class="button-tint" data-manual data-track="results.merge-names">Merge</button></div>`
      : '');
  body.addEventListener('click', (e) => {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.dataset.merge != null) {
      const g = p.suggestions[Number(t.dataset.merge)];
      mergeNames(g.canonical, g.aliases);
    } else if (t.dataset.unmerge != null) {
      post('unmerge', { name: t.dataset.unmerge });
    } else if (t.dataset.manual != null) {
      const from = body.querySelector('[data-from]').value;
      const into = body.querySelector('[data-into]').value;
      if (from === into) toast('Pick two different names', 'error');
      else mergeNames(into, [from]);
    }
  });
  peopleEl.appendChild(det);
}

const mergeNames = (canonical, aliases) => post('merge', { canonical, aliases });

async function post(action, body) {
  try {
    await api(`${base()}/aliases/${action}`, { method: 'POST', body });
    await load();
  } catch (e) { toast(e.message, 'error'); }
}

// ---- the check against Zoom's saved chat ------------------------------------

function renderCheck() {
  checkEl.innerHTML = '';
  if (!data.went_live) return;
  const r = data.reconciliation;
  if (!r) {
    const card = document.createElement('div');
    card.className = 'card check-card';
    card.innerHTML = `<div class="grow"><div class="row-title">Check against Zoom's saved chat</div>` +
      `<div class="row-meta">Zoom saves the whole chat when the meeting ends — compare it with what the app caught.</div></div>` +
      `<button type="button" class="button-tint" data-check data-track="results.check-file">${icon('file-text')} Choose file</button>`;
    card.querySelector('[data-check]').addEventListener('click', check);
    checkEl.appendChild(card);
    return;
  }
  const allIn = r.missing_count === 0;
  const banner = document.createElement('div');
  banner.className = `banner ${allIn ? 'ok' : 'warn'} check-banner`;
  const text = allIn
    ? `Checked against Zoom's saved chat · ${r.matched} of ${r.zoom_messages} messages matched`
    : `Zoom's saved chat · ${r.matched} of ${r.zoom_messages} messages matched · ${r.missing_count} missing from the app's record`;
  banner.innerHTML = `${icon(allIn ? 'circle-check' : 'triangle-alert')}<span class="grow">${esc(text)}</span>` +
    `<button type="button" class="banner-action" data-check data-track="results.check-file" title="Check another file">Check again</button>`;
  banner.querySelector('[data-check]').addEventListener('click', check);
  checkEl.appendChild(banner);
  const notes = [];
  if (r.extra_count) notes.push(`${r.extra_count} only in the app's record`);
  if (r.reactions) notes.push(`${r.reactions} reactions not counted`);
  if (r.simulated) notes.push(`${r.simulated} simulated left out`);
  if (!r.missing_count && !r.extra_count) {
    if (notes.length) checkEl.insertAdjacentHTML('beforeend', `<p class="small muted check-note">${esc(notes.join(' · '))}</p>`);
    return;
  }
  const rows = [
    ...r.missing.map((m) => ({ ...m, tag: 'Missing' })),
    ...r.extra.map((m) => ({ ...m, tag: 'Only in the app' })),
  ];
  const det = document.createElement('details');
  det.className = 'card card--collapsible';
  det.innerHTML = `<summary class="collapse-summary"><span class="collapse-main">${icon('message-square')}` +
    `<h3 class="collapse-title">Messages that differ</h3><span class="collapse-count">${esc(notes.length ? notes.join(' · ') : `${rows.length}`)}</span></span>` +
    `<span class="collapse-chevron" aria-hidden="true">›</span></summary>` +
    `<div class="collapse-body"><div class="list">${rows.map((m) =>
      `<div class="list-row diff-row"><span class="chip ${m.tag === 'Missing' ? 'warn' : ''}">${esc(m.tag)}</span>` +
      `<div class="grow"><div class="row-title">${esc(m.sender)} <span class="muted small">${esc(m.time)}</span></div>` +
      `<div class="row-meta">${esc(m.text)}</div></div></div>`).join('')}</div></div>`;
  checkEl.appendChild(det);
}

async function check() {
  try {
    const picked = await api('/api/pick', { method: 'POST', body: { kind: 'zoom_chat' } });
    if (!picked.path) return;
    const r = await api(`${base()}/reconcile`, { method: 'POST', body: { path: picked.path } });
    data.reconciliation = r;
    renderCheck();
    toast(r.missing_count ? `${r.missing_count} of Zoom's messages are missing — listed under the banner` : `All ${r.matched} of Zoom's messages matched`);
  } catch (e) { toast(e.message, 'error'); }
}

// ---- the activity list --------------------------------------------------------

const quizKey = (id) => `quiz:${id}`;

// Activities in their order, each quiz game slotted in before the first activity that started after it.
function entries() {
  const quizzes = [...data.quizzes];
  const out = [];
  const takeQuizzes = (before) => {
    while (quizzes.length && (before == null || (quizzes[0].start_ms != null && quizzes[0].start_ms < before))) {
      const q = quizzes.shift();
      out.push({ key: quizKey(q.id), quiz: q });
    }
  };
  data.activities.forEach((a) => {
    if (a.start_ms != null) takeQuizzes(a.start_ms);
    out.push({ key: a.id, activity: a });
  });
  takeQuizzes(null);
  return out;
}

function renderList() {
  listEl.innerHTML = '';
  if (!data.activities.length && !data.quizzes.length) {
    listEl.appendChild(emptyStateEl('chart-column', data.went_live
      ? 'No activity was captured in this session yet. Press Space on an activity during the session to capture answers.'
      : 'Results appear after a live session.'));
    return;
  }
  entries().forEach(({ key, activity: a, quiz: q }) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'result-row' + (key === selected ? ' selected' : '');
    row.dataset.item = key;
    row.innerHTML = `<span class="result-num">${a ? a.number : icon('crown')}</span>` +
      `<span class="grow"><span class="row-title">${esc(a ? a.title : q.label)}</span><span class="row-meta">${esc(a ? a.summary : q.summary)}</span></span>` +
      icon('chevron-right');
    row.addEventListener('click', () => select(key));
    listEl.appendChild(row);
  });
}

function select(id) {
  selected = id;
  listEl.querySelectorAll('.result-row').forEach((r) => r.classList.toggle('selected', r.dataset.item === id));
  renderDetail();
  if (window.matchMedia('(max-width: 1099px)').matches) detailEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---- the selected activity + exports -------------------------------------------

function renderDetail() {
  detailEl.innerHTML = '';
  const a = data.activities.find((x) => x.id === selected);
  const q = data.quizzes.find((x) => quizKey(x.id) === selected);
  if (a) detailEl.appendChild(activityDetail(a));
  if (q) detailEl.appendChild(quizDetail(q));
  if (data.pages.length || data.activities.length) detailEl.appendChild(pdfCard());
  if (data.pages.length || data.activities.length) detailEl.appendChild(exportActions());
  const open = openLinks();
  if (open) detailEl.appendChild(open);
  detailEl.appendChild(analyzeEl);
}

function activityDetail(a) {
  const wrap = document.createElement('div');
  wrap.className = 'result-detail';
  const meta = [`${a.answers} answers from ${a.people} people`, a.captured ? `captured ${a.captured}` : '', a.hidden ? `${a.hidden} hidden` : '', a.excluded ? `${a.excluded} from excluded senders` : '']
    .filter(Boolean).join(' · ');
  wrap.innerHTML = `<div class="detail-title"><h1>${esc(a.title)}</h1><p class="muted">${esc(meta)}</p></div>` +
    (a.has_png
      ? `<div class="card result-visual"><img alt="${esc(a.title)} as the stage showed it" src="${base()}/results/captures/${encodeURIComponent(a.id)}.png?v=${encodeURIComponent(a.frozen_at || '')}"></div>`
      : `<div class="card result-visual missing">${icon('image')}<span>The stage image was not saved for this capture — the answers below are complete.</span></div>`);
  const grid = document.createElement('div');
  grid.className = 'result-cards' + (a.top.length ? '' : ' single');
  if (a.top.length) {
    grid.insertAdjacentHTML('beforeend', `<div class="card"><h3 class="card-title">${esc(a.top_label || 'Top')}</h3><div class="list">${
      a.top.map((t) => `<div class="list-row top-row"><span class="grow">${esc(t.label)}</span><b>${t.count}</b></div>`).join('')}</div></div>`);
  }
  const rows = a.answer_rows;
  grid.insertAdjacentHTML('beforeend', `<div class="card answers-card"><h3 class="card-title">Answers, with names</h3>${rows.length ? `<div class="list">${
    rows.map((r) => `<div class="list-row answer-row${r.hidden ? ' hidden-answer' : ''}"><b class="answer-name" title="${esc(r.label)}">${esc(r.label)}</b>` +
      `<span class="grow">${esc(r.text)}${r.hidden ? ` <span class="chip">${r.excluded ? 'excluded' : 'hidden'}</span>` : ''}</span><span class="muted small">${esc(r.time)}</span></div>`).join('')
  }</div>` : '<p class="muted small">No answers.</p>'}</div>`);
  wrap.appendChild(grid);
  return wrap;
}

function seconds(ms) {
  return ms == null ? '–' : `${(ms / 1000).toFixed(1)} s`;
}

function quizDetail(q) {
  const wrap = document.createElement('div');
  wrap.className = 'result-detail';
  const played = q.start_ms ? `played ${new Date(q.start_ms).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}` : '';
  const asked = q.question_count < q.planned_questions ? `${q.question_count} of ${q.planned_questions} questions asked` : `${q.question_count} questions`;
  const meta = [`${q.player_count} player${q.player_count === 1 ? '' : 's'}`, asked, played].filter(Boolean).join(' · ');
  wrap.innerHTML = `<div class="detail-title"><h1>${esc(q.label)}</h1><p class="muted">${esc(meta)}</p></div>`;
  const grid = document.createElement('div');
  grid.className = 'result-cards';
  grid.insertAdjacentHTML('beforeend', `<div class="card"><h3 class="card-title">Podium</h3>${q.podium.length ? `<div class="list">${
    q.podium.map((r) => `<div class="list-row top-row"><span class="result-num">${r.rank}</span><span class="grow">${esc(r.name)}</span><b>${r.score}</b></div>`).join('')
  }</div>` : '<p class="muted small">Nobody played.</p>'}</div>`);
  grid.insertAdjacentHTML('beforeend', `<div class="card answers-card"><h3 class="card-title">Leaderboard</h3>${q.leaderboard.length ? `<div class="list">${
    q.leaderboard.map((r) => `<div class="list-row quiz-rank-row"><span class="result-num">${r.rank}</span>` +
      `<span class="grow"><span class="row-title">${esc(r.name)} <span class="chip">${esc(r.source)}</span></span>` +
      `<span class="row-meta">${r.correct} of ${q.question_count} correct · avg ${esc(seconds(r.avg_ms))}</span></span><b>${r.score}</b></div>`).join('')
  }</div>` : '<p class="muted small">No players.</p>'}</div>`);
  wrap.appendChild(grid);
  q.questions.forEach((x) => {
    const top = Math.max(1, ...x.answers.map((o) => o.count));
    wrap.insertAdjacentHTML('beforeend', `<div class="card quiz-question"><h3 class="card-title">${x.number} · ${esc(x.question)}</h3>` +
      `<div class="list">${x.answers.map((o) => `<div class="quiz-bar-row${o.correct ? ' correct' : ''}">` +
        `<span class="quiz-answer">${o.n} · ${esc(o.text)}${o.correct ? ` <span class="quiz-correct">${icon('circle-check')} correct</span>` : ''}</span>` +
        `<span class="quiz-bar" aria-hidden="true"><span style="width:${Math.round((100 * o.count) / top)}%"></span></span><b>${o.count}</b></div>`).join('')}</div>` +
      `<p class="small muted">${x.answered} of ${x.players} players answered</p></div>`);
  });
  return wrap;
}

function pdfCard() {
  const card = document.createElement('div');
  card.className = 'card pdf-card';
  const pdf = data.exports.pdf;
  const built = pdf ? ` · exported ${stamp(pdf.modified_ms)}${pdf.pages ? ` · ${pdf.pages} pages` : ''}` : '';
  const quizPages = data.quiz_pages ? ` + ${data.quiz_pages} quiz pages` : '';
  card.innerHTML = `<div class="pdf-head"><b>Session PDF</b><span class="muted small">${data.slides} slides + ${data.captures} live results${quizPages}, in the order they happened${esc(built)}</span></div>`;
  const strip = document.createElement('div');
  strip.className = 'pdf-strip';
  strip.innerHTML = data.pages.map((p) => p.kind === 'slide'
    ? `<img class="pdf-tile" loading="lazy" alt="${esc(p.title)}" title="${esc(p.title)}" src="${base()}/slides/${encodeURIComponent(p.file)}">`
    : `<button type="button" class="pdf-tile live" data-track="results.open-item" data-item="${esc(p.game ? quizKey(p.game) : p.item_id)}" title="${esc(p.title)}">` +
      `<span class="pdf-tile-text">${esc(p.label)}${p.tile ? ` · ${esc(p.tile)}` : ''}</span></button>`).join('') +
    '<span class="pdf-tile appendix">Answers appendix</span>';
  strip.addEventListener('click', (e) => {
    const b = e.target.closest('[data-item]');
    if (b) select(b.dataset.item);
  });
  card.appendChild(strip);
  card.insertAdjacentHTML('beforeend', '<p class="small muted pdf-note">Each visual was saved exactly as the audience saw it when you stopped the capture. Everything lives in the session folder › exports/.</p>');
  return card;
}

function exportActions() {
  const row = document.createElement('div');
  row.className = 'row-actions export-actions';
  row.innerHTML = `<button type="button" class="button-primary" data-pdf data-track="results.export-pdf">${icon('download')} Export session PDF</button>` +
    `<button type="button" class="button-surface" data-xlsx data-track="results.export-xlsx" ${data.activities.length || data.quizzes.length ? '' : 'disabled'}>${icon('file-text')} Excel report</button>`;
  row.querySelector('[data-pdf]').addEventListener('click', exportPdf);
  row.querySelector('[data-xlsx]').addEventListener('click', exportXlsx);
  return row;
}

// The files stay in the session folder's exports/; these links open them in a tab
// (inline, never a download) for a device that cannot reach that folder.
function openLinks() {
  const links = [];
  const tag = 'target="_blank" rel="noopener"';
  if (data.exports.pdf) links.push(`<a class="button-tint" href="${base()}/exports/session.pdf" ${tag} data-track="results.open-pdf">${icon('external-link')} Open session PDF</a>`);
  if (data.exports.xlsx) links.push(`<a class="button-tint" href="${base()}/exports/report.xlsx" ${tag} data-track="results.open-xlsx">${icon('external-link')} Open Excel report</a>`);
  if (!links.length) return null;
  const row = document.createElement('div');
  row.className = 'row-actions export-open';
  row.innerHTML = links.join('');
  return row;
}

async function exportPdf(e) {
  const r = await runExport(e, 'pdf');
  if (!r) return;
  const gaps = r.missing_images.length ? ` · ${r.missing_images.length} capture${r.missing_images.length === 1 ? '' : 's'} without an image` : '';
  toast(`Session PDF saved in exports/${r.pages ? ` · ${r.pages} pages` : ''}${gaps}`);
}

async function exportXlsx(e) {
  if (await runExport(e, 'xlsx')) toast('Excel report saved in exports/');
}

// Builds one export on the server and records its file info; null when it failed (the toast says why).
async function runExport(e, key) {
  if (busy) return null;
  busy = true;
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.textContent = 'Exporting…';
  let r = null;
  try {
    r = await api(`${base()}/exports/${key}`, { method: 'POST' });
    data.exports[key] = { modified_ms: r.modified_ms, pages: r.pages };
  } catch (err) { toast(err.message, 'error'); }
  busy = false;
  renderDetail();
  return r;
}
