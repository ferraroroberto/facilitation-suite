// The magic map on the stage (epic §10).
//
// The world outline is an SVG in an equirectangular projection with a 35°
// standard parallel (x = lon·cos35°·10, y = −lat·10, see scripts/build_geo.py).
// Pins are HTML on top, so labels keep their size while the map zooms.
//
// - auto-fit: the view eases to the pins' bounding box (plus padding, never
//   closer than a country-sized span), re-aimed as people arrive;
// - clusters: pins closer than a few pixels merge into one with a count;
// - inset: when a small region holds many people while the view spans far
//   more, that region gets its own zoomed box in the emptiest corner, joined
//   to a dashed frame on the main map;
// - click a pin or a cluster: who is there, with city and country.

import { esc } from '/static/js/ui.js';

const KX = Math.cos((35 * Math.PI) / 180);
const S = 10;
const WORLD = { x: -170 * KX * S, y: -80 * S, w: 360 * KX * S, h: 138 * S }; // Antarctica left out
const MIN_SPAN = 14 * S; // never zoom closer than ~14° of longitude
const CLUSTER_PX = 34;
const EASE_MS = 900;

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
  let x0 = Math.min(...points.map((p) => p[0]));
  let x1 = Math.max(...points.map((p) => p[0]));
  let y0 = Math.min(...points.map((p) => p[1]));
  let y1 = Math.max(...points.map((p) => p[1]));
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
    g.key = g.members.map((p) => p.id).sort((a, b) => a - b).join('-');
  }
  return groups;
}

function label(g, names) {
  const m = g.members;
  if (!names) {
    const cities = [...new Set(m.map((p) => p.name))];
    return cities.length === 1 ? cities[0] : `${cities[0]} +${cities.length - 1}`;
  }
  if (m.length <= 3) return m.map((p) => p.sender.split(' ')[0]).join(' · ');
  const cities = [...new Set(m.map((p) => p.name))];
  return cities.length === 1 ? cities[0] : `${m.length}`;
}

/** The densest small region worth an inset, or null. */
function findInset(pins, vb) {
  if (pins.length < 6) return null;
  let best = null;
  for (const p of pins) {
    const near = pins.filter((q) => Math.abs(q.xy[0] - p.xy[0]) < 7 * S && Math.abs(q.xy[1] - p.xy[1]) < 5 * S);
    if (!best || near.length > best.length) best = near;
  }
  if (!best || best.length < Math.max(5, pins.length * 0.3)) return null;
  const box = fitBox(best.map((p) => p.xy), 1.5, 0.25);
  return vb.w > box.w * 3.2 ? { pins: best, box } : null;
}

function lerpBox(a, b, t) {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, w: a.w + (b.w - a.w) * t, h: a.h + (b.h - a.h) * t };
}

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
      el.innerHTML = '<span class="mp-dot"><b></b></span><span class="mp-label"></span>';
      el.addEventListener('click', (e) => { e.stopPropagation(); onClick(el._group, el); });
      layer.appendChild(el);
      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('mp-enter')));
    }
    el._group = g;
    el.classList.toggle('many', g.members.length > 1);
    el.querySelector('b').textContent = g.members.length > 3 ? String(g.members.length) : '';
    el.querySelector('.mp-label').textContent = label(g, names);
    const x = (g.xy[0] - vb.x) * kx;
    el.classList.toggle('flip', x > width - 220); // near the right edge the label goes left
    el.style.transform = `translate(${x}px, ${(g.xy[1] - vb.y) * ky}px)`;
  }
  layer.querySelectorAll('.mp-pin').forEach((el) => { if (!keep.has(el.dataset.key)) el.remove(); });
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
    st = body._mp = { vb: null, target: null, anim: 0, pins: [], names: true, size: '' };
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
  st.pins = ((result && result.pins) || []).map((p) => ({ ...p, xy: project(p.lat, p.lon) }));
  st.names = ctx.names !== false;
  aim(body, st);
  drawSide(body, result);
}

/** Point the camera at the pins (eased from wherever it is now). */
function aim(body, st) {
  const area = body.querySelector('.mp-area');
  const W = area.clientWidth || 1;
  const H = area.clientHeight || 1;
  st.size = `${W}x${H}`;
  st.target = fitBox(st.pins.map((p) => p.xy), W / H);
  st.from = st.vb && st.vb.w / st.vb.h === st.target.w / st.target.h ? { ...st.vb } : { ...st.target };
  st.start = performance.now();
  st.inset = findInset(st.pins, st.target);
  const frame = (now) => {
    const t = Math.min(1, (now - st.start) / EASE_MS);
    st.vb = lerpBox(st.from, st.target, ease(t));
    const land = area.querySelector(':scope > .mp-land');
    land.setAttribute('viewBox', `${st.vb.x} ${st.vb.y} ${st.vb.w} ${st.vb.h}`);
    const groups = cluster(st.pins, st.target, W, H);
    drawPins(area.querySelector(':scope > .mp-pins'), groups, st.vb, W, H, st.names, (g, el) => popover(area, g, el));
    drawInset(area, st, W, H);
    if (t < 1) st.anim = requestAnimationFrame(frame);
  };
  cancelAnimationFrame(st.anim);
  st.anim = requestAnimationFrame(frame);
}

function drawInset(area, st, W, H) {
  const box = area.querySelector('.mp-inset');
  const links = area.querySelector('.mp-links');
  if (!st.inset) {
    box.hidden = true;
    links.innerHTML = '';
    return;
  }
  const { pins, box: ib } = st.inset;
  const kx = W / st.vb.w;
  const ky = H / st.vb.h;
  // the region on the main map
  const rx = (ib.x - st.vb.x) * kx;
  const ry = (ib.y - st.vb.y) * ky;
  const rw = ib.w * kx;
  const rh = ib.h * ky;
  // the inset goes in the corner farthest from the region, and always fits the area
  const iw = Math.min(W * 0.42, 560, (H * 0.62 - 44) * 1.5);
  const ih = iw / 1.5 + 44;
  // the corner that hides the fewest pins (ties: the one farthest from the region)
  const k2 = [W / st.vb.w, H / st.vb.h];
  const screen = st.pins.map((p) => [(p.xy[0] - st.vb.x) * k2[0], (p.xy[1] - st.vb.y) * k2[1]]);
  const corners = [[16, 16], [W - iw - 16, 16], [16, H - ih - 16], [W - iw - 16, H - ih - 16]];
  const score = ([l, t]) => screen.filter(([x, y]) => x > l - 40 && x < l + iw + 120 && y > t - 30 && y < t + ih + 30).length * 1e6
    - Math.hypot(l + iw / 2 - (rx + rw / 2), t + ih / 2 - (ry + rh / 2));
  const [left, top] = corners.reduce((a, b) => (score(b) < score(a) ? b : a));
  box.hidden = false;
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
  box.style.width = `${iw}px`;
  box.querySelector('.mp-inset-title').innerHTML = `${ICON('users')} <b>${pins.length}</b> · ${esc([...new Set(pins.map((p) => p.country_name))].join(' · '))}`;
  const map = box.querySelector('.mp-inset-map');
  map.style.height = `${iw / 1.5}px`;
  map.querySelector('.mp-land').setAttribute('viewBox', `${ib.x} ${ib.y} ${ib.w} ${ib.h}`);
  const groups = cluster(pins, ib, iw, iw / 1.5);
  drawPins(map.querySelector('.mp-pins'), groups, ib, iw, iw / 1.5, st.names, (g, el) => popover(area, g, el));
  // main-map pins inside the region are drawn by the inset instead
  const inside = new Set(pins.map((p) => p.id));
  area.querySelectorAll(':scope > .mp-pins .mp-pin').forEach((el) => {
    el.hidden = el._group.members.every((p) => inside.has(p.id));
  });
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
