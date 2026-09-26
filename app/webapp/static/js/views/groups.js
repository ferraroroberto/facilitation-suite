// Groups tab: the roster with presence switches (left) and the three breakout
// rounds (right) — pairs, then two rounds of four that mix people — with the
// Zoom copy-paste text, the pre-assign CSV and an optional reveal slide.

import { icon } from '/static/_vendored/icons/icons.js';
import { emptyStateEl } from '/static/_vendored/empty-state/empty-state.js';
import { switchEl, setSwitch } from '/static/_vendored/switch/switch.js';
import { api, esc, pageHead, setStatus, toast } from '/static/js/ui.js';

let root;
let ctx;
let head;
let listEl;
let detailEl;
let data = null;
let tab = 'pairs';
let filter = '';
const ROUNDS = ['pairs', 'g4a', 'g4b'];

export async function mount(el, context) {
  root = el;
  ctx = context;
  root.innerHTML = '';
  const split = document.createElement('div');
  split.className = 'split groups-split';
  const list = document.createElement('div');
  list.className = 'split-list';
  detailEl = document.createElement('div');
  detailEl.className = 'split-detail';
  split.append(list, detailEl);
  root.appendChild(split);

  head = pageHead({ glyph: 'shuffle', title: 'Groups', status: '' });
  list.appendChild(head);
  const tools = document.createElement('div');
  tools.className = 'row-actions';
  tools.innerHTML = `<button type="button" class="button-surface" data-import>${icon('upload')} Import roster (.xlsx)</button>`;
  list.appendChild(tools);
  tools.querySelector('[data-import]').addEventListener('click', importRoster);
  const search = document.createElement('input');
  search.className = 'input groups-filter';
  search.placeholder = 'Filter participants';
  search.setAttribute('aria-label', 'Filter participants');
  search.addEventListener('input', () => { filter = search.value.trim().toLowerCase(); renderList(); });
  list.appendChild(search);
  listEl = document.createElement('div');
  listEl.className = 'card list-card';
  list.appendChild(listEl);

  ctx.onSession(() => load());
  await load();
}

export function show() { load(); }

async function load() {
  if (!ctx.sessionId) {
    data = null;
    setStatus(head, '');
    listEl.innerHTML = '';
    listEl.appendChild(emptyStateEl('calendar-days', 'Pick a session in the Sessions tab first.', { actionLabel: 'Sessions', onAction: () => ctx.goTo('sessions') }));
    detailEl.innerHTML = '';
    return;
  }
  try {
    data = await api(`/api/sessions/${ctx.sessionId}/groups`);
  } catch (e) {
    listEl.innerHTML = '';
    listEl.appendChild(emptyStateEl('triangle-alert', e.message, { actionLabel: 'Retry', onAction: load }));
    return;
  }
  render();
}

function render() {
  setStatus(head, data.total ? `${data.present} of ${data.total} present` : '');
  renderList();
  renderRounds();
}

function renderList() {
  listEl.innerHTML = '';
  if (!data || !data.total) {
    listEl.appendChild(emptyStateEl('users', 'No roster yet. Import an .xlsx with a name column (role, company, country, present and email are optional).',
      { actionLabel: 'Import roster', onAction: importRoster }));
    return;
  }
  const rows = data.roster.filter((p) => !filter || `${p.name} ${p.role} ${p.company} ${p.country}`.toLowerCase().includes(filter));
  if (!rows.length) {
    listEl.appendChild(emptyStateEl('search', 'Nobody matches the filter.'));
    return;
  }
  const list = document.createElement('div');
  list.className = 'list';
  rows.forEach((p) => {
    const row = document.createElement('div');
    row.className = 'list-row person-row' + (p.present ? '' : ' absent');
    const sw = switchEl(p.present, {
      label: `${p.name} present`,
      onToggle: async (next, btn) => {
        setSwitch(btn, next);
        try {
          data = await api(`/api/sessions/${ctx.sessionId}/groups/presence`, { method: 'PUT', body: { name: p.name, present: next } });
          render();
          ctx.groupsChanged();
        } catch (e) { setSwitch(btn, !next); toast(e.message, 'error'); }
      },
    });
    const meta = [p.role, p.country].filter(Boolean).join(' · ') + (p.email ? '' : (p.role || p.country ? ' · ' : '') + 'no email');
    const text = document.createElement('div');
    text.className = 'grow';
    text.innerHTML = `<div class="row-title">${esc(p.name)}</div><div class="row-meta">${esc(meta)}</div>`;
    row.append(sw, text);
    list.appendChild(row);
  });
  listEl.appendChild(list);
}

function roomCard(g, i, expected) {
  const odd = g.length !== expected;
  return `<div class="card room-card"><div class="room-head"><b>Room ${i + 1}</b>` +
    `<span class="muted small">${g.length} people${odd ? ` · ${esc(oddNote(expected))}` : ''}</span></div>` +
    `<ul class="room-names">${g.map((n) => `<li>${esc(n)}</li>`).join('')}</ul></div>`;
}

function oddNote(expected) {
  return `${data.rounds ? data.rounds.pairs.flat().length : 0} is not a multiple of ${expected}`;
}

function renderRounds() {
  detailEl.innerHTML = '';
  const title = document.createElement('div');
  title.className = 'detail-title';
  const when = data && data.shuffled_at ? `shuffled ${new Date(data.shuffled_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}` : 'not shuffled yet';
  title.innerHTML = `<h1>Breakout rounds</h1><p class="muted">Pairs, then two rounds of four · ${esc(when)}</p>`;
  detailEl.appendChild(title);

  if (!data || !data.total) return;
  if (!data.rounds) {
    const card = document.createElement('div');
    card.className = 'card';
    card.appendChild(emptyStateEl('shuffle', `${data.present} present. Shuffle to make pairs and two rounds of four with as few repeats as possible.`,
      { actionLabel: 'Shuffle', onAction: shuffle }));
    detailEl.appendChild(card);
    return;
  }
  const r = data.rounds;
  const tabs = document.createElement('div');
  tabs.className = 'range-tabs';
  tabs.setAttribute('role', 'tablist');
  tabs.innerHTML = ROUNDS.map((k) =>
    `<button type="button" role="tab" class="range-tab${k === tab ? ' active' : ''}" aria-selected="${k === tab}" data-round="${k}">${esc(data.labels[k])} · ${r[k].length} rooms</button>`).join('');
  tabs.addEventListener('click', (e) => {
    const b = e.target.closest('[data-round]');
    if (b) { tab = b.dataset.round; renderRounds(); }
  });
  detailEl.appendChild(tabs);

  const note = document.createElement('div');
  if (data.stale) {
    note.className = 'banner warn';
    note.innerHTML = `${icon('triangle-alert')} <span>Presence changed since the shuffle — shuffle again so the rooms match who is here.</span>`;
  } else {
    note.className = 'banner ok';
    const msg = {
      pairs: 'Pairs are random; an odd number gives one trio.',
      g4a: 'Round A keeps each pair together.',
      g4b: `Round B: ${data.mixing} ${data.mixing === 1 ? 'person shares' : 'people share'} a room with someone from their round-A group.`,
    }[tab];
    note.innerHTML = `${icon('circle-check')} <span>${esc(msg)}</span>`;
  }
  detailEl.appendChild(note);

  const grid = document.createElement('div');
  grid.className = 'room-grid';
  grid.innerHTML = r[tab].map((g, i) => roomCard(g, i, tab === 'pairs' ? 2 : 4)).join('');
  detailEl.appendChild(grid);

  const actions = document.createElement('div');
  actions.className = 'row-actions groups-actions';
  const noCsv = data.missing_emails && data.missing_emails.length;
  actions.innerHTML =
    `<button type="button" class="button-primary" data-shuffle>${icon('shuffle')} Shuffle again</button>` +
    `<button type="button" class="button-tint" data-copy>${icon('copy')} Copy rooms for Zoom</button>` +
    `<button type="button" class="button-surface" data-csv ${noCsv ? 'disabled' : ''}>${icon('download')} Export Zoom pre-assign CSV</button>` +
    `<button type="button" class="button-surface" data-reveal>${icon('presentation')} Add reveal slide</button>`;
  detailEl.appendChild(actions);
  if (noCsv) {
    const p = document.createElement('p');
    p.className = 'small muted';
    p.textContent = `The pre-assign CSV needs an email for everyone present — missing for ${data.missing_emails.join(', ')}. Copy the rooms instead, or add the emails to the roster.`;
    detailEl.appendChild(p);
  }
  actions.querySelector('[data-shuffle]').addEventListener('click', shuffle);
  actions.querySelector('[data-copy]').addEventListener('click', () => copy(data.texts[tab]));
  actions.querySelector('[data-csv]').addEventListener('click', () => { window.location.href = `/api/sessions/${ctx.sessionId}/groups/zoom.csv?round=${tab}`; });
  actions.querySelector('[data-reveal]').addEventListener('click', addReveal);
}

async function importRoster() {
  if (!ctx.sessionId) { toast('Pick a session first', 'error'); return; }
  try {
    const picked = await api('/api/pick', { method: 'POST', body: { kind: 'xlsx' } });
    if (!picked.path) return;
    data = await api(`/api/sessions/${ctx.sessionId}/roster`, { method: 'POST', body: { path: picked.path } });
    toast(`Roster imported: ${data.total} people`);
    render();
    ctx.groupsChanged();
  } catch (e) { toast(e.message, 'error'); }
}

async function shuffle() {
  const btn = detailEl.querySelector('[data-shuffle]');
  if (btn) { btn.disabled = true; btn.textContent = 'Shuffling…'; }
  try {
    data = await api(`/api/sessions/${ctx.sessionId}/groups/shuffle`, { method: 'POST', body: {} });
    render();
    ctx.groupsChanged();
  } catch (e) {
    toast(e.message, 'error');
    if (btn) { btn.disabled = false; btn.innerHTML = `${icon('shuffle')} Shuffle again`; }
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Rooms copied — paste them while assigning Zoom breakout rooms');
  } catch (e) { toast('Could not copy', 'error'); }
}

async function addReveal() {
  try {
    const r = await ctx.addToPlan({ kind: 'activity', type: 'groups_reveal', profile: 'screen_only', options: { round: tab } });
    toast(`"Who are you with?" added to ${r.section} — not saved yet: Save in the Plan tab`);
  } catch (e) { toast(e.message, 'error'); }
}
