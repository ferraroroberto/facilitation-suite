// The one stage renderer: the /stage window, the presenter's "on stage now" and
// "next" previews all draw an item through createStage(), so what the
// presenter sees is what Zoom sees. The canvas is a 1920×1080 logical box,
// scaled to fit its host; the look comes from the session theme
// (/themes/<name>.css + the session's own theme.css), never the fleet UI.

import { esc, lines } from '/static/js/ui.js';
import { remaining } from '/static/js/live.js';
import { words, roundLine } from '/static/js/stage-words.js';
import { roleStyle } from '/static/js/lettering.js';

export const W = 1920;
export const H = 1080;

const ICON = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`;

// Activity plug-ins: /activities/<type>/stage.js exports render(body, result, ctx)
// (ctx.lang: the session's language; ctx.quiz: the live quiz snapshot when it is
// about this item, else null; ctx.now: the server's clock in ms; ctx.preview: a
// presenter or Plan-tab preview, never the stage itself) and optionally
// subtitle(item, lang) — a line under the title; an optional stage.css is
// linked once. render runs again only when the result, the names switch or the
// quiz snapshot change. Loaded on first use, then cached.
// The server stamps this module's own URL with the asset hash (?v=…) and every literal import in it, but
// not a template one: the plug-in URLs reuse this module's stamp so they cache and invalidate with it.
const VERSION = new URL(import.meta.url).search;
const plugins = {};
export function loadPlugin(type) {
  if (!plugins[type]) {
    plugins[type] = import(`/activities/${type}/stage.js${VERSION}`).then((mod) => {
      if (!document.querySelector(`link[data-plugin="${type}"]`)) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = `/activities/${type}/stage.css${VERSION}`;
        link.dataset.plugin = type;
        link.onerror = () => link.remove(); // stage.css is optional
        document.head.appendChild(link);
      }
      return mod;
    }).catch(() => null);
  }
  return plugins[type];
}

/** The result to draw for `item`: an explicit one (preview, freeze) or the live capture's. */
function resultFor(item, ctx) {
  if ('result' in ctx) return ctx.result;
  const cap = ctx.state && ctx.state.capture;
  return cap && cap.item_id === item.id ? cap.result : null;
}

/**
 * An item's own lettering as inline style — its font, size and capitals —
 * where it sets them; the rest follows the session (theme variables).
 */
function lettering(f, defaultSize) {
  f = f || {};
  // "theme" (and the older "Patrick Hand") = the session's stage font.
  const family = !f.family || f.family === 'theme' || f.family === 'Patrick Hand' ? '' : `font-family:'${esc(f.family)}',var(--st-font);`;
  const size = Number(f.size_px) || defaultSize;
  return family + (f.caps === true ? 'text-transform:uppercase;' : f.caps === false ? 'text-transform:none;' : '') + (size ? `font-size:${size}px;` : '');
}

const ANCHOR = { top: 'flex-start', middle: 'center', bottom: 'flex-end' };

/** A slide's timer goes bottom-left when the camera zone takes the bottom-right corner. */
function pillLeft(zone) {
  return !!(zone && zone[2] > 0.8 && zone[3] > 0.85);
}

/**
 * Which of a slide's text boxes are its title: the ones PowerPoint marks as
 * the title, else (a deck of plain text boxes) the ones in the biggest size.
 */
function titleBoxes(boxes) {
  if (boxes.some((b) => b.title)) return boxes.map((b) => !!b.title);
  const biggest = Math.max(...boxes.map((b) => Number(b.size) || 0));
  return boxes.map((b) => (Number(b.size) || 0) === biggest);
}

/** A slide's text boxes (from the import), drawn over its text-free picture: its title in the title lettering, the rest as slide text. */
function slideText(it) {
  const own = lettering(Object.assign({}, it.font, { size_px: null, roles: null }));
  const titles = titleBoxes(it.text_boxes);
  return `<div class="st-slide-text">` + it.text_boxes.map((b, i) =>
    `<div class="st-tbox${titles[i] ? ' title' : ''}" data-size="${Number(b.size) || 40}" style="left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px;` +
    `padding:${(b.pad || []).map((p) => `${Number(p) || 0}px`).join(' ')};color:${esc(b.color)};text-align:${esc(b.align)};` +
    `justify-content:${ANCHOR[b.anchor] || 'flex-start'};font-size:${Number(b.size) || 40}px;${titles[i] ? own : ''}"><span>${esc(b.text)}</span></div>`).join('') +
    '</div>';
}

/**
 * Shrink each slide text box until its text fits (a handwriting font can run
 * wider or taller than PowerPoint's), down to 60 % of its size.
 */
function fitSlideText(root) {
  root.querySelectorAll('.st-tbox').forEach((b) => {
    const base = Number(b.dataset.size) || 40;
    let size = base;
    b.style.fontSize = `${size}px`;
    while ((b.scrollHeight > b.clientHeight + 1 || b.scrollWidth > b.clientWidth + 1) && size > base * 0.6) {
      size *= 0.94;
      b.style.fontSize = `${size}px`;
    }
  });
}

/** A camera zone (fractions of the canvas) as a box in canvas px when it is a corner box (under 40 % of the canvas height), else null. */
function cornerBox(zone) {
  if (!zone) return null;
  const [x0, y0, x1, y1] = [zone[0] * W, zone[1] * H, zone[2] * W, zone[3] * H];
  return y1 - y0 >= H * 0.4 ? null : { x0, y0, x1, y1 };
}

/**
 * Under a corner camera the title text beside it is centred vertically on the camera (#190):
 * an activity's question with its subtitle, and an imported slide's title boxes (a box that
 * reaches under the camera is left alone). Only a block that fits the camera's height moves;
 * a longer one keeps flowing from the top and below the camera, as before. Measured in canvas
 * px, so it holds at any window size; a hidden host measures 0 and is redone when it shows.
 * The quiz keeps its own flow: its status row and bars rise into the band under its title.
 */
function centreOnCamera(canvas, it) {
  const box = cornerBox(it.zone);
  const frame = canvas.getBoundingClientRect();
  const scale = frame.width / W;
  if (!box || !scale || (it.type || '').startsWith('quiz')) return;
  const mid = (box.y0 + box.y1) / 2;
  const centre = (move, first, last) => {
    move.style.transform = '';
    const top = (first.getBoundingClientRect().top - frame.top) / scale;
    const bottom = (last.getBoundingClientRect().bottom - frame.top) / scale;
    if (bottom - top > box.y1 - box.y0 || bottom < box.y0 || top > box.y1) return;
    move.style.transform = `translateY(${mid - (top + bottom) / 2}px)`;
  };
  if (it.kind === 'activity') {
    const head = canvas.querySelector('.st-head');
    const sub = head && head.querySelector('.st-sub');
    if (head) centre(head, head.querySelector('.st-question'), sub && !sub.hidden ? sub : head.querySelector('.st-question'));
  } else if (it.kind === 'slide') {
    canvas.querySelectorAll('.st-tbox.title').forEach((b) => {
      if (parseFloat(b.style.left) + parseFloat(b.style.width) <= box.x0 + 1) centre(b, b.firstElementChild, b.firstElementChild);
    });
  }
}

/**
 * Where an item's content goes, from its camera zone (Settings → profiles, in
 * fractions of the canvas): a tall zone at a side keeps the content beside it;
 * a corner box keeps the title clear of it and starts the body under it. So
 * changing the OBS profile (or moving a zone) re-flows the stage by itself.
 */
function zoneStyle(zone) {
  if (!zone) return '';
  const corner = cornerBox(zone);
  if (!corner) {
    const [x0, x1] = [zone[0] * W, zone[2] * W];
    return x0 >= W / 2 ? `--st-right:${Math.round(W - x0 + 60)}px;` : `--st-left:${Math.round(x1 + 60)}px;`;
  }
  return `--st-head-right:${Math.max(0, Math.round(W - corner.x0 - 110 + 40))}px;--st-head-min:${Math.max(0, Math.round(corner.y1 - 80 + 24))}px;`;
}

/** "mm:ss" for the countdowns (stage, presenter, phone). Rounds up, so 00:00 shows only at
 * zero, and never turns into hours — unlike live.js `hms`, which counts up (rounds down). */
export function clock(sec) {
  const s = Math.max(0, Math.ceil(sec));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

const TIMER_STATES = ['idle', 'running', 'paused', 'done'];

/** Where a stage timer is (#211): not started, running, paused or ended — the class that colours it. */
function timerState(t) {
  if (!t) return 'idle';
  if (t.done) return 'done';
  return t.running_since == null ? 'paused' : 'running';
}

function setTimerState(el, state) {
  el.classList.remove(...TIMER_STATES);
  el.classList.add(state);
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
  let plugin = null;
  let pluginFor = null;
  let lastResult;
  let lang = 'en';

  // A hidden host (a collapsed section, an inactive tab) measures 0 for every fit step
  // (fitSlideText, the quiz/word-cloud/… plug-ins' own layout), so a preview drawn while
  // hidden never shrinks its text or lays out its words — until something else redraws it.
  // One host-level 0→visible transition re-runs whatever fit step the current item needs.
  let wasVisible = host.clientWidth > 0 && host.clientHeight > 0;
  function fit() {
    const w = host.clientWidth;
    const h = host.clientHeight || (w * H) / W;
    const s = Math.min(w / W, h / H) || 0;
    canvas.style.transform = `translate(${(w - W * s) / 2}px, ${(h - H * s) / 2}px) scale(${s})`;
    const visible = w > 0 && h > 0;
    if (visible && !wasVisible) refit();
    wasVisible = visible;
  }
  new ResizeObserver(fit).observe(host);
  fit();

  /**
   * Redraw the current item from scratch, now that the host has a real size: a plug-in's own
   * fresh/unchanged cache (e.g. the quiz renderer's, keyed by its DOM node) is keyed to the old,
   * mis-fit body, so a fresh body — the same rebuild a new item selection already gets — is the
   * one change that reliably re-runs every fit step (fitSlideText, a plug-in's own layout).
   */
  function refit() {
    if (!item || !lastCtx) return;
    build(item, lastCtx);
    update(lastCtx);
  }

  function stageTimer(it, state) {
    if (!it || !it.timer || it.timer.show_on === 'presenter') return null;
    return (state.timers || {})[it.id] || null;
  }

  function build(it, ctx) {
    item = it;
    plugin = null;
    lastResult = undefined;
    if (it && it.kind === 'activity' && it.type) {
      pluginFor = it.id;
      loadPlugin(it.type).then((mod) => {
        if (pluginFor !== it.id) return;
        plugin = mod;
        lastResult = undefined;
        if (lastCtx) update(lastCtx);
      });
    }
    if (!it) {
      canvas.innerHTML = '';
      return;
    }
    const sid = ctx.plan.session.id;
    lang = ctx.plan.run.language || 'en';
    const hint = ctx.plan.run.chat_hint || words(lang).chat_hint;
    // the item's own lettering for its other texts (hint, answers, subtitles, slide text) rides on its box
    const roles = roleStyle((it.font || {}).roles);
    let html = `<div class="st-item" data-kind="${it.kind}" data-profile="${esc(it.profile)}" data-type="${esc(it.type || '')}" style="${it.kind === 'slide' ? '' : zoneStyle(it.zone)}${roles}">`;
    if (it.kind === 'slide') {
      const slides = `/api/sessions/${encodeURIComponent(sid)}/slides/`;
      html += it.slide_bg && it.text_boxes
        ? `<img class="st-slide" alt="" src="${slides}${esc(it.slide_bg)}">${slideText(it)}`
        : it.slide_file
          ? `<img class="st-slide" alt="" src="${slides}${esc(it.slide_file)}">`
          : `<div class="st-content"><h1 class="st-question" style="${lettering(it.font)}">${lines(it.title)}</h1></div>`;
      // a slide's timer sits in a bottom corner, away from the camera
      if (it.timer) html += `<span class="st-pill st-slide-pill${pillLeft(it.zone) ? ' left' : ''}" data-pill hidden>${ICON('timer')}<span data-pill-text></span></span>`;
    } else if (it.kind === 'break' || it.kind === 'breakout') {
      // a breakout says which round is in the rooms, under its title
      const sub = it.kind === 'breakout' ? roundLine(lang, (it.options || {}).round, it.rooms) : '';
      html += `<div class="st-content"><div class="st-break"><h1 class="st-break-title" style="${lettering(it.font)}">${lines(it.title)}</h1>` +
        (sub ? `<p class="st-sub">${esc(sub)}</p>` : '') +
        (it.timer ? `<div class="st-break-clock" data-clock></div>` : '') + `</div></div>`;
    } else {
      const text = it.capture ? (it.question || it.title) : it.title;
      html += `<div class="st-content">` +
        `<div class="st-head"><h1 class="st-question" style="${lettering(it.font)}">${lines(text)}</h1>` +
        `<p class="st-sub" data-sub hidden></p></div>` +
        `<div class="st-body" data-body></div>` +
        `<div class="st-foot">` +
        (it.capture ? `<span class="st-hint">${ICON('message-square')}${esc(hint)}</span>` : '') +
        // a quiz question takes chat answers too while its lobby accepts them (#54): shown while it is open
        (it.type === 'quiz' ? `<span class="st-hint" data-quizhint hidden>${ICON('message-square')}${esc(words(lang).quiz_hint)}</span>` : '') +
        `<span class="st-spacer"></span><span class="st-count" data-count></span>` +
        `<span class="st-pill" data-pill hidden>${ICON('timer')}<span data-pill-text></span></span>` +
        `</div></div>`;
    }
    if (opts.guides && it.zone && (it.kind !== 'slide' || opts.slideGuides)) {
      const z = it.zone;
      html += `<div class="st-guide" style="left:${z[0] * W}px;top:${z[1] * H}px;width:${(z[2] - z[0]) * W}px;height:${(z[3] - z[1]) * H}px">${ICON('video')}<span>Camera</span></div>`;
    }
    html += `</div><div class="st-blackout" data-blackout hidden></div>`;
    canvas.innerHTML = html;
    if (it.text_boxes) fitSlideText(canvas);
    centreOnCamera(canvas, it);
    document.fonts.ready.then(() => {
      if (item !== it) return;
      if (it.text_boxes) fitSlideText(canvas);
      centreOnCamera(canvas, it);
    });
  }

  let lastCtx = null;
  function update(ctx) {
    lastCtx = ctx;
    const state = ctx.state || {};
    const blk = canvas.querySelector('[data-blackout]');
    if (blk) blk.hidden = !(opts.blackout !== false && state.blackout);
    if (!item) return;
    const t = stageTimer(item, state);
    const phase = timerState(t);
    // "Remove the timer" at 00:00: it leaves the stage once done.
    const gone = !!(t && t.done && item.timer && item.timer.end === 'hide');
    const clockEl = canvas.querySelector('[data-clock]');
    if (clockEl) {
      const left = t ? remaining(t, ctx.now) : item.timer.seconds;
      clockEl.textContent = clock(left);
      setTimerState(clockEl, phase);
      clockEl.hidden = gone;
    }
    const pill = canvas.querySelector('[data-pill]');
    if (pill) {
      pill.hidden = !t || !!opts.noTimers || gone;
      if (t) pill.querySelector('[data-pill-text]').textContent = clock(remaining(t, ctx.now));
      setTimerState(pill, phase);
    }
    const quizHint = canvas.querySelector('[data-quizhint]');
    if (quizHint) {
      const q = state.quiz;
      quizHint.hidden = !(q && q.accept_chat && q.item_id === item.id && q.phase === 'question');
    }
    if (item.kind === 'activity') drawResult(ctx);
  }

  function drawResult(ctx) {
    const result = resultFor(item, ctx);
    const names = 'names' in ctx ? ctx.names : !!(ctx.state && ctx.state.names);
    const body = canvas.querySelector('[data-body]');
    const count = canvas.querySelector('[data-count]');
    if (count) {
      count.innerHTML = result && result.answers
        ? `${ICON('message-square')} <b>${result.answers}</b> · ${ICON('users')} <b>${result.people}</b>` : '';
    }
    if (!plugin || !body) return;
    const sub = canvas.querySelector('[data-sub]');
    if (sub && plugin.subtitle) {
      const before = sub.textContent;
      sub.textContent = plugin.subtitle(item, lang);
      sub.hidden = !sub.textContent;
      if (sub.textContent !== before) centreOnCamera(canvas, item);
    }
    // The live quiz game (#53) reaches only the item it is about: every other item gets null.
    const q = ctx.state && ctx.state.quiz;
    const quiz = q && q.item_id === item.id ? q : null;
    const sig = JSON.stringify([result, names, quiz]);
    if (sig === lastResult) return; // nothing new: the plug-in keeps its DOM (and its animations)
    lastResult = sig;
    try {
      plugin.render(body, result, { item, names, options: item.options || {}, lang, quiz, now: ctx.now, preview: !!opts.guides });
    } catch (e) {
      console.error('stage plug-in failed', item.type, e);
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
    /** Resolves once the item's plug-in (if any) is loaded and drawn. */
    ready() { return item && item.kind === 'activity' && item.type ? loadPlugin(item.type) : Promise.resolve(); },
  };
}

/**
 * Load the session's theme on top of /themes/default.css (linked by the page):
 * a named repo theme when the plan names one, then the session's font and own
 * theme.css (re-fetched whenever the plan changes). Resolves once they have
 * loaded (or failed), so a caller can wait for the fonts after it.
 */
export function applyTheme(plan) {
  return linkTheme((plan && plan.run && plan.run.theme) || 'default', plan && plan.active ? `/api/live/theme.css?rev=${plan.rev}` : null);
}

/** The same for a session that is not live — the plan editor's stage previews. */
export function applySessionTheme(sid, theme, version) {
  return linkTheme(theme || 'default', sid ? `/api/sessions/${encodeURIComponent(sid)}/theme.css?v=${encodeURIComponent(version || '')}` : null);
}

function linkTheme(name, sessionCss) {
  const want = [];
  if (name !== 'default' && /^[a-z0-9-]+$/.test(name)) want.push(`/themes/${name}.css`);
  if (sessionCss) want.push(sessionCss);
  const have = [...document.querySelectorAll('link[data-stage-theme]')];
  if (have.length === want.length && have.every((l, i) => l.getAttribute('href') === want[i])) return Promise.resolve();
  have.forEach((l) => l.remove());
  return Promise.all(want.map((href) => new Promise((resolve) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.dataset.stageTheme = '';
    link.onload = link.onerror = resolve;
    document.head.appendChild(link);
  })));
}
