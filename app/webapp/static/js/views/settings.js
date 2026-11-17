// Settings — everything common to every presentation, in five sections of inset groups (a group
// label over a card of one-line rows; a row that needs room opens a sheet): Appearance (light/dark on
// every device, this device's text size), Stage defaults (the theme and lettering new sessions start
// from, and the stage library of fonts and themes), Music (the Spotify account and the default
// fades), Live tools (OBS, the chat reader, the Copilot overlay and recordings, the Stream Deck
// buttons, the phone remote) and About (build, credits). Each section has a deep link,
// /#settings/<section> (the presenter's music chip opens /#settings/music).

import { icon } from '/static/_vendored/icons/icons.js';
import { bindTextSize } from '/static/_vendored/text-size/text-size.js';
import { buildReadoutText } from '/static/_vendored/page-foot/page-foot.js';
import { APP, api, esc, plural, toast, currentAppearance, onAppearance, setAppearance } from '/static/js/ui.js';
import { formDialog, sheetDialog } from '/static/js/dialogs.js';
import { fontEditorHtml, letteringLabel, themeOptions, wireFontEditor } from '/static/js/font-editor.js';
import { words } from '/static/js/stage-words.js';
import { overlayGroup } from '/static/js/views/overlay-card.js';
import { recordingGroup } from '/static/js/views/recording-card.js';
import { configuredSourceName } from '/static/js/chat-source.js';
import { actionRow, controlRow, factRow, numberRow, selectRow, setRowValue, settingsGroup, toggleRow, valueRow } from '/static/js/views/settings-rows.js';

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
// a chip kind's tone as a row value: only an exception is coloured
const TONE = { warn: 'attention', bad: 'danger' };

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
  no_device: ['warn', 'Desktop app not open'],
  not_playing: ['warn', 'Not playing'],
  restricted: ['warn', 'Command refused'],
  not_premium: ['bad', 'Premium needed'],
  unknown: ['', 'Unknown'],
};
const LOGIN_POLL_MS = 1500;

let root;
let data = null;
let defs = null; // GET /api/settings/defaults: the defaults and the stage library
let spotify = null; // GET /api/settings/spotify (null until it answers)
let build = null; // GET /api/version, once
let wanted = null; // the section a deep link asked for, until it is drawn
let loginTimer = null;
let spotifySheet = null; // the open Spotify sheet, redrawn as the login moves
let remoteSheet = null; // the open phone remote sheet, redrawn when its link changes

export async function mount(el) {
  root = el;
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
  // the page scrolls itself, under its sticky header
  const page = root.closest('dialog');
  const clear = page.querySelector('.settings-page-head').offsetHeight + 12;
  page.scrollTo({ top: el.getBoundingClientRect().top - page.getBoundingClientRect().top + page.scrollTop - clear });
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

/** A section: its groups under one deep-link anchor. */
function section(key, groups) {
  const [, , title] = SECTIONS.find(([k]) => k === key);
  const el = document.createElement('section');
  el.className = 'settings-section';
  el.dataset.section = key;
  el.id = `settings-${key}`;
  el.setAttribute('aria-label', title);
  groups.forEach((g) => el.appendChild(g));
  return el;
}

function render() {
  root.innerHTML = '';
  root.appendChild(section('appearance', [appearanceGroup()]));
  root.appendChild(section('stage', [stageGroup()]));
  root.appendChild(section('music', [musicGroup()]));
  root.appendChild(section('live', [obsGroup(), readerGroup(), stageClickGroup(), overlayGroup(), recordingGroup(), remoteGroup()]));
  root.appendChild(section('about', [aboutGroup()]));
  scrollToWanted();
}

/** The value of the row that carries `key`, repainted in place (a sheet changed what it shows). */
function paintValue(key, text, tone) {
  const v = root && root.querySelector(`.settings-row-value[data-value="${key}"]`);
  if (v) setRowValue(v.closest('.settings-row'), text, tone);
}

// ---- Live tools -----------------------------------------------------------

function remoteState() {
  const r = data.remote;
  if (r.bind_loopback) return ['unavailable', 'attention'];
  if (!r.https) return ['needs HTTPS', 'attention'];
  return r.enabled ? ['on', undefined] : ['off', undefined];
}

function remoteGroup() {
  const [state, tone] = remoteState();
  const remote = valueRow({ glyph: 'smartphone', label: 'Phone remote', value: state, tone, valueKey: 'remote', track: 'settings.remote-open', onOpen: openRemote });
  const deck = valueRow({ glyph: 'keyboard', label: 'Stream Deck buttons', value: '', valueKey: 'deck', track: 'settings.deck-open', onOpen: openDeck });
  loadActions().then((list) => paintValue('deck', plural(deckRows(list).length, 'button'))).catch(() => { /* the sheet says why */ });
  return settingsGroup('remote', 'Remote control', [remote, deck],
    'A phone gets next, previous, capture, timer and blackout over Tailscale; a Stream Deck button sends one URL.').frag;
}

function openRemote() {
  const sheet = sheetDialog({ title: 'Phone remote', track: 'dialog.settings-remote', onClose: () => { remoteSheet = null; } });
  remoteSheet = sheet;
  drawRemote();
}

function drawRemote() {
  if (!remoteSheet) return;
  const r = data.remote;
  const body = remoteSheet.body;
  body.classList.add('settings-sheet-stack');
  body.innerHTML =
    '<p class="small muted settings-note">Next, previous, capture, timer and blackout from your phone, over Tailscale. Open the link once on the phone: it pairs that phone. ' +
    'Other devices never get in without it; this PC never needs it.</p>';
  if (r.bind_loopback) {
    body.insertAdjacentHTML('beforeend', '<p class="small settings-note">This PC only accepts connections from itself (<code>"host": "127.0.0.1"</code> in <code>config.json</code>) — a phone can\'t reach it. Set <code>host</code> to <code>"0.0.0.0"</code> and restart the tray.</p>');
    return;
  }
  if (!r.https) {
    body.insertAdjacentHTML('beforeend', '<p class="small settings-note">HTTPS is not set up on this PC yet: run <code>scripts/gen_tailscale_cert.py</code> (a Tailscale certificate), then restart the tray.</p>');
    return;
  }
  const row = document.createElement('div');
  row.className = 'row-actions remote-actions';
  if (r.enabled && r.link) {
    body.insertAdjacentHTML('beforeend', `<div class="list-row deck-row"><span class="deck-label">Pairing link</span><code class="grow" data-link>${esc(r.link.replace(/token=.*/, 'token=•••'))}</code></div>`);
    row.innerHTML = `<button type="button" class="button-surface" data-copy data-track="settings.remote-copy">${icon('copy')} Copy the link</button>` +
      `<button type="button" class="button-surface" data-new data-track="settings.remote-new">${icon('refresh-cw')} New link (unpairs phones)</button>` +
      `<button type="button" class="button-surface" data-off data-track="settings.remote-off">${icon('x')} Turn off</button>`;
  } else {
    row.innerHTML = `<button type="button" class="button-tint" data-new data-track="settings.remote-make">${icon('smartphone')} Make the phone link</button>`;
  }
  body.appendChild(row);
  const on = (sel, fn) => { const b = row.querySelector(sel); if (b) b.addEventListener('click', fn); };
  const changed = (next, message) => {
    data = next;
    render();
    drawRemote();
    toast(message);
  };
  on('[data-copy]', async () => {
    try { await navigator.clipboard.writeText(r.link); toast('Link copied — open it on the phone (send it to yourself)'); } catch (e) { toast('Could not copy', 'error'); }
  });
  on('[data-new]', async () => {
    try { changed(await api('/api/settings/remote/token', { method: 'POST' }), r.enabled ? 'New link made — phones paired before need it again' : 'Phone link made'); } catch (e) { toast(e.message, 'error'); }
  });
  on('[data-off]', async () => {
    try { changed(await api('/api/settings/remote/token', { method: 'DELETE' }), 'Phone remote off'); } catch (e) { toast(e.message, 'error'); }
  });
}

let actions = null;
const loadActions = () => (actions ? Promise.resolve(actions) : api('/api/actions').then((r) => { actions = r.actions; return actions; }));

/** The Stream Deck buttons: [label, path] for each action the deck can call. */
function deckRows(list) {
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
  return rows;
}

function openDeck() {
  const { body } = sheetDialog({ title: 'Stream Deck buttons', track: 'dialog.settings-deck' });
  const base = data.remote.base_url;
  body.classList.add('settings-sheet-stack');
  body.innerHTML =
    `<p class="small muted settings-note">Each button sends a POST to <code>${esc(base)}</code> plus the path below — the same pattern as home-automation's action alias, so the fleet Stream Deck plugin's “Call Action” key works with it. No token is needed from this PC (the fleet plugin's <code>FACILITATION_SUITE_BASE_URL</code> is this address).</p>` +
    `<div class="list deck-list" data-deck><p class="muted small">Loading…</p></div>`;
  loadActions().then((list) => {
    const box = body.querySelector('[data-deck]');
    box.innerHTML = deckRows(list).map(([label, path]) =>
      `<div class="list-row deck-row"><span class="deck-label">${esc(label)}</span><code class="grow">${esc(path)}</code>` +
      `<button type="button" class="button-surface" data-copy="${esc(base + path)}" data-track="settings.remote-copy-url">${icon('copy')} Copy</button></div>`).join('');
    box.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-copy]');
      if (!b) return;
      try { await navigator.clipboard.writeText(b.dataset.copy); toast('URL copied'); } catch (err) { toast('Could not copy', 'error'); }
    });
  }).catch((e) => { body.querySelector('[data-deck]').textContent = e.message; });
}

function obsGroup() {
  const st = data.obs_state;
  const [kind, label] = OBS_CHIP[st.state] || ['bad', st.state];
  const connection = valueRow({ glyph: 'radio', label: 'Connection', value: label, tone: TONE[kind], valueKey: 'obs-state', track: 'settings.obs-connection', onOpen: editConnection });
  connection.querySelector('.settings-row-value').title = `${label} — ${connectionText()}`;
  const rows = [connection];
  Object.entries(data.profiles).forEach(([key, p]) => {
    const row = valueRow({ glyph: 'video', label: p.label, value: p.scene || 'No scene set', track: 'settings.obs-profile', onOpen: () => editProfile(key, p) });
    row.dataset.profile = key;
    rows.push(row);
  });
  rows.push(actionRow({ glyph: 'refresh-cw', label: 'Test connection', track: 'settings.obs-test', onTap: async (e) => {
    e.currentTarget.disabled = true;
    try {
      data = await api('/api/settings/obs/test', { method: 'POST' });
      toast(data.obs_state.connected ? data.obs_state.detail : 'OBS is not reachable', data.obs_state.connected ? 'info' : 'error');
    } catch (err) { toast(err.message, 'error'); }
    render();
  } }));
  const trouble = !st.connected && data.obs.enabled ? ` ${st.detail}` : '';
  return settingsGroup('obs', 'OBS', rows,
    `Each plan item has a profile: the app switches OBS to that profile’s scene when the item goes on stage, and keeps the profile’s camera zone empty.${trouble}`).frag;
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

function readerGroup() {
  const source = data.reader.source;
  const name = configuredSourceName(source);
  const what = {
    teams: `the Teams chat open in a window titled “… ${data.reader.teams_title}”`,
    meet: `the chat panel of the Meet tab in a window titled “${data.reader.meet_title} …”`,
  }[source] || `the popped-out Zoom window titled “${data.reader.window_title}”`;
  const row = toggleRow({ glyph: 'message-square', label: `${name} chat reader`, hint: 'Starts when a session goes live', on: data.reader.enabled,
    track: 'settings.reader-enabled', onToggle: (next) => save({ reader: { enabled: next } }) });
  return settingsGroup('reader', 'Chat reader', [row],
    `It reads ${what} every ${data.reader.poll_ms} ms. Choose Zoom, Teams or Meet on the presenter, under the chat.`).frag;
}

/** What a click on the stage window does: the next item, only when switched on. */
function stageClickGroup() {
  const toggle = (key) => (next) => save({ stage_click: { [key]: next } });
  return settingsGroup('stage-click', 'Clicking the stage', [
    toggleRow({ glyph: 'arrow-right', label: 'Next item on click', on: data.stage_click.advance, track: 'settings.stage-click-advance', onToggle: toggle('advance') }),
    toggleRow({ glyph: 'camera', label: 'Camera layout on click', on: data.stage_click.restore_camera, track: 'settings.stage-click-camera', onToggle: toggle('restore_camera') }),
  ], 'Going to the next item is off by default, so a stray click cannot move the presentation on; the arrow keys and a clicker always go forward and back, and a double-click is full screen. With it off, a click re-applies the current item’s camera layout in OBS.').frag;
}

// ---- Appearance -------------------------------------------------------------

const APPEARANCE_CHOICES = [['system', 'System'], ['light', 'Light'], ['dark', 'Dark']];

/** Light/dark for the app, the presenter and the phone remote — one value for every device; text size per device. */
function appearanceGroup() {
  const appearance = document.createElement('div');
  appearance.className = 'segmented';
  appearance.id = 'appearanceControl';
  appearance.setAttribute('role', 'group');
  appearance.setAttribute('aria-label', 'Appearance');
  appearance.innerHTML = APPEARANCE_CHOICES.map(([v, l]) => `<button type="button" class="segmented-item" aria-pressed="false" data-track="settings.appearance" data-appearance="${v}">${l}</button>`).join('');
  appearance.addEventListener('click', (e) => {
    const b = e.target.closest('[data-appearance]');
    if (b) setAppearance(b.dataset.appearance);
  });
  paintAppearanceControl(currentAppearance(), appearance);
  const size = document.createElement('div');
  size.className = 'segmented';
  size.id = 'textSizeControl';
  size.setAttribute('role', 'group');
  size.setAttribute('aria-label', 'Text size');
  size.innerHTML = '<button type="button" class="segmented-item" aria-pressed="false" data-textsize="small" data-track="settings.textsize">Small</button>' +
    '<button type="button" class="segmented-item" aria-pressed="true" data-textsize="default" data-track="settings.textsize">Default</button>' +
    '<button type="button" class="segmented-item" aria-pressed="false" data-textsize="large" data-track="settings.textsize">Large</button>';
  bindTextSize(size, APP);
  return settingsGroup('appearance', 'Appearance', [
    controlRow({ glyph: 'sun', label: 'Light and dark', control: appearance }),
    controlRow({ glyph: 'type', label: 'Text size', control: size }),
  ], 'Light and dark covers the app, the presenter and the phone remote on every device at once; System follows each device. Text size is for this device. The stage keeps the session’s look.').frag;
}

/** Mark the chosen appearance (also when it changes on another page or device). */
function paintAppearanceControl(value, scope = root) {
  if (!scope) return;
  scope.querySelectorAll('#appearanceControl [data-appearance]').forEach((b) => {
    b.setAttribute('aria-pressed', String(b.dataset.appearance === value));
  });
}
onAppearance((value) => paintAppearanceControl(value));

// ---- Stage defaults -----------------------------------------------------

async function saveDefaults(patch, message = 'Default saved — new sessions start from it') {
  try {
    defs = await api('/api/settings/defaults', { method: 'PUT', body: patch });
    toast(message);
    return true;
  } catch (e) { toast(e.message, 'error'); return false; }
}

const libraryText = () => {
  const own = defs.library.themes.filter((t) => t.source === 'library');
  return `${plural(defs.library.fonts.length, 'font')} · ${plural(own.length, 'colour set')}`;
};

function stageGroup() {
  const { row: theme, select } = selectRow({ glyph: 'presentation', label: 'Colour set', aria: 'Default colour set', track: 'settings.default-theme',
    optionsHtml: themeOptions(defs.library.themes, defs.stage.theme) });
  select.dataset.defaultTheme = '';
  select.addEventListener('change', async (e) => {
    if (await saveDefaults({ stage: { theme: e.target.value } })) render();
  });
  const lettering = valueRow({ glyph: 'type', label: 'Lettering and timers', value: letteringLabel(defs.stage.font), valueKey: 'lettering', track: 'settings.lettering-open', onOpen: openLettering });
  const library = valueRow({ glyph: 'folder', label: 'Stage library', value: libraryText(), valueKey: 'library', track: 'settings.library-open', onOpen: openLibrary });
  return settingsGroup('stage', 'Stage defaults', [theme, lettering, library],
    'A new session copies these; an existing session keeps its own (on the Sessions tab, in Session settings under Stage look, where “Reset to default” takes the current one).').frag;
}

/** The default lettering: the session editor on a sample of its own (its CSS never reaches a stage). */
function openLettering() {
  const { body } = sheetDialog({ title: 'Stage lettering and timer colours', track: 'dialog.settings-lettering', wide: true });
  body.classList.add('defaults-font-card');
  const draw = () => {
    const font = defs.stage.font;
    const top = body.scrollTop;
    body.innerHTML =
      '<p class="small muted settings-note">The title font and the text font every new session starts from, which one each kind of text uses, and the colours of the timer (under Timer colours). A session can change its own; an item can set exceptions in the Plan tab.</p>' +
      fontEditorHtml(font, { hint: words('en').chat_hint, sampleClass: 'defaults-sample', library: defs.library.fonts });
    linkDefaultsFont(font);
    wireFontEditor(body, {
      current: () => defs.stage.font,
      save: (next) => saveDefaults({ stage: { font: next } }),
      redraw: () => { paintValue('lettering', letteringLabel(defs.stage.font)); paintValue('library', libraryText()); draw(); },
      pickFile: async () => {
        try {
          const picked = await api('/api/pick', { method: 'POST', body: { kind: 'font' } });
          if (!picked.path) return '';
          defs = await api('/api/settings/library/font', { method: 'POST', body: { path: picked.path } });
          return defs.added.path; // the library's copy, never the file where it was picked
        } catch (e) { toast(e.message, 'error'); return ''; }
      },
    });
    body.scrollTop = top;
  };
  draw();
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

function openLibrary() {
  const { body } = sheetDialog({ title: 'Stage library', track: 'dialog.settings-library' });
  body.classList.add('settings-sheet-stack');
  const draw = () => {
    const lib = defs.library;
    const own = lib.themes.filter((t) => t.source === 'library');
    const row = (glyph, title, meta) => `<div class="list-row library-row">${icon(glyph, 'settings-row-icon')}` +
      `<span class="grow"><span class="row-title">${esc(title)}</span><span class="row-meta">${esc(meta)}</span></span></div>`;
    const rows = [...lib.fonts.map((f) => row('type', f.name, 'Font')), ...own.map((t) => row('palette', t.label, 'Colour set'))];
    body.innerHTML =
      (rows.length ? `<div class="list">${rows.join('')}</div>`
        : '<p class="small muted settings-note">Nothing here yet. Add a font file or a colour set to offer it to every session.</p>') +
      `<p class="small muted settings-note">Your own files, kept next to your sessions and never in the app’s folder: <code class="library-path">${esc(lib.path)}</code></p>` +
      `<div class="row-actions settings-actions"><button type="button" class="button-tint" data-add="font" data-track="settings.add-font" aria-haspopup="dialog">${icon('plus')} Add font…</button>` +
      `<button type="button" class="button-tint" data-add="theme" data-track="settings.add-theme" aria-haspopup="dialog">${icon('plus')} Add colour set…</button></div>`;
    body.querySelectorAll('[data-add]').forEach((b) => b.addEventListener('click', async () => {
      const kind = b.dataset.add;
      try {
        const picked = await api('/api/pick', { method: 'POST', body: { kind } });
        if (!picked.path) return;
        defs = await api(`/api/settings/library/${kind}`, { method: 'POST', body: { path: picked.path } });
        toast(`${defs.added.name} is in the library`);
        paintValue('library', libraryText());
        draw();
      } catch (e) { toast(e.message, 'error'); }
    }));
  };
  draw();
}

// ---- Music ----------------------------------------------------------------

async function loadSpotify(fresh = false) {
  try {
    spotify = await api(fresh ? '/api/settings/spotify/check' : '/api/settings/spotify', fresh ? { method: 'POST' } : {});
  } catch (e) {
    spotify = { state: 'unknown', detail: e.message, device: '', checked_at: null, client_id_set: true, logged_in: false, login: { waiting: false, done: false, failed: false, detail: '' } };
  }
  paintSpotify();
  pollLogin();
}

/** The Spotify state as the row's value and tone. */
function spotifyState() {
  const [kind, label] = spotify ? (SPOTIFY_CHIP[spotify.state] || ['bad', spotify.state]) : ['', 'Checking…'];
  return [label, TONE[kind]];
}

function paintSpotify() {
  const [label, tone] = spotifyState();
  paintValue('spotify', label, tone);
  drawSpotify();
}

/** While the browser login is open: ask again every moment until it is saved or failed. */
function pollLogin() {
  clearTimeout(loginTimer);
  if (!spotify || !spotify.login.waiting) return;
  loginTimer = setTimeout(async () => {
    try { spotify = await api('/api/settings/spotify'); } catch (e) { /* asked again next time */ }
    if (spotify.login.done) {
      toast('Spotify connected');
      loadSpotify(true);
      return;
    }
    if (spotify.login.failed) toast(spotify.login.detail, 'error');
    paintSpotify();
    pollLogin();
  }, LOGIN_POLL_MS);
}

function whenText(ts) {
  if (!ts) return 'Not checked yet';
  return new Date(ts * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function musicGroup() {
  const [label, tone] = spotifyState();
  const m = defs.music;
  const commit = (key) => async (n, input) => {
    if (!Number.isFinite(n) || n < 0 || n > 60) {
      toast('A fade is 0 to 60 seconds', 'error');
      input.value = defs.music[key];
      return;
    }
    await saveDefaults({ music: { [key]: n } }, 'Fades saved for new music items');
  };
  return settingsGroup('music', 'Music', [
    valueRow({ glyph: 'music', label: 'Spotify', value: label, tone, valueKey: 'spotify', track: 'settings.spotify-open', onOpen: openSpotify }),
    numberRow({ glyph: 'sliders-horizontal', label: 'Fade in', value: m.fade_in_s, unit: 's', min: 0, max: 60, step: '0.5', aria: 'Fade in for new music, in seconds', track: 'settings.music-fade-in', onCommit: commit('fade_in_s') }),
    numberRow({ glyph: 'sliders-horizontal', label: 'Fade out', value: m.fade_out_s, unit: 's', min: 0, max: 60, step: '0.5', aria: 'Fade out for new music, in seconds', track: 'settings.music-fade-out', onCommit: commit('fade_out_s') }),
  ], 'Music plays on the Spotify desktop app of this PC only. The fades are what an item’s music starts with when you switch it on in the Plan tab; each item can change its own.').frag;
}

function openSpotify() {
  spotifySheet = sheetDialog({ title: 'Spotify', track: 'dialog.settings-spotify', onClose: () => { spotifySheet = null; } });
  drawSpotify();
}

function drawSpotify() {
  if (!spotifySheet) return;
  const sp = spotify;
  const waiting = !!sp && sp.login.waiting;
  const fact = (name, value) => `<div class="list-row deck-row"><span class="deck-label">${name}</span><span class="grow small">${esc(value)}</span></div>`;
  const body = spotifySheet.body;
  body.classList.add('settings-sheet-stack');
  body.innerHTML =
    (sp ? `<div class="list" data-spotify-card>${fact('Account', sp.detail)}${fact('Plays on', sp.device || 'The Spotify app on this PC')}${fact('Last check', whenText(sp.checked_at))}</div>` : '') +
    (waiting ? `<p class="status-line unknown" data-login-line>${icon('refresh-cw')} ${esc(sp.login.detail)}. This sheet updates by itself.</p>` : '') +
    (sp && sp.login.failed ? `<p class="status-line warn" data-login-line>${icon('triangle-alert')} ${esc(sp.login.detail)}</p>` : '') +
    (sp && !sp.client_id_set ? '<p class="small muted settings-note">First create the Spotify developer app and put its client id in <code>.env</code> (see Spotify setup in the README).</p>' : '') +
    '<p class="small muted settings-note">Music plays on the Spotify desktop app of this PC only, never a web player (a Premium account): open the app before the session. Connecting opens Spotify’s login in the browser; the login is saved on this PC only.</p>' +
    `<div class="row-actions settings-actions"><button type="button" class="button-tint" data-connect data-track="settings.spotify-connect"${!sp || !sp.client_id_set || waiting ? ' disabled' : ''}>${icon('link')} ${sp && sp.logged_in ? 'Reconnect Spotify' : 'Connect Spotify'}</button>` +
    `<button type="button" class="button-surface" data-check data-track="settings.spotify-check"${sp ? '' : ' disabled'}>${icon('refresh-cw')} Check now</button></div>`;
  body.querySelector('[data-connect]').addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    try {
      spotify = await api('/api/settings/spotify/connect', { method: 'POST' });
      toast('Log in to Spotify in the browser window that opened');
    } catch (err) { toast(err.message, 'error'); }
    paintSpotify();
    pollLogin();
  });
  body.querySelector('[data-check]').addEventListener('click', (e) => {
    e.currentTarget.disabled = true;
    loadSpotify(true);
  });
}

// ---- About -----------------------------------------------------------------------

function buildLine() {
  if (!build) return '…';
  if (build.failed) return 'unknown';
  return buildReadoutText(build.git_sha, build.built_at).replace(/^Build:\s*/, '');
}

function aboutGroup() {
  return settingsGroup('about', 'About', [
    factRow({ glyph: 'monitor', label: 'Build', value: buildLine(), valueKey: 'build' }),
    valueRow({ glyph: 'file-text', label: 'Credits', value: plural(CREDITS.length, 'source'), track: 'settings.credits-open', onOpen: openCredits }),
  ]).frag;
}

function paintAbout() {
  paintValue('build', buildLine());
}

function openCredits() {
  const { body } = sheetDialog({ title: 'Credits', track: 'dialog.settings-credits' });
  body.classList.add('credits');
  body.innerHTML = `<ul class="credit-list">${CREDITS.map(([name, url, what]) =>
    `<li><a href="${url}" target="_blank" rel="noopener">${name}</a> — ${what}</li>`).join('')}</ul>`;
}
