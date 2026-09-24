// The one stage renderer: the /stage window, the presenter's "on stage now" and
// "next" previews all draw an item through createStage(), so what the
// presenter sees is what Zoom sees. The canvas is a 1920×1080 logical box,
// scaled to fit its host; the look comes from the session theme
// (/themes/<name>.css + the session's own theme.css), never the fleet UI.

import { esc } from '/static/js/ui.js';
import { remaining } from '/static/js/live.js';

export const W = 1920;
export const H = 1080;

const ICON = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`;

/** "mm:ss" for the stage clocks. */
export function clock(sec) {
  const s = Math.max(0, Math.ceil(sec));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * createStage(host, { guides }) → { render(item, ctx), update(ctx), el }
 *   item: a run item (src/live/plan.py) or null
 *   ctx:  { plan, state, now }  (plan = the /ws plan message; now = server ms)
 *   guides: draw the camera zone as a dashed box (previews only — never on the real stage)
 */
export function createStage(host, opts = {}) {
  const frame = document.createElement('div');
  frame.className = 'stage-frame';
  const canvas = document.createElement('div');
  canvas.className = 'stage-canvas';
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  frame.appendChild(canvas);
  host.appendChild(frame);

  let key = null;
  let item = null;

  function fit() {
    const w = host.clientWidth;
    const h = host.clientHeight || (w * H) / W;
    const s = Math.min(w / W, h / H) || 0;
    canvas.style.transform = `translate(${(w - W * s) / 2}px, ${(h - H * s) / 2}px) scale(${s})`;
  }
  new ResizeObserver(fit).observe(host);
  fit();

  function stageTimer(it, state) {
    if (!it || !it.timer || it.timer.show_on === 'presenter') return null;
    return (state.timers || {})[it.id] || null;
  }

  function build(it, ctx) {
    item = it;
    if (!it) {
      canvas.innerHTML = '';
      return;
    }
    const sid = ctx.plan.session.id;
    const hint = ctx.plan.run.chat_hint || 'Write your answer in the Zoom chat';
    let html = `<div class="st-item" data-kind="${it.kind}" data-profile="${esc(it.profile)}" data-type="${esc(it.type || '')}">`;
    if (it.kind === 'slide') {
      html += it.slide_file
        ? `<img class="st-slide" alt="" src="/api/sessions/${encodeURIComponent(sid)}/slides/${esc(it.slide_file)}">`
        : `<div class="st-content"><h1 class="st-question" style="font-size:72px">${esc(it.title)}</h1></div>`;
    } else if (it.kind === 'break') {
      html += `<div class="st-content"><div class="st-break"><h1 class="st-break-title">${esc(it.title)}</h1>` +
        (it.timer ? `<div class="st-break-clock" data-clock></div>` : '') + `</div></div>`;
    } else {
      const f = it.font || {};
      const text = it.capture ? (it.question || it.title) : it.title;
      html += `<div class="st-content">` +
        `<div class="st-head"><h1 class="st-question" style="font-family:'${esc(f.family || 'Patrick Hand')}',var(--st-font);font-size:${Number(f.size_px) || 72}px">${esc(text)}</h1></div>` +
        `<div class="st-body" data-body></div>` +
        `<div class="st-foot">` +
        (it.capture ? `<span class="st-hint">${ICON('message-square')}${esc(hint)}</span>` : '') +
        `<span class="st-spacer"></span><span class="st-count" data-count></span>` +
        `<span class="st-pill" data-pill hidden>${ICON('timer')}<span data-pill-text></span></span>` +
        `</div></div>`;
    }
    if (opts.guides && it.zone && it.kind !== 'slide') {
      const z = it.zone;
      html += `<div class="st-guide" style="left:${z[0] * W}px;top:${z[1] * H}px;width:${(z[2] - z[0]) * W}px;height:${(z[3] - z[1]) * H}px">${ICON('video')}<span>Camera</span></div>`;
    }
    html += `</div><div class="st-blackout" data-blackout hidden></div>`;
    canvas.innerHTML = html;
  }

  function update(ctx) {
    const state = ctx.state || {};
    const blk = canvas.querySelector('[data-blackout]');
    if (blk) blk.hidden = !(opts.blackout !== false && state.blackout);
    if (!item) return;
    const t = stageTimer(item, state);
    const clockEl = canvas.querySelector('[data-clock]');
    if (clockEl) {
      const left = t ? remaining(t, ctx.now) : item.timer.seconds;
      clockEl.textContent = clock(left);
      clockEl.classList.toggle('done', !!(t && t.done));
    }
    const pill = canvas.querySelector('[data-pill]');
    if (pill) {
      pill.hidden = !t;
      if (t) {
        pill.querySelector('[data-pill-text]').textContent = clock(remaining(t, ctx.now));
        pill.classList.toggle('done', !!t.done);
      }
    }
  }

  return {
    el: canvas,
    /** Draw `it` (rebuilt only when the item or the plan changes), then update. */
    render(it, ctx) {
      const k = it ? `${ctx.plan.session.id}|${JSON.stringify(it)}` : null;
      if (k !== key) {
        key = k;
        build(it, ctx);
      }
      update(ctx);
    },
    update,
    get body() { return canvas.querySelector('[data-body]'); },
  };
}

/**
 * Load the session's theme on top of /themes/default.css (linked by the page):
 * a named repo theme when the plan names one, then the session's own theme.css
 * (re-fetched whenever the plan changes).
 */
export function applyTheme(plan) {
  const name = (plan && plan.run && plan.run.theme) || 'default';
  const want = [];
  if (name !== 'default' && /^[a-z0-9-]+$/.test(name)) want.push(`/themes/${name}.css`);
  if (plan && plan.active) want.push(`/api/live/theme.css?rev=${plan.rev}`);
  document.querySelectorAll('link[data-stage-theme]').forEach((l) => l.remove());
  want.forEach((href) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.dataset.stageTheme = '';
    document.head.appendChild(link);
  });
}
