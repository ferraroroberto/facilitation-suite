// Shared UI helpers for the app, the presenter and the remote: the API wrapper,
// the page header (vendored home-head + theme toggle + settings), the global
// appearance, toasts, escaping.

import { icon } from '/static/_vendored/icons/icons.js';

export const APP = 'facilitation-suite';

/** fetch JSON; a non-2xx answer throws an Error carrying the envelope's message. */
export async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  if (!res.ok) {
    const msg = (data && data.error && data.error.message) || `Request failed (${res.status})`;
    const err = new Error(msg);
    err.status = res.status;
    err.code = data && data.error && data.error.code;
    throw err;
  }
  return data;
}

export function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** A stage title as typed, as HTML: "\n" (typed as backslash-n) or a real newline breaks the line. */
export function lines(s) {
  return esc(s).replace(/\\n|\r?\n/g, '<br>');
}

/** The same title on one line — lists, captions, the presenter's "next". */
export function oneLine(s) {
  return String(s == null ? '' : s).replace(/\s*(?:\\n|\r?\n)\s*/g, ' ').trim();
}

let toastTimer = null;
/** Global toast — reserved for user-initiated command results (design.md). */
export function toast(message, kind = 'info') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = message;
  el.dataset.kind = kind;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'error' ? 6000 : 3000);
}

// ---- Appearance (#92) --------------------------------------------------------
// One server-side value — system | light | dark — for the app, the presenter and
// the phone remote on every device (the stage and /play follow the session theme).
// The page's pre-paint script stamps html[data-theme] from `<app>.theme` (the
// no-flash cache: 'light' / 'dark', absent = follow the OS); the page then
// follows the live snapshot's `appearance` — sent on connect, so a stale cache is
// corrected at once, and again on every change (GET /api/settings/appearance
// answers the same). A sun/moon toggle anywhere sets the global value.

const THEME_KEY = APP + '.theme';
const APPEARANCES = ['system', 'light', 'dark'];
const osDark = window.matchMedia('(prefers-color-scheme: dark)');
const appearanceListeners = new Set();
let appearance = cachedAppearance();
// Our own change, until the server's value carries it back: a snapshot sent before
// the change reached the server is stale and must not flip the page back.
let awaiting = null;
let awaitTimer = null;
let lastServer = null;

function cachedAppearance() {
  let v = null;
  try { v = localStorage.getItem(THEME_KEY); } catch (e) { /* storage blocked */ }
  return v === 'light' || v === 'dark' ? v : 'system';
}

export function currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function currentAppearance() {
  return appearance;
}

/** Call fn(appearance) whenever it changes (the Settings control repaints). */
export function onAppearance(fn) {
  appearanceListeners.add(fn);
}

function themeOf(value) {
  return value === 'system' ? (osDark.matches ? 'dark' : 'light') : value;
}

function paintAppearance(value) {
  appearance = value;
  const theme = themeOf(value);
  document.documentElement.dataset.theme = theme;
  try {
    if (value === 'system') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, value);
  } catch (e) { /* not cached: the next load reconciles from the server */ }
  document.querySelectorAll('[data-theme-toggle] use').forEach((u) => {
    u.setAttribute('href', theme === 'dark' ? '#i-sun' : '#i-moon');
  });
  appearanceListeners.forEach((fn) => fn(value));
}

// Only pages that follow the appearance watch the OS: the stage imports this module too.
let watchingOs = false;
function watchOs() {
  if (watchingOs) return;
  watchingOs = true;
  osDark.addEventListener('change', () => { if (appearance === 'system') paintAppearance('system'); });
}

/** The server's value (page load, live snapshot): follow it, unless it predates our own change. */
export function followAppearance(value) {
  if (!APPEARANCES.includes(value)) return;
  watchOs();
  lastServer = value;
  if (awaiting) {
    if (value !== awaiting) return;
    stopAwaiting(false);
  }
  // The painted theme counts too: another tab can rewrite the shared cache between
  // this page's pre-paint and this module reading it.
  if (value !== appearance || themeOf(value) !== currentTheme()) paintAppearance(value);
}

function stopAwaiting(catchUp) {
  awaiting = null;
  clearTimeout(awaitTimer);
  if (catchUp && lastServer) followAppearance(lastServer);
}

/** Read the server's value once, on load (the pre-paint cache may be stale). */
export function loadAppearance() {
  api('/api/settings/appearance').then((r) => followAppearance(r.appearance)).catch(() => { /* keep the cache */ });
}

/** Set the global appearance: this page at once, every other page through the server. */
export async function setAppearance(value) {
  watchOs();
  paintAppearance(value);
  awaiting = value;
  clearTimeout(awaitTimer);
  awaitTimer = setTimeout(() => stopAwaiting(true), 3000); // no echo (offline): stop waiting
  try {
    const r = await api('/api/settings/appearance', { method: 'PUT', body: { appearance: value } });
    // Saved. The live echo (same socket as any stale snapshot, so after it) ends the wait.
    if (awaiting === value) lastServer = r.appearance;
  } catch (e) {
    toast(`Appearance not saved: ${e.message}`, 'error');
    stopAwaiting(false);
    loadAppearance(); // back to what the server holds
  }
}

/** The sun/moon button: the other of light and dark, for every screen. */
export function toggleTheme() {
  return setAppearance(currentTheme() === 'dark' ? 'light' : 'dark');
}

/**
 * The page header (design.md `page-header`, vendored home-head): leading glyph +
 * tab title, one context line, trailing theme toggle and (optionally) settings.
 */
export function pageHead({ glyph, title, status = '', settings = true }) {
  const head = document.createElement('div');
  head.className = 'card home-head';
  const themeGlyph = currentTheme() === 'dark' ? 'sun' : 'moon';
  head.innerHTML =
    `<span class="home-title">${icon(glyph)}<span class="home-title-text">${esc(title)}</span></span>` +
    `<span class="status">${esc(status)}</span>` +
    `<button type="button" class="home-toggle" data-theme-toggle aria-label="Toggle theme" title="Toggle theme">${icon(themeGlyph)}</button>` +
    (settings ? `<button type="button" class="home-toggle" data-open-settings aria-label="Settings" title="Settings">${icon('settings')}</button>` : '');
  head.querySelector('[data-theme-toggle]').addEventListener('click', toggleTheme);
  return head;
}

export function setStatus(head, text) {
  const s = head.querySelector('.status');
  if (s) s.textContent = text;
}

/** Minutes → "1:30" style. */
export function fmtMinutes(min) {
  const m = Math.max(0, Math.round(min));
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
}
