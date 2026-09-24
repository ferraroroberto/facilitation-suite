// Shared UI helpers for the app and the presenter: the API wrapper, the page
// header (vendored home-head + theme toggle + settings), toasts, escaping.

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

export function currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(APP + '.theme', theme); } catch (e) { /* not persisted */ }
  document.querySelectorAll('[data-theme-toggle] use').forEach((u) => {
    u.setAttribute('href', theme === 'dark' ? '#i-sun' : '#i-moon');
  });
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
  head.querySelector('[data-theme-toggle]').addEventListener('click', () => {
    setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  });
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

/** Seconds → "mm:ss" (negative → "-mm:ss"). */
export function fmtClock(sec) {
  const neg = sec < 0;
  const s = Math.abs(Math.round(sec));
  const out = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  return neg ? '-' + out : out;
}
