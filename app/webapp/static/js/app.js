// The app shell: nav (vendored), the Settings pane behind the header gear, the
// build readout, and lazy view modules — one per pane, each exporting
// mount(el, ctx) and optionally show().

import { initNavTabs } from '/static/_vendored/nav/nav-tabs.js';
import { buildReadoutText } from '/static/_vendored/page-foot/page-foot.js';
import { api, APP, followAppearance } from '/static/js/ui.js';
import { connectLive } from '/static/js/live.js';

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
  /** Add an item to the Plan tab's unsaved edits (it loads the plan first when needed). */
  async addToPlan(item) { return (await ensureView('plan')).stageItem(item); },
  /** The rounds changed (shuffle, presence, roster): views showing rooms refresh. */
  groupListeners: new Set(),
  onGroups(fn) { this.groupListeners.add(fn); },
  groupsChanged() { this.groupListeners.forEach((fn) => fn()); },
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

/** /#sessions/<id> belongs to the Sessions tab: another tab drops it, so a reload opens that tab. */
function leaveSessionHash(name) {
  if (name !== 'sessions' && location.hash.startsWith('#sessions/')) {
    history.replaceState(history.state, '', location.pathname + location.search);
  }
}

async function showView(name) {
  leaveSessionHash(name);
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

// The nav's first onChange (its stored tab, at init) runs before followHash: it must
// not drop a /#sessions/<id> link the page was opened with.
let navReady = false;
const nav = initNavTabs({
  storageKey: APP + '.tab',
  onChange: (tab) => {
    if (navReady) leaveSessionHash(tab);
    settingsPane.hidden = true;
    ensureView(tab).then((mod) => { if (mod.show) mod.show(); });
  },
});
navReady = true;

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-open-settings]')) showView('settings');
});

ctx.goTo = showView;

/**
 * A deep link — /#settings or /#settings/<section> (the presenter's music chip → Music),
 * or /#sessions/<id>: that session open full screen (#150; the Sessions tab keeps it).
 */
async function followHash() {
  const open = location.hash.match(/^#sessions\/(.+)$/);
  if (open) {
    (await ensureView('sessions')).openSession(decodeURIComponent(open[1]), { push: false });
    showView('sessions');
    return true;
  }
  const m = location.hash.match(/^#settings(?:\/([a-z]+))?$/);
  if (!m) return false;
  history.replaceState(null, '', location.pathname + location.search); // a reload opens the last tab again
  if (m[1]) (await ensureView('settings')).focusSection(m[1]);
  showView('settings');
  return true;
}
window.addEventListener('hashchange', followHash);
followHash().then((linked) => { if (!linked) showView(nav.getTab() || 'sessions'); });

// The live snapshot carries the global light/dark (#92): a toggle on the presenter,
// the phone or another tab switches this one too.
connectLive('app', { onState: (s) => followAppearance(s.appearance) });

api('/api/version').then((v) => {
  document.getElementById('buildReadout').textContent = buildReadoutText(v.git_sha, v.captured_at);
}).catch(() => {
  document.getElementById('buildReadout').textContent = 'Build: unknown';
});
