// The live connection shared by the stage and the presenter: one WebSocket to
// /ws, reconnecting with backoff; the server owns the state and pushes full
// snapshots, clients only send intents (actions).

/**
 * connectLive(role, handlers) → { send(action, arg), hello(w, h), now(), plan, state, online }
 * handlers: onPlan(plan), onState(state), onMessage(msg) for anything else
 * (chime, error, chat …), onConnection(online).
 */
export function connectLive(role, handlers = {}) {
  const live = {
    plan: null,
    state: null,
    online: false,
    skew: 0, // server clock − local clock, ms
    send(action, arg = null) {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'action', action, arg }));
    },
    hello(w, h) {
      helloMsg = { type: 'hello', w, h };
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(helloMsg));
    },
    /** The server's clock now, in ms (timers and clocks are server epochs). */
    now() { return Date.now() + this.skew; },
  };
  let ws = null;
  let delay = 500;
  let helloMsg = null;

  function open() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws?role=${encodeURIComponent(role)}`);
    ws.onopen = () => {
      delay = 500;
      live.online = true;
      if (helloMsg) ws.send(JSON.stringify(helloMsg));
      if (handlers.onConnection) handlers.onConnection(true);
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === 'plan') {
        live.plan = msg;
        if (handlers.onPlan) handlers.onPlan(msg);
      } else if (msg.type === 'state') {
        live.skew = msg.server_now - Date.now();
        live.state = msg.state;
        if (handlers.onState) handlers.onState(msg.state);
      } else if (handlers.onMessage) {
        handlers.onMessage(msg);
      }
    };
    ws.onclose = () => {
      const was = live.online;
      live.online = false;
      if (was && handlers.onConnection) handlers.onConnection(false);
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 5000);
    };
    ws.onerror = () => { /* onclose follows and reconnects */ };
  }
  open();
  return live;
}

/** Seconds left on an item timer state at server time `now` (never below 0). */
export function remaining(t, now) {
  if (!t) return null;
  const running = t.running_since != null ? (now - t.running_since) / 1000 : 0;
  return Math.max(0, t.total - t.elapsed - running);
}

/** Seconds → "mm:ss", or "h:mm:ss" from an hour on. */
export function hms(sec) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${String(m).padStart(2, '0')}:${ss}`;
}

/**
 * The session clock against the plan, for the item on stage: seconds since
 * the clock started, the item's section, minutes spent in it, and the drift —
 * minutes behind (+) or ahead (−) of the planned section start, counting an
 * overrun of the section itself. null until the session clock starts.
 */
export function timing(plan, state, cur, now) {
  const started = state.clock.started_at;
  if (!started) return null;
  const section = cur ? plan.run.sections.find((x) => x.id === cur.section_id) || null : null;
  const out = { elapsed: (now - started) / 1000, section, inSection: 0, drift: 0 };
  if (section) {
    const entered = state.section_entered[section.id] || now;
    out.inSection = (now - entered) / 60000;
    out.drift = Math.round((entered - started) / 60000 - section.planned_start + Math.max(0, out.inSection - section.minutes));
  }
  return out;
}

/** "+2 min" / "−3 min" / "on time". */
export function driftText(minutes) {
  return minutes >= 1 ? `+${minutes} min` : minutes <= -1 ? `−${-minutes} min` : 'on time';
}

/** The keyboard map shared by the stage and the presenter (PowerPoint-like). */
export const KEYS = {
  ArrowRight: 'next', PageDown: 'next', ArrowDown: 'next', n: 'next', N: 'next',
  ArrowLeft: 'prev', PageUp: 'prev', ArrowUp: 'prev', p: 'prev', P: 'prev',
  b: 'blackout', B: 'blackout', '.': 'blackout',
  ' ': 'capture_toggle',
  t: 'timer_toggle', T: 'timer_toggle',
  '+': 'timer_add_minute', m: 'timer_add_minute', M: 'timer_add_minute',
};

/** Wire the keyboard map to live.send, ignoring keys typed into fields. */
export function bindKeys(live, extra = {}) {
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (t && t.tagName === 'BUTTON' && (e.key === ' ' || e.key === 'Enter')) return;
    if (extra[e.key]) { e.preventDefault(); extra[e.key](); return; }
    const action = KEYS[e.key];
    if (!action) return;
    e.preventDefault();
    live.send(action);
  });
}
