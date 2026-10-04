// Settings — everything common to every presentation, in five sections (#110):
// Appearance (light/dark on every device, this device's text size), Stage
// defaults (the theme and lettering new sessions start from, and the stage
// library of fonts and themes), Music (the Spotify account and the default
// fades), Live tools (OBS, the chat reader, the Stream Deck buttons, the phone
// remote) and About (build, credits). Each section has a deep link,
// /#settings/<section> (the presenter's music chip opens /#settings/music).

import { icon } from '/static/_vendored/icons/icons.js';
import { switchEl, setSwitch } from '/static/_vendored/switch/switch.js';
import { bindTextSize } from '/static/_vendored/text-size/text-size.js';
import { buildReadoutText } from '/static/_vendored/page-foot/page-foot.js';
import { APP, api, esc, pageHead, setStatus, toast, currentAppearance, onAppearance, setAppearance } from '/static/js/ui.js';
import { formDialog } from '/static/js/dialogs.js';
import { fontEditorHtml, letteringLabel, themeOptions, wireFontEditor } from '/static/js/font-editor.js';
import { words } from '/static/js/stage-words.js';

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

export const SECTIONS = [
  ['appearance', 'sun', 'Appearance'],
  ['stage', 'palette', 'Stage defaults'],
  ['music', 'music', 'Music'],
  ['live', 'plug', 'Live tools'],
  ['about', 'book-open', 'About'],
];
const SPOTIFY_CHIP = {
  ok: ['ok', 'Connected'],
  not_configured: ['warn', 'Not set up'],
  token_expired: ['bad', 'Login expired'],
  no_device: ['warn', 'Not open on this PC'],
  not_premium: ['bad', 'Premium needed'],
  unknown: ['', 'Unknown'],
};
const LOGIN_POLL_MS = 1500;

let root;
let head;
let data = null;
let defs = null; // GET /api/settings/defaults: the defaults and the stage library
let spotify = null; // GET /api/settings/spotify (null until it answers)
let build = null; // GET /api/version, once
let wanted = null; // the section a deep link asked for, until it is drawn
let loginTimer = null;

export async function mount(el) {
  root = el;
  head = pageHead({ glyph: 'settings', title: 'Settings', status: 'Shared by every session', settings: false });
  await load();
}

export function show() { load(); }

/** A deep link, /#settings/<section>: the next drawing of the page scrolls to that section. */
export function focusSection(name) {
  wanted = SECTIONS.some(([k]) => k === name) ? name : null;
}

function scrollToWanted() {
  const el = wanted && root ? root.querySelector(`[data-section="${wanted}"]`) : null;
  if (!el) return;
  wanted = null;
  // clear of a tab bar floating at the top (a narrow window); the rail and a bottom bar leave the top free
  const nav = document.querySelector('.tabs').getBoundingClientRect();
  const clear = (nav.top < 40 && nav.bottom < window.innerHeight / 3 ? nav.bottom : 0) + 12;
  window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - clear });
}

async function load() {
  try {
    [data, defs] = await Promise.all([api('/api/settings'), api('/api/settings/defaults')]);
  } catch (e) {
    toast(e.message, 'error');
    return;
  }
  render();
  loadSpotify();
  if (!build) {
    api('/api/version').then((v) => { build = v; paintAbout(); }).catch(() => { build = { failed: true }; paintAbout(); });
  }
}

function zoneThumb(zone) {
  const z = zone ? `<span style="left:${zone[0] * 100}%;top:${zone[1] * 100}%;width:${(zone[2] - zone[0]) * 100}%;height:${(zone[3] - zone[1]) * 100}%"></span>` : '';
  return `<span class="zone-thumb" aria-hidden="true">${z}</span>`;
}

function section(key, cards) {
  const [, glyph, title] = SECTIONS.find(([k]) => k === key);
  const el = document.createElement('section');
  el.className = 'settings-section';
  el.dataset.section = key;
  el.id = `settings-${key}`;
  el.setAttribute('aria-labelledby', `settings-${key}-title`);
  el.innerHTML = `<h2 class="settings-section-title" id="settings-${key}-title">${icon(glyph)}${esc(title)}</h2>`;
  cards.forEach((c) => el.appendChild(c));
  return el;
}

function render() {
  root.innerHTML = '';
  root.appendChild(head);
  setStatus(head, 'Shared by every session');
  root.appendChild(section('appearance', [appearanceCard(), textSizeCard()]));
  root.appendChild(section('stage', [themeCard(), letteringCard(), libraryCard()]));
  root.appendChild(section('music', [spotifyCard(), musicDefaultsCard()]));
  root.appendChild(section('live', [obsCard(), readerCard(), stageClickCard(), streamDeckCard(), remoteCard()]));
  root.appendChild(section('about', [aboutCard(), creditsCard()]));
  scrollToWanted();
}

function remoteCard() {
  const r = data.remote;
  const card = document.createElement('div');
  card.className = 'card settings-card';
  const state = r.bind_loopback ? ['warn', 'unavailable'] : !r.https ? ['warn', 'needs HTTPS'] : r.enabled ? ['ok', 'on'] : ['', 'off'];
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('smartphone')} Phone remote</h3><span class="chip ${state[0]}">${state[1]}</span></div>` +
    '<p class="small muted settings-note">Next, previous, capture, timer and blackout from your phone, over Tailscale. Open the link once on the phone: it pairs that phone. ' +
    'Other devices never get in without it; this PC never needs it.</p>';
  if (r.bind_loopback) {
    card.insertAdjacentHTML('beforeend', '<p class="small settings-note">This PC only accepts connections from itself (<code>"host": "127.0.0.1"</code> in <code>config.json</code>) — a phone can\'t reach it. Set <code>host</code> to <code>"0.0.0.0"</code> and restart the tray.</p>');
    return card;
  }
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
      { name: 'port', label: 'Port', type: 'number', value: o.port, required: true, hint: 'In OBS, Tools, then WebSocket Server Settings (default 4455).' },
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

/** What a click on the stage window does (#191): the next item, only when switched on. */
function stageClickCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.innerHTML = `<div class="card-head"><h3 class="card-title">${icon('monitor')} Clicking the stage</h3></div>`;
  const row = (key, label) => {
    const line = document.createElement('div');
    line.className = 'switch-line';
    line.append(
      switchEl(data.stage_click[key], { label, onToggle: (next, btn) => { setSwitch(btn, next); save({ stage_click: { [key]: next } }); } }),
      Object.assign(document.createElement('span'), { textContent: label }),
    );
    card.appendChild(line);
  };
  row('advance', 'A click on the stage goes to the next item');
  row('restore_camera', 'A click on the stage restores the item’s camera layout');
  const note = document.createElement('p');
  note.className = 'small muted settings-note';
  note.textContent = 'Going to the next item is off by default, so a stray click cannot move the presentation on; the arrow keys and a clicker always go forward and back, and a double-click is full screen. With it off, a click re-applies the current item’s camera layout in OBS (after you switched OBS by hand); it does nothing when the item has no layout or OBS is not connected.';
  card.appendChild(note);
  return card;
}

const APPEARANCE_CHOICES =[['system', 'System'], ['light', 'Light'], ['dark', 'Dark']];

/** Light/dark for the app, the presenter and the phone remote — one value for every device (#92). */
function appearanceCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('sun')} Light and dark</h3></div>` +
    '<nav class="range-tabs" id="appearanceControl" aria-label="Appearance">' +
    APPEARANCE_CHOICES.map(([v, l]) => `<button type="button" class="range-tab" data-appearance="${v}">${l}</button>`).join('') +
    '</nav>' +
    '<p class="small muted settings-note">For the app, the presenter and the phone remote, on every device at once — the sun/moon buttons set it too. System follows each device\'s own setting. The stage keeps the session\'s look.</p>';
  card.querySelector('#appearanceControl').addEventListener('click', (e) => {
    const b = e.target.closest('[data-appearance]');
    if (b) setAppearance(b.dataset.appearance);
  });
  paintAppearanceControl(currentAppearance(), card);
  return card;
}

/** Mark the chosen appearance (also when it changes on another page or device). */
function paintAppearanceControl(value, scope = root) {
  if (!scope) return;
  scope.querySelectorAll('#appearanceControl [data-appearance]').forEach((b) => {
    const on = b.dataset.appearance === value;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
}
onAppearance((value) => paintAppearanceControl(value));

/** The zoom-lock escape (design.md Layout "Text size"): vendored text-size control, per device. */
function textSizeCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('type')} Text size</h3></div>` +
    '<nav class="range-tabs" id="textSizeControl" aria-label="Text size">' +
    '<button type="button" class="range-tab" data-textsize="small">Small</button>' +
    '<button type="button" class="range-tab" data-textsize="default">Default</button>' +
    '<button type="button" class="range-tab" data-textsize="large">Large</button></nav>' +
    '<p class="small muted settings-note">For this app on this device; the stage keeps its own sizes.</p>';
  bindTextSize(card.querySelector('#textSizeControl'), APP);
  return card;
}

// ---- Stage defaults (#110) -----------------------------------------------------

async function saveDefaults(patch, message = 'Default saved — new sessions start from it') {
  try {
    defs = await api('/api/settings/defaults', { method: 'PUT', body: patch });
    toast(message);
    return true;
  } catch (e) { toast(e.message, 'error'); return false; }
}

function themeCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('presentation')} Stage theme</h3></div>` +
    `<div class="font-rows"><label class="font-row"><span class="small">New sessions</span>` +
    `<select class="select-native" aria-label="Default stage theme" data-default-theme>${themeOptions(defs.library.themes, defs.stage.theme)}</select></label></div>` +
    '<p class="small muted settings-note">The stage’s colours and layout. A new session copies the default; an existing session keeps its own (on the Sessions tab, in Session settings under Stage look, where “Reset to default” takes the current one).</p>';
  card.querySelector('[data-default-theme]').addEventListener('change', async (e) => {
    if (await saveDefaults({ stage: { theme: e.target.value } })) render();
  });
  return card;
}

/** The default lettering: the session editor on a sample of its own (its CSS never reaches a stage). */
function letteringCard() {
  const font = defs.stage.font;
  const card = document.createElement('div');
  card.className = 'card settings-card defaults-font-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('type')} Stage lettering and timer colours</h3>` +
    `<span class="card-head-meta">${esc(letteringLabel(font))}</span></div>` +
    '<p class="small muted settings-note">The title font and the text font every new session starts from, which one each kind of text uses, and the colours of the timer (under Timer colours). A session can change its own; an item can set exceptions in the Plan tab.</p>' +
    fontEditorHtml(font, { hint: words('en').chat_hint, sampleClass: 'defaults-sample', library: defs.library.fonts });
  linkDefaultsFont(font);
  wireFontEditor(card, {
    current: () => defs.stage.font,
    save: (next) => saveDefaults({ stage: { font: next } }),
    redraw: render,
    pickFile: async () => {
      try {
        const picked = await api('/api/pick', { method: 'POST', body: { kind: 'font' } });
        if (!picked.path) return '';
        defs = await api('/api/settings/library/font', { method: 'POST', body: { path: picked.path } });
        return defs.added.path; // the library's copy, never the file where it was picked
      } catch (e) { toast(e.message, 'error'); return ''; }
    },
  });
  return card;
}

/** The default lettering's CSS for the sample (fetched again when it changes). */
function linkDefaultsFont(font) {
  const href = `/api/settings/defaults/font.css?v=${encodeURIComponent(JSON.stringify(font || {}))}`;
  let link = document.querySelector('link[data-defaults-font]');
  if (link && link.getAttribute('href') === href) return;
  if (!link) {
    link = document.createElement('link');
    link.rel = 'stylesheet';
    link.dataset.defaultsFont = '';
    document.head.appendChild(link);
  }
  link.href = href;
}

function libraryCard() {
  const lib = defs.library;
  const own = lib.themes.filter((t) => t.source === 'library');
  const card = document.createElement('div');
  card.className = 'card settings-card';
  const row = (glyph, title, meta) => `<div class="list-row library-row"><span class="settings-ico">${icon(glyph)}</span>` +
    `<span class="grow"><span class="row-title">${esc(title)}</span><span class="row-meta">${esc(meta)}</span></span></div>`;
  const rows = [...lib.fonts.map((f) => row('type', f.name, 'Font')), ...own.map((t) => row('palette', t.label, 'Stage theme'))];
  const n = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('folder')} Stage library</h3>` +
    `<span class="card-head-meta">${n(lib.fonts.length, 'font')} · ${n(own.length, 'theme')}</span></div>` +
    (rows.length ? `<div class="list">${rows.join('')}</div>`
      : '<p class="small muted settings-note">Nothing here yet. Add a font file or a stage theme to offer it to every session.</p>') +
    `<p class="small muted settings-note">Your own files, kept next to your sessions and never in the app’s folder: <code class="library-path">${esc(lib.path)}</code></p>` +
    `<div class="row-actions settings-actions"><button type="button" class="button-surface" data-add="font">${icon('plus')} Add font…</button>` +
    `<button type="button" class="button-surface" data-add="theme">${icon('plus')} Add stage theme…</button></div>`;
  card.querySelectorAll('[data-add]').forEach((b) => b.addEventListener('click', async () => {
    const kind = b.dataset.add;
    try {
      const picked = await api('/api/pick', { method: 'POST', body: { kind } });
      if (!picked.path) return;
      defs = await api(`/api/settings/library/${kind}`, { method: 'POST', body: { path: picked.path } });
      toast(`${defs.added.name} is in the library`);
      render();
    } catch (e) { toast(e.message, 'error'); }
  }));
  return card;
}

// ---- Music (#110) ----------------------------------------------------------------

async function loadSpotify(fresh = false) {
  try {
    spotify = await api(fresh ? '/api/settings/spotify/check' : '/api/settings/spotify', fresh ? { method: 'POST' } : {});
  } catch (e) {
    spotify = { state: 'unknown', detail: e.message, device: '', checked_at: null, client_id_set: true, logged_in: false, login: { state: 'idle' } };
  }
  paintSpotify();
  pollLogin();
}

function paintSpotify() {
  const old = root && root.querySelector('[data-spotify-card]');
  if (old) old.replaceWith(spotifyCard());
}

/** While the browser login is open: ask again every moment until it is saved or failed. */
function pollLogin() {
  clearTimeout(loginTimer);
  if (!spotify || spotify.login.state !== 'waiting') return;
  loginTimer = setTimeout(async () => {
    try { spotify = await api('/api/settings/spotify'); } catch (e) { /* asked again next time */ }
    if (spotify.login.state === 'done') {
      toast('Spotify connected');
      loadSpotify(true);
      return;
    }
    if (spotify.login.state === 'failed') toast(spotify.login.detail, 'error');
    paintSpotify();
    pollLogin();
  }, LOGIN_POLL_MS);
}

function whenText(ts) {
  if (!ts) return 'Not checked yet';
  return new Date(ts * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function spotifyCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.dataset.spotifyCard = '';
  const sp = spotify;
  const [kind, label] = sp ? (SPOTIFY_CHIP[sp.state] || ['bad', sp.state]) : ['', 'Checking…'];
  const waiting = !!sp && sp.login.state === 'waiting';
  const fact = (name, value) => `<div class="list-row deck-row"><span class="deck-label">${name}</span><span class="grow small">${esc(value)}</span></div>`;
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('music')} Spotify</h3>` +
    `<span class="chip ${kind}" data-spotify-state><span class="dot"></span>${esc(label)}</span></div>` +
    (sp ? `<div class="list">${fact('Account', sp.detail)}${fact('Plays on', sp.device || 'The Spotify desktop app on this PC')}${fact('Last check', whenText(sp.checked_at))}</div>` : '') +
    (waiting ? `<p class="status-line unknown" data-login-line>${icon('refresh-cw')} ${esc(sp.login.detail)}. This card updates by itself.</p>` : '') +
    (sp && sp.login.state === 'failed' ? `<p class="status-line warn" data-login-line>${icon('triangle-alert')} ${esc(sp.login.detail)}</p>` : '') +
    (sp && !sp.client_id_set ? '<p class="small muted settings-note">First create the Spotify developer app and put its client id in <code>.env</code> (see Spotify setup in the README).</p>' : '') +
    '<p class="small muted settings-note">Music plays on the Spotify desktop app of this PC (a Premium account). Connecting opens Spotify’s login in the browser; the login is saved on this PC only.</p>' +
    `<div class="row-actions settings-actions"><button type="button" class="button-tint" data-connect${!sp || !sp.client_id_set || waiting ? ' disabled' : ''}>${icon('link')} ${sp && sp.logged_in ? 'Reconnect Spotify' : 'Connect Spotify'}</button>` +
    `<button type="button" class="button-surface" data-check${sp ? '' : ' disabled'}>${icon('refresh-cw')} Check now</button></div>`;
  card.querySelector('[data-connect]').addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    try {
      spotify = await api('/api/settings/spotify/connect', { method: 'POST' });
      toast('Log in to Spotify in the browser window that opened');
    } catch (err) { toast(err.message, 'error'); }
    paintSpotify();
    pollLogin();
  });
  card.querySelector('[data-check]').addEventListener('click', (e) => {
    e.currentTarget.disabled = true;
    loadSpotify(true);
  });
  return card;
}

function musicDefaultsCard() {
  const m = defs.music;
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('volume-2')} New music items</h3></div>` +
    `<div class="list"><button type="button" class="list-row settings-row" data-fades>` +
    `<span class="settings-ico">${icon('sliders-horizontal')}</span><span class="grow"><span class="row-title">Fades</span>` +
    `<span class="row-meta">In ${m.fade_in_s} s · out ${m.fade_out_s} s</span></span>${icon('chevron-right')}</button></div>` +
    '<p class="small muted settings-note">What an item’s music starts with when you switch it on in the Plan tab; each item can change its own.</p>';
  card.querySelector('[data-fades]').addEventListener('click', async () => {
    const v = await formDialog({
      title: 'Fades for new music',
      fields: [
        { name: 'fade_in_s', label: 'Fade in (s)', type: 'number', value: m.fade_in_s, step: '0.5', min: 0, max: 60, required: true },
        { name: 'fade_out_s', label: 'Fade out (s)', type: 'number', value: m.fade_out_s, step: '0.5', min: 0, max: 60, required: true },
      ],
    });
    if (!v) return;
    if (await saveDefaults({ music: { fade_in_s: Number(v.fade_in_s), fade_out_s: Number(v.fade_out_s) } }, 'Fades saved for new music items')) render();
  });
  return card;
}

// ---- About -----------------------------------------------------------------------

function aboutCard() {
  const card = document.createElement('div');
  card.className = 'card settings-card';
  card.innerHTML =
    `<div class="card-head"><h3 class="card-title">${icon('monitor')} This app</h3></div>` +
    `<p class="small settings-note" data-build>${esc(buildLine())}</p>`;
  return card;
}

function buildLine() {
  if (!build) return 'Build: …';
  if (build.failed) return 'Build: unknown';
  return buildReadoutText(build.git_sha, build.captured_at);
}

function paintAbout() {
  const line = root && root.querySelector('[data-build]');
  if (line) line.textContent = buildLine();
}

function creditsCard() {
  const credits = document.createElement('div');
  credits.className = 'card credits';
  credits.innerHTML = `<div class="card-head"><h3 class="card-title">${icon('file-text')} Credits</h3></div>` +
    `<ul class="credit-list">${CREDITS.map(([name, url, what]) =>
      `<li><a href="${url}" target="_blank" rel="noopener">${name}</a> — ${what}</li>`).join('')}</ul>`;
  return credits;
}
