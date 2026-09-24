// Settings — shared by every session: OBS (connection + the three profiles:
// scene and camera zone), the chat reader, the phone remote, the Stream Deck
// buttons and the credits.

import { icon } from '/static/_vendored/icons/icons.js';
import { switchEl, setSwitch } from '/static/_vendored/switch/switch.js';
import { api, esc, pageHead, setStatus, toast } from '/static/js/ui.js';
import { formDialog } from '/static/js/dialogs.js';

const CREDITS = [
  ['GeoNames', 'https://www.geonames.org', 'the map\'s cities and countries (cities15000), licensed CC BY 4.0'],
  ['Natural Earth · world-atlas', 'https://github.com/topojson/world-atlas', 'the country outlines, public domain'],
  ['Unicode CLDR', 'https://cldr.unicode.org', 'country names in Spanish and English'],
  ['Patrick Hand', 'https://fonts.google.com/specimen/Patrick+Hand', 'the stage lettering, SIL Open Font License'],
  ['Lucide', 'https://lucide.dev', 'icons, ISC license'],
];
const OBS_CHIP = {
  connected: ['ok', 'OBS connected'],
  connecting: ['warn', 'Connecting…'],
  disconnected: ['bad', 'OBS not reachable'],
  off: ['', 'Switching off'],
};

let root;
let head;
let data = null;

export async function mount(el) {
  root = el;
  head = pageHead({ glyph: 'settings', title: 'Settings', status: 'Shared by every session', settings: false });
  await load();
}

export function show() { load(); }

async function load() {
  try {
    data = await api('/api/settings');
  } catch (e) {
    toast(e.message, 'error');
    return;
  }
  render();
}

function zoneThumb(zone) {
  const z = zone ? `<span style="left:${zone[0] * 100}%;top:${zone[1] * 100}%;width:${(zone[2] - zone[0]) * 100}%;height:${(zone[3] - zone[1]) * 100}%"></span>` : '';
  return `<span class="zone-thumb" aria-hidden="true">${z}</span>`;
}

function render() {
  root.innerHTML = '';
  root.appendChild(head);
  setStatus(head, 'Shared by every session');
  root.appendChild(obsCard());
  root.appendChild(readerCard());
  root.appendChild(remoteCard());
  root.appendChild(streamDeckCard());
  root.appendChild(creditsCard());
}

function remoteCard() {
  const r = data.remote;
  const card = document.createElement('div');
  card.className = 'card settings-card';
  const state = !r.https ? ['warn', 'needs HTTPS'] : r.enabled ? ['ok', 'on'] : ['', 'off'];
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('smartphone')} Phone remote</h3><span class="chip ${state[0]}">${state[1]}</span></div>` +
    '<p class="small muted settings-note">Next, previous, capture, timer and blackout from your phone, over Tailscale. Open the link once on the phone: it pairs that phone. ' +
    'Other devices never get in without it; this PC never needs it.</p>';
  if (!r.https) {
    card.insertAdjacentHTML('beforeend', '<p class="small settings-note">HTTPS is not set up on this PC yet: run <code>scripts/gen_tailscale_cert.py</code> (a Tailscale certificate), then restart the tray.</p>');
    return card;
  }
  const row = document.createElement('div');
  row.className = 'row-actions remote-actions';
  if (r.enabled && r.link) {
    card.insertAdjacentHTML('beforeend', `<div class="list-row deck-row"><span class="deck-label">Pairing link</span><code class="grow" data-link>${esc(r.link.replace(/token=.*/, 'token=•••'))}</code></div>`);
    row.innerHTML = `<button type="button" class="button-tint" data-copy>${icon('copy')} Copy the link</button>` +
      `<button type="button" class="button-surface" data-new>${icon('refresh-cw')} New link (unpairs phones)</button>` +
      `<button type="button" class="button-surface" data-off>${icon('x')} Turn off</button>`;
  } else {
    row.innerHTML = `<button type="button" class="button-tint" data-new>${icon('smartphone')} Make the phone link</button>`;
  }
  card.appendChild(row);
  const on = (sel, fn) => { const b = row.querySelector(sel); if (b) b.addEventListener('click', fn); };
  on('[data-copy]', async () => {
    try { await navigator.clipboard.writeText(r.link); toast('Link copied — open it on the phone (send it to yourself)'); } catch (e) { toast('Could not copy', 'error'); }
  });
  on('[data-new]', async () => {
    try { data = await api('/api/settings/remote/token', { method: 'POST' }); render(); toast(r.enabled ? 'New link made — phones paired before need it again' : 'Phone link made'); } catch (e) { toast(e.message, 'error'); }
  });
  on('[data-off]', async () => {
    try { data = await api('/api/settings/remote/token', { method: 'DELETE' }); render(); toast('Phone remote off'); } catch (e) { toast(e.message, 'error'); }
  });
  return card;
}

let actions = null;
function streamDeckCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card';
  const base = data.remote.base_url;
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('keyboard')} Stream Deck buttons</h3><span class="muted small">one URL per button</span></div>` +
    `<p class="small muted settings-note">Each button sends a POST to <code>${esc(base)}</code> plus the path below — the same pattern as home-automation's action alias, so the fleet Stream Deck plugin's “Call Action” key works with it. No token is needed from this PC (the fleet plugin's <code>FACILITATION_SUITE_BASE_URL</code> is this address).</p>` +
    `<div class="list deck-list" data-deck><p class="muted small">Loading…</p></div>`;
  const load = actions ? Promise.resolve(actions) : api('/api/actions').then((r) => { actions = r.actions; return actions; });
  load.then((list) => {
    const rows = [];
    list.filter((a) => a.stream_deck).forEach((a) => {
      if (a.id === 'obs_profile') {
        Object.entries(data.profiles).forEach(([k, p]) => rows.push([`Switch to ${p.label}`, `/api/actions/obs_profile/${k}`]));
      } else if (a.arg) {
        rows.push([`${a.label} (1 = the first)`, `/api/actions/${a.id}/1`]);
      } else {
        rows.push([a.label, `/api/actions/${a.id}`]);
      }
    });
    const box = card.querySelector('[data-deck]');
    box.innerHTML = rows.map(([label, path]) =>
      `<div class="list-row deck-row"><span class="deck-label">${esc(label)}</span><code class="grow">${esc(path)}</code>` +
      `<button type="button" class="button-surface" data-copy="${esc(base + path)}">${icon('copy')} Copy</button></div>`).join('');
    box.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-copy]');
      if (!b) return;
      try { await navigator.clipboard.writeText(b.dataset.copy); toast('URL copied'); } catch (err) { toast('Could not copy', 'error'); }
    });
  }).catch((e) => { card.querySelector('[data-deck]').textContent = e.message; });
  return card;
}

function obsCard() {
  const st = data.obs_state;
  const [kind, label] = OBS_CHIP[st.state] || ['bad', st.state];
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('video')} OBS profiles</h3>` +
    `<span class="chip ${kind}" title="${esc(st.detail)}"><span class="dot"></span>${esc(label)}</span></div>` +
    `<div class="list" data-profiles></div>` +
    `<p class="small muted settings-note">Each item in the plan has a profile; the app switches OBS to that profile's scene when the item goes on stage, and the stage keeps the profile's camera zone empty so your camera never covers content.</p>` +
    `<div class="list"><button type="button" class="list-row settings-row" data-conn>` +
    `<span class="settings-ico">${icon('radio')}</span><span class="grow"><span class="row-title">Connection</span>` +
    `<span class="row-meta">${esc(connectionText())}</span></span>${icon('chevron-right')}</button></div>` +
    (st.state !== 'connected' && data.obs.enabled ? `<p class="small muted settings-note">${esc(st.detail)}</p>` : '') +
    `<div class="row-actions"><button type="button" class="button-surface" data-test>${icon('refresh-cw')} Test connection</button></div>`;
  const list = card.querySelector('[data-profiles]');
  Object.entries(data.profiles).forEach(([key, p]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'list-row settings-row';
    b.dataset.profile = key;
    const scene = p.scene ? `OBS scene “${p.scene}”` : 'no OBS scene set — OBS stays as it is';
    const where = p.zone ? 'camera zone kept empty' : 'no camera';
    b.innerHTML = `${zoneThumb(p.zone)}<span class="grow"><span class="row-title">${esc(p.label)}</span>` +
      `<span class="row-meta">${esc(scene)} · ${esc(where)}</span></span>${icon('chevron-right')}`;
    b.addEventListener('click', () => editProfile(key, p));
    list.appendChild(b);
  });
  card.querySelector('[data-conn]').addEventListener('click', editConnection);
  card.querySelector('[data-test]').addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    try {
      data = await api('/api/settings/obs/test', { method: 'POST' });
      toast(data.obs_state.state === 'connected' ? data.obs_state.detail : 'OBS is not reachable', data.obs_state.state === 'connected' ? 'info' : 'error');
    } catch (err) { toast(err.message, 'error'); }
    render();
  });
  return card;
}

function connectionText() {
  const o = data.obs;
  return `${o.enabled ? 'Scene switching on' : 'Scene switching off'} · ${o.host}:${o.port} · ${o.password_set ? 'password set' : 'no password'}`;
}

async function save(patch) {
  try {
    data = await api('/api/settings', { method: 'PUT', body: patch });
    render();
    toast('Settings saved');
  } catch (e) { toast(e.message, 'error'); }
}

async function editProfile(key, p) {
  const scenes = [...new Set([...(data.scenes || []), p.scene].filter(Boolean))];
  const fields = [
    scenes.length
      ? { name: 'scene', label: 'OBS scene', type: 'select', value: p.scene,
        options: [{ value: '', label: '— none: leave OBS as it is —' }, ...scenes.map((s) => ({ value: s, label: s }))],
        hint: data.scenes.length ? 'The scenes OBS has now.' : 'Connect OBS to pick from its scenes.' }
      : { name: 'scene', label: 'OBS scene', value: p.scene, placeholder: 'The scene name, exactly as in OBS', hint: 'OBS is not connected, so type the name.' },
  ];
  if (p.zone) {
    ['left', 'top', 'right', 'bottom'].forEach((side, i) => fields.push({ name: side, label: `Camera zone ${side} (0–1)`, type: 'number', value: p.zone[i] }));
  }
  const v = await formDialog({ title: p.label, fields });
  if (!v) return;
  const entry = { scene: v.scene };
  if (p.zone) entry.zone = ['left', 'top', 'right', 'bottom'].map((s) => Number(v[s]));
  await save({ profiles: { [key]: entry } });
}

async function editConnection() {
  const o = data.obs;
  const v = await formDialog({
    title: 'OBS connection',
    fields: [
      { name: 'enabled', label: 'Scene switching', type: 'select', value: o.enabled ? '1' : '0', options: [{ value: '1', label: 'On' }, { value: '0', label: 'Off' }] },
      { name: 'host', label: 'Host', value: o.host, required: true },
      { name: 'port', label: 'Port', type: 'number', value: o.port, required: true, hint: 'OBS → Tools → WebSocket Server Settings (default 4455).' },
      { name: 'password', label: 'Password', type: 'password', value: '', placeholder: o.password_set ? 'Leave empty to keep the current one' : 'Only if OBS asks for one' },
    ],
  });
  if (!v) return;
  const obs = { enabled: v.enabled === '1', host: v.host.trim(), port: Number(v.port) };
  if (v.password) obs.password = v.password;
  await save({ obs });
}

function readerCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.innerHTML = `<div class="card-head"><h3 class="card-title">${icon('message-square')} Zoom chat reader</h3></div>`;
  const line = document.createElement('div');
  line.className = 'switch-line';
  const sw = switchEl(data.reader.enabled, {
    label: 'Start the reader when a session goes live',
    onToggle: (next, btn) => { setSwitch(btn, next); save({ reader: { enabled: next } }); },
  });
  line.append(sw, Object.assign(document.createElement('span'), { textContent: 'Start the reader when a session goes live' }));
  card.appendChild(line);
  const note = document.createElement('p');
  note.className = 'small muted settings-note';
  note.textContent = `It reads the popped-out Zoom window titled “${data.reader.window_title}” every ${data.reader.poll_ms} ms.`;
  card.appendChild(note);
  return card;
}

function creditsCard() {
  const credits = document.createElement('div');
  credits.className = 'card credits';
  credits.innerHTML = `<div class="card-head"><h3 class="card-title">${icon('file-text')} Credits</h3></div>` +
    `<ul class="credit-list">${CREDITS.map(([name, url, what]) =>
      `<li><a href="${url}" target="_blank" rel="noopener">${name}</a> — ${what}</li>`).join('')}</ul>`;
  return credits;
}
