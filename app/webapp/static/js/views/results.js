// Results tab: every captured activity in the order it happened (left) and the
// selected one (right) — the visual exactly as the stage showed it at the
// stop, its top items, every answer with the person's name — plus the check
// against Zoom's saved chat, the session PDF and the Excel report.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { api, esc, pageHead, setStatus, toast } from '/static/js/ui.js';

let root;
let ctx;
let head;
let checkEl;
let listEl;
let detailEl;
let data = null;
let selected = null;
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
  list.append(head, checkEl, listEl);

  ctx.onSession(() => { selected = null; load(); });
  await load();
}

export function show() { load(); }

const base = () => `/api/sessions/${ctx.sessionId}`;

async function load() {
  if (!ctx.sessionId) {
    data = null;
    setStatus(head, '');
    checkEl.innerHTML = '';
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
  if (!data.activities.some((a) => a.id === selected)) selected = data.activities.length ? data.activities[0].id : null;
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
  setStatus(head, [data.session.title, shortDate(data.session.date)].filter(Boolean).join(' · '));
  renderCheck();
  renderList();
  renderDetail();
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
      `<button type="button" class="button-tint" data-check>${icon('file-text')} Choose file</button>`;
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
    `<button type="button" class="banner-action" data-check title="Check another file">Check again</button>`;
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

function renderList() {
  listEl.innerHTML = '';
  if (!data.activities.length) {
    listEl.appendChild(emptyStateEl('chart-column', data.went_live
      ? 'No activity was captured in this session yet. Press Space on an activity during the session to capture answers.'
      : 'Results appear after a live session.'));
    return;
  }
  data.activities.forEach((a) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'result-row' + (a.id === selected ? ' selected' : '');
    row.dataset.item = a.id;
    row.innerHTML = `<span class="result-num">${a.number}</span>` +
      `<span class="grow"><span class="row-title">${esc(a.title)}</span><span class="row-meta">${esc(a.summary)}</span></span>` +
      icon('chevron-right');
    row.addEventListener('click', () => select(a.id));
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
  if (a) detailEl.appendChild(activityDetail(a));
  if (data.pages.length || data.activities.length) detailEl.appendChild(pdfCard());
  if (data.pages.length || data.activities.length) detailEl.appendChild(exportActions());
}

function activityDetail(a) {
  const wrap = document.createElement('div');
  wrap.className = 'result-detail';
  const meta = [`${a.answers} answers from ${a.people} people`, a.captured ? `captured ${a.captured}` : '', a.hidden ? `${a.hidden} hidden` : '']
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
    rows.map((r) => `<div class="list-row answer-row${r.hidden ? ' hidden-answer' : ''}"><b class="answer-name">${esc(r.sender)}</b>` +
      `<span class="grow">${esc(r.text)}${r.hidden ? ' <span class="chip">hidden</span>' : ''}</span><span class="muted small">${esc(r.time)}</span></div>`).join('')
  }</div>` : '<p class="muted small">No answers.</p>'}</div>`);
  wrap.appendChild(grid);
  return wrap;
}

function pdfCard() {
  const card = document.createElement('div');
  card.className = 'card pdf-card';
  const pdf = data.exports.pdf;
  const built = pdf ? ` · exported ${stamp(pdf.modified_ms)}${pdf.pages ? ` · ${pdf.pages} pages` : ''}` : '';
  card.innerHTML = `<div class="pdf-head"><b>Session PDF</b><span class="muted small">${data.slides} slides + ${data.captures} live results, in the order they happened${esc(built)}</span></div>`;
  const strip = document.createElement('div');
  strip.className = 'pdf-strip';
  strip.innerHTML = data.pages.map((p) => p.kind === 'slide'
    ? `<img class="pdf-tile" loading="lazy" alt="${esc(p.title)}" title="${esc(p.title)}" src="${base()}/slides/${encodeURIComponent(p.file)}">`
    : `<button type="button" class="pdf-tile live" data-item="${esc(p.item_id)}" title="${esc(p.title)}">${esc(p.label)}${p.tile ? ` · ${esc(p.tile)}` : ''}</button>`).join('') +
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
  row.innerHTML = `<button type="button" class="button-primary" data-pdf>${icon('download')} Export session PDF</button>` +
    `<button type="button" class="button-surface" data-xlsx ${data.activities.length ? '' : 'disabled'}>${icon('file-text')} Excel report</button>`;
  row.querySelector('[data-pdf]').addEventListener('click', exportPdf);
  row.querySelector('[data-xlsx]').addEventListener('click', () => download(`${base()}/exports/report.xlsx`, 'report.xlsx', 'Excel report saved in exports/'));
  return row;
}

async function exportPdf(e) {
  if (busy) return;
  busy = true;
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.textContent = 'Exporting…';
  try {
    const r = await api(`${base()}/exports/pdf`, { method: 'POST' });
    data.exports.pdf = { modified_ms: r.modified_ms, pages: r.pages };
    const gaps = r.missing_images.length ? ` · ${r.missing_images.length} capture${r.missing_images.length === 1 ? '' : 's'} without an image` : '';
    await download(`${base()}/exports/session.pdf`, 'session.pdf', `Session PDF ready${r.pages ? ` · ${r.pages} pages` : ''}${gaps}`);
  } catch (err) { toast(err.message, 'error'); }
  busy = false;
  renderDetail();
}

async function download(url, name, done) {
  try {
    const res = await fetch(url);
    if (!res.ok) {
      let msg = `Download failed (${res.status})`;
      try { msg = (await res.json()).error.message || msg; } catch (e) { /* not JSON */ }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    toast(done);
  } catch (e) { toast(e.message, 'error'); }
}
