// The app shell: nav (vendored), the Settings pane behind the header gear, the
// build readout, and lazy view modules — one per pane, each exporting
// mount(el, ctx) and optionally show().

import { initNavTabs } from '/static/_vendored/nav/nav-tabs.js';
import { buildReadoutText } from '/static/_vendored/page-foot/page-foot.js';
import { api, APP } from '/static/js/ui.js';

const VIEWS = {
  sessions: () => import('/static/js/views/sessions.js'),
  plan: () => import('/static/js/views/plan.js'),
  groups: () => import('/static/js/views/groups.js'),
  results: () => import('/static/js/views/results.js'),
  settings: () => import('/static/js/views/settings.js'),
};

const mounted = {};
const ctx = {
  /** The session every tab works on; views subscribe with onSession(). */
  sessionId: null,
  /** A session whose re-import waits for review: the Plan tab opens the review for it. */
  reviewFor: null,
  listeners: new Set(),
  setSession(id) {
    this.sessionId = id;
    try { localStorage.setItem(APP + '.session', id || ''); } catch (e) { /* not persisted */ }
    this.listeners.forEach((fn) => fn(id));
  },
  onSession(fn) { this.listeners.add(fn); },
  goTo(tab) { showView(tab); },
};
try { ctx.sessionId = localStorage.getItem(APP + '.session') || null; } catch (e) { ctx.sessionId = null; }

function ensureView(name) {
  // Cache the promise, not the module: the initial show and the nav's own
  // onChange can ask for the same view in the same tick.
  if (!mounted[name]) {
    const el = document.querySelector(`[data-view="${name}"]`);
    mounted[name] = VIEWS[name]().then(async (mod) => { await mod.mount(el, ctx); return mod; });
  }
  return mounted[name];
}

const settingsPane = document.getElementById('paneSettings');

async function showView(name) {
  if (name === 'settings') {
    document.querySelectorAll('.pane').forEach((p) => { p.hidden = p !== settingsPane; });
    document.querySelectorAll('.tabs .tab').forEach((t) => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
  } else {
    settingsPane.hidden = true;
    nav.setTab(name);
  }
  const mod = await ensureView(name);
  if (mod.show) mod.show();
}

const nav = initNavTabs({
  storageKey: APP + '.tab',
  onChange: (tab) => {
    settingsPane.hidden = true;
    ensureView(tab).then((mod) => { if (mod.show) mod.show(); });
  },
});

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-open-settings]')) showView('settings');
});

ctx.goTo = showView;
showView(nav.getTab() || 'sessions');

api('/api/version').then((v) => {
  document.getElementById('buildReadout').textContent = buildReadoutText(v.git_sha, v.captured_at);
}).catch(() => {
  document.getElementById('buildReadout').textContent = 'Build: unknown';
});
