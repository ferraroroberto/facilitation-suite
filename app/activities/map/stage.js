// The magic map on the stage (epic §10).
//
// The world outline is an SVG in an equirectangular projection with a 35°
// standard parallel (x = lon·cos35°·10, y = −lat·10, see scripts/build_geo.py).
// Pins are HTML on top, so labels keep their size while the map zooms.
//
// - auto-fit: the view eases to the pins' bounding box (plus padding, never
//   closer than a country-sized span), re-aimed as people arrive;
// - clusters: pins closer than a few pixels merge into one, the count in its
//   dot; a cluster keeps its element (keyed by its first answer) as people
//   join, so it never blinks out and back in;
// - labels: each goes right, left, above, below or off a corner of its dot —
//   the first place that overlaps no other dot or label — or is left off (a
//   click on the pin still says who); decided once per re-aim, not on every
//   animation frame;
// - inset: when a small region holds many people while the view spans far
//   more, that region gets its own zoomed box in the emptiest corner, joined
//   to a dashed frame on the main map; once shown it stays (same region, same
//   corner) until the crowd there clearly thins, so it does not flicker;
// - click a pin or a cluster: who is there, with city and country.

import { esc } from '/static/js/ui.js';

const KX = Math.cos((35 * Math.PI) / 180);
const S = 10;
const WORLD = { x: -170 * KX * S, y: -80 * S, w: 360 * KX * S, h: 138 * S }; // Antarctica left out
const MIN_SPAN = 14 * S; // never zoom closer than ~14° of longitude
const CLUSTER_PX = 34;
const EASE_MS = 900;
const DOT = 16; // a dot's half size, plus a little air, for label placement
const SIDES = ['right', 'left', 'up', 'down', 'up-right', 'up-left', 'down-right', 'down-left'];

let worldPaths = null;
function loadWorld() {
  worldPaths = worldPaths || fetch('/activities/map/world.svg').then((r) => r.text()).then((t) => {
    const m = t.match(/<g class="land">([\s\S]*)<\/g>/);
    return m ? m[1] : '';
  }).catch(() => '');
  return worldPaths;
}

const project = (lat, lon) => [lon * KX * S, -lat * S];
const ease = (t) => 1 - (1 - t) ** 3;
const ICON = (n) => `<svg class="icon" aria-hidden="true"><use href="#i-${n}"></use></svg>`;

/** A viewBox around the points, padded and stretched to the area's aspect. */
function fitBox(points, aspect, pad = 0.18) {
  if (!points.length) return { ...WORLD };
  const x0 = Math.min(...points.map((p) => p[0]));
  const x1 = Math.max(...points.map((p) => p[0]));
  const y0 = Math.min(...points.map((p) => p[1]));
  const y1 = Math.max(...points.map((p) => p[1]));
  let w = Math.max(x1 - x0, MIN_SPAN);
  let h = Math.max(y1 - y0, MIN_SPAN / aspect);
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  w *= 1 + 2 * pad;
  h *= 1 + 2 * pad;
  if (w / h > aspect) h = w / aspect; else w = h * aspect;
  // Never wider than the world — shrink both, so the aspect (and the pins) stay true.
  const cap = Math.min(1, (WORLD.w * 1.05) / w);
  w *= cap;
  h *= cap;
  return { x: cx - w / 2, y: cy - h / 2, w, h };
}

/** Group pins that land within CLUSTER_PX of each other at this view. */
function cluster(pins, vb, width, height) {
  const kx = width / vb.w;
  const ky = height / vb.h;
  const groups = [];
  for (const p of pins) {
    const sx = (p.xy[0] - vb.x) * kx;
    const sy = (p.xy[1] - vb.y) * ky;
    const g = groups.find((c) => Math.hypot(c.sx - sx, c.sy - sy) < CLUSTER_PX);
    if (g) {
      g.members.push(p);
      g.sx = (g.sx * (g.members.length - 1) + sx) / g.members.length;
      g.sy = (g.sy * (g.members.length - 1) + sy) / g.members.length;
    } else {
      groups.push({ sx, sy, members: [p] });
    }
  }
  for (const g of groups) {
    g.xy = [g.members.reduce((a, p) => a + p.xy[0], 0) / g.members.length, g.members.reduce((a, p) => a + p.xy[1], 0) / g.members.length];
    // The first answer names the cluster: later arrivals join it without re-creating it.
    g.key = String(Math.min(...g.members.map((p) => p.id)));
  }
  return groups;
}

/** A cluster's label: up to three first names (names on), else its city — the most common, "+N" for the others. */
function label(g, names) {
  const m = g.members;
  if (names && m.length <= 3) return m.map((p) => p.sender.split(' ')[0]).join(' · ');
  const count = new Map();
  m.forEach((p) => count.set(p.name, (count.get(p.name) || 0) + 1));
  const cities = [...count.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
  return cities.length === 1 ? cities[0] : `${cities[0]} +${cities.length - 1}`;
}

/** The densest small region worth an inset — or the one already shown, while it still holds a crowd. */
function findInset(pins, vb, prev) {
  if (prev) {
    const inside = pins.filter((p) => p.xy[0] >= prev.box.x && p.xy[0] <= prev.box.x + prev.box.w && p.xy[1] >= prev.box.y && p.xy[1] <= prev.box.y + prev.box.h);
    // the same region, as long as it keeps a crowd and the view stays much wider (looser than to open it)
    if (inside.length >= 4 && inside.length >= pins.length * 0.2 && vb.w > prev.box.w * 2.4) {
      // keep the frame while its pins sit comfortably inside it; re-frame when one reaches its edge
      const fit = fitBox(inside.map((p) => p.xy), 1.5, 0.25);
      const b = prev.box;
      const holds = fit.x >= b.x && fit.y >= b.y && fit.x + fit.w <= b.x + b.w && fit.y + fit.h <= b.y + b.h;
      return { pins: inside, box: holds ? b : fit, corner: prev.corner };
    }
  }
  if (pins.length < 6) return null;
  let best = null;
  for (const p of pins) {
    const near = pins.filter((q) => Math.abs(q.xy[0] - p.xy[0]) < 7 * S && Math.abs(q.xy[1] - p.xy[1]) < 5 * S);
    if (!best || near.length > best.length) best = near;
  }
  if (!best || best.length < Math.max(5, pins.length * 0.3)) return null;
  const box = fitBox(best.map((p) => p.xy), 1.5, 0.25);
  return vb.w > box.w * 3.2 ? { pins: best, box, corner: null } : null;
}

function lerpBox(a, b, t) {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, w: a.w + (b.w - a.w) * t, h: a.h + (b.h - a.h) * t };
}

/** Create, update and place the pin elements for `groups` at view `vb` (positions only). */
function drawPins(layer, groups, vb, width, height, names, onClick) {
  const kx = width / vb.w;
  const ky = height / vb.h;
  const keep = new Set();
  for (const g of groups) {
    keep.add(g.key);
    let el = layer.querySelector(`[data-key="${g.key}"]`);
    if (!el) {
      el = document.createElement('button');
      el.type = 'button';
      el.className = 'mp-pin mp-enter';
      el.dataset.key = g.key;
      el.dataset.side = 'right';
      el.innerHTML = '<span class="mp-dot"><b></b></span><span class="mp-label"></span>';
      el.addEventListener('click', (e) => { e.stopPropagation(); onClick(el._group, el); });
      layer.appendChild(el);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('mp-enter')));
    }
    el._group = g;
    const n = g.members.length;
    el.classList.toggle('many', n > 1);
    el.querySelector('b').textContent = n > 1 ? String(n) : ''; // every cluster shows its count
    const text = label(g, names);
    const lab = el.querySelector('.mp-label');
    if (lab.textContent !== text) lab.textContent = text;
    el.style.transform = `translate(${(g.xy[0] - vb.x) * kx}px, ${(g.xy[1] - vb.y) * ky}px)`;
  }
  layer.querySelectorAll('.mp-pin').forEach((el) => { if (!keep.has(el.dataset.key)) el.remove(); });
}

/**
 * Put each label on the first side of its dot where it overlaps no dot, no
 * label already placed and nothing in `avoid` (the inset box), inside the
 * area; the biggest clusters choose first. A label with no free side is left
 * off. Coordinates are the target view's, where the easing ends.
 */
function placeLabels(layer, groups, vb, width, height, avoid = []) {
  const kx = width / vb.w;
  const ky = height / vb.h;
  const at = new Map(groups.map((g) => [g.key, [(g.xy[0] - vb.x) * kx, (g.xy[1] - vb.y) * ky]]));
  const taken = [...avoid, ...groups.map((g) => { const [x, y] = at.get(g.key); const r = g.members.length > 1 ? DOT + 6 : DOT; return [x - r, y - r, x + r, y + r]; })];
  const hit = (a) => a[0] < 4 || a[1] < 4 || a[2] > width - 4 || a[3] > height - 4 ||
    taken.some((b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]);
  const order = [...groups].sort((a, b) => b.members.length - a.members.length);
  for (const g of order) {
    const el = layer.querySelector(`[data-key="${g.key}"]`);
    if (!el || el.hidden) continue;
    el.dataset.side = 'right'; // shown, so it can be measured
    const lab = el.querySelector('.mp-label');
    const w = lab.offsetWidth;
    const h = lab.offsetHeight;
    const [x, y] = at.get(g.key);
    const r = (g.members.length > 1 ? DOT + 6 : DOT) + 4;
    const d = r * 0.7; // the diagonals sit off the dot's corner
    const rect = {
      right: [x + r, y - h / 2, x + r + w, y + h / 2],
      left: [x - r - w, y - h / 2, x - r, y + h / 2],
      up: [x - w / 2, y - r - h, x + w / 2, y - r],
      down: [x - w / 2, y + r, x + w / 2, y + r + h],
      'up-right': [x + d, y - d - h, x + d + w, y - d],
      'up-left': [x - d - w, y - d - h, x - d, y - d],
      'down-right': [x + d, y + d, x + d + w, y + d + h],
      'down-left': [x - d - w, y + d, x - d, y + d + h],
    };
    const side = !w ? 'none' : SIDES.find((s) => !hit(rect[s])) || 'none';
    el.dataset.side = side;
    if (side !== 'none') taken.push(rect[side]);
  }
}

function popover(host, g, el) {
  host.querySelectorAll('.mp-pop').forEach((p) => p.remove());
  const pop = document.createElement('div');
  pop.className = 'mp-pop';
  pop.innerHTML = g.members.map((p) => `<div><b>${esc(p.sender)}</b> — ${esc(p.name)}, ${esc(p.country_name)}</div>`).join('');
  const r = el.getBoundingClientRect();
  const hr = host.getBoundingClientRect();
  const scale = hr.width / host.offsetWidth || 1;
  pop.style.left = `${(r.left - hr.left) / scale + 20}px`;
  pop.style.top = `${(r.top - hr.top) / scale + 20}px`;
  host.appendChild(pop);
}

export function render(body, result, ctx) {
  let st = body._mp;
  if (!st) {
    body.innerHTML =
      '<div class="mp"><div class="mp-area"><svg class="mp-land" preserveAspectRatio="none"></svg>' +
      '<svg class="mp-links"></svg><div class="mp-pins"></div><div class="mp-inset" hidden><div class="mp-inset-title"></div>' +
      '<div class="mp-inset-map"><svg class="mp-land" preserveAspectRatio="none"></svg><div class="mp-pins"></div></div></div></div>' +
      '<aside class="mp-side"><div class="mp-total"></div><div class="mp-top"></div></aside></div>';
    st = body._mp = { vb: null, target: null, anim: 0, pins: [], names: true, size: '', inset: null };
    const area = body.querySelector('.mp-area');
    area.addEventListener('click', () => area.querySelectorAll('.mp-pop').forEach((p) => p.remove()));
    loadWorld().then((paths) => {
      body.querySelectorAll('.mp-land').forEach((svg) => { svg.innerHTML = paths; });
    });
    // The area's size can settle after the first draw (fonts, the question's
    // height): aim again whenever it changes.
    new ResizeObserver(() => {
      const size = `${area.clientWidth}x${area.clientHeight}`;
      if (size !== st.size && st.pins) aim(body, st);
    }).observe(area);
  }
  const pins = ((result && result.pins) || []).map((p) => ({ ...p, xy: project(p.lat, p.lon) }));
  const names = ctx.names !== false;
  const sig = JSON.stringify([pins.map((p) => [p.id, p.lat, p.lon]), names]);
  drawSide(body, result);
  if (sig === st.sig) return; // the same pins: keep the view (and its animation) as it is
  st.sig = sig;
  st.pins = pins;
  st.names = names;
  aim(body, st);
}

/** The inset's corner: the one hiding the fewest pins (ties: farthest from its region), kept while still as good. */
function insetCorner(st, W, H, iw, ih, region) {
  const k = [W / st.target.w, H / st.target.h];
  const screen = st.pins.map((p) => [(p.xy[0] - st.target.x) * k[0], (p.xy[1] - st.target.y) * k[1]]);
  const corners = { tl: [16, 16], tr: [W - iw - 16, 16], bl: [16, H - ih - 16], br: [W - iw - 16, H - ih - 16] };
  const covered = ([l, t]) => screen.filter(([x, y]) => x > l - 40 && x < l + iw + 120 && y > t - 30 && y < t + ih + 30).length;
  const far = ([l, t]) => Math.hypot(l + iw / 2 - (region[0] + region[2] / 2), t + ih / 2 - (region[1] + region[3] / 2));
  const best = Object.keys(corners).reduce((a, b) => (covered(corners[b]) < covered(corners[a]) ||
    (covered(corners[b]) === covered(corners[a]) && far(corners[b]) > far(corners[a])) ? b : a));
  const prev = st.inset.corner;
  return prev && covered(corners[prev]) <= covered(corners[best]) ? prev : best;
}

/** Point the camera at the pins (eased from wherever it is now); lay out labels and the inset once, for the target. */
function aim(body, st) {
  const area = body.querySelector('.mp-area');
  const W = area.clientWidth || 1;
  const H = area.clientHeight || 1;
  st.size = `${W}x${H}`;
  st.target = fitBox(st.pins.map((p) => p.xy), W / H);
  st.from = st.vb && Math.abs(st.vb.w / st.vb.h - st.target.w / st.target.h) < 0.01 ? { ...st.vb } : { ...st.target };
  st.start = performance.now();
  st.inset = findInset(st.pins, st.target, st.inset);
  const layer = area.querySelector(':scope > .mp-pins');
  const groups = cluster(st.pins, st.target, W, H);
  const onClick = (g, el) => popover(area, g, el);

  // the inset's place, from the target view
  const box = area.querySelector('.mp-inset');
  let avoid = [];
  if (st.inset) {
    const ib = st.inset.box;
    const k = [W / st.target.w, H / st.target.h];
    const region = [(ib.x - st.target.x) * k[0], (ib.y - st.target.y) * k[1], ib.w * k[0], ib.h * k[1]];
    const iw = Math.min(W * 0.42, 560, (H * 0.62 - 44) * 1.5);
    const ih = iw / 1.5 + 44;
    st.inset.corner = insetCorner(st, W, H, iw, ih, region);
    const [left, top] = { tl: [16, 16], tr: [W - iw - 16, 16], bl: [16, H - ih - 16], br: [W - iw - 16, H - ih - 16] }[st.inset.corner];
    Object.assign(st.inset, { left, top, iw, ih });
    avoid = [[left - 8, top - 8, left + iw + 8, top + ih + 8]];
  }
  // pins and their labels at the target view (the frames below only move them)
  drawPins(layer, groups, st.target, W, H, st.names, onClick);
  const inside = new Set(st.inset ? st.inset.pins.map((p) => p.id) : []);
  layer.querySelectorAll('.mp-pin').forEach((el) => {
    el.hidden = inside.size > 0 && el._group.members.every((p) => inside.has(p.id)); // the inset draws those
  });
  placeLabels(layer, groups.filter((g) => !g.members.every((p) => inside.has(p.id))), st.target, W, H, avoid);
  drawInset(area, st, W, H, onClick, true);

  const frame = (now) => {
    const t = Math.min(1, (now - st.start) / EASE_MS);
    st.vb = lerpBox(st.from, st.target, ease(t));
    area.querySelector(':scope > .mp-land').setAttribute('viewBox', `${st.vb.x} ${st.vb.y} ${st.vb.w} ${st.vb.h}`);
    drawPins(layer, groups, st.vb, W, H, st.names, onClick);
    drawLinks(area, st, W, H);
    if (t < 1) st.anim = requestAnimationFrame(frame);
  };
  cancelAnimationFrame(st.anim);
  box.hidden = !st.inset;
  st.anim = requestAnimationFrame(frame);
}

/** The inset box: its map, pins and labels (laid out once per aim, `layout`). */
function drawInset(area, st, W, H, onClick, layout) {
  const box = area.querySelector('.mp-inset');
  if (!st.inset) {
    box.hidden = true;
    area.querySelector('.mp-links').innerHTML = '';
    return;
  }
  const { pins, box: ib, left, top, iw } = st.inset;
  box.hidden = false;
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
  box.style.width = `${iw}px`;
  box.querySelector('.mp-inset-title').innerHTML = `${ICON('users')} <b>${pins.length}</b> · ${esc([...new Set(pins.map((p) => p.country_name))].join(' · '))}`;
  const map = box.querySelector('.mp-inset-map');
  map.style.height = `${iw / 1.5}px`;
  map.querySelector('.mp-land').setAttribute('viewBox', `${ib.x} ${ib.y} ${ib.w} ${ib.h}`);
  const groups = cluster(pins, ib, iw, iw / 1.5);
  const layer = map.querySelector('.mp-pins');
  drawPins(layer, groups, ib, iw, iw / 1.5, st.names, onClick);
  if (layout) placeLabels(layer, groups, ib, iw, iw / 1.5);
}

/** The dashed frame on the main map around the inset's region, and the line to the inset. */
function drawLinks(area, st, W, H) {
  const links = area.querySelector('.mp-links');
  if (!st.inset) { links.innerHTML = ''; return; }
  const { box: ib, left, top, iw, ih } = st.inset;
  const kx = W / st.vb.w;
  const ky = H / st.vb.h;
  const rx = (ib.x - st.vb.x) * kx;
  const ry = (ib.y - st.vb.y) * ky;
  const rw = ib.w * kx;
  const rh = ib.h * ky;
  const ax = left + (left < rx ? iw : 0);
  const ay = top + ih / 2;
  const bx = left < rx ? rx : rx + rw;
  links.setAttribute('viewBox', `0 0 ${W} ${H}`);
  links.innerHTML = `<rect x="${rx}" y="${ry}" width="${rw}" height="${rh}" rx="10"/>` +
    `<line x1="${ax}" y1="${ay}" x2="${bx}" y2="${ry + rh / 2}"/>`;
}

function drawSide(body, result) {
  const side = body.querySelector('.mp-side');
  const r = result || { placed: 0, countries: 0, cities: [] };
  side.querySelector('.mp-total').innerHTML =
    `<div class="mp-big">${r.placed || 0}${ICON('users')}</div><div class="mp-sub">${ICON('map-pin')} <b>${r.countries || 0}</b></div>`;
  side.querySelector('.mp-top').innerHTML = (r.cities || []).slice(0, 3).map((c) =>
    `<div class="mp-city"><div class="mp-city-head"><b>${esc(c.name)}</b><span>${c.count}</span></div>` +
    `<div class="mp-city-names">${esc(c.names.map((n) => n.split(' ')[0]).join(', '))}</div></div>`).join('');
}
