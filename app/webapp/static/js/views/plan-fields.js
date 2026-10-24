// Plan tab: the item editor's fields — the generic rows and controls (field,
// segmentedChoice, input, selectField) and each item's font, other-text roles, timer,
// music and notes. plan.js keeps the list, selection, editor shell and preview,
// and binds the per-item fields to its state and redraws through planFields().

import { icon } from '/static/_vendored/icons/icons.js';
import { api, esc, toast, switchRow } from '/static/js/ui.js';
import { ROLES, TEXT_FONTS, sessionRole, fontName } from '/static/js/lettering.js';

const FONTS = [
  ['theme', 'Session font (Sessions → Stage font)'],
  ['system-ui', 'System sans'],
  ['Georgia', 'Georgia (serif)'],
  ['Segoe Print', 'Segoe Print (handwriting)'],
];
const TIMER_START = { manual: 'When I start it', on_enter: 'When the item opens', with_capture: 'With the capture' };
const TIMER_END = { keep: 'Keep showing 00:00', hide: 'Remove the timer', stop_capture: 'Stop the capture', advance: 'Go to the next item', chime: 'Play a chime' };
const MUSIC_START = { with_timer: 'With the timer', on_enter: 'When the item opens', manual: 'Only from the presenter' };
const SPOTIFY_LINK = /^(spotify:(playlist|album|artist|track|show|episode):[A-Za-z0-9]+|https?:\/\/open\.spotify\.com\/(intl-[A-Za-z-]+\/)?(playlist|album|artist|track|show|episode)\/[A-Za-z0-9]+)/;
const MUSIC_LEAVE = { fade_out: 'Fade out and stop', keep_playing: 'Keep playing' };
const MUSIC_TIMER_END = { fade_out: 'Fade out and stop', keep_playing: 'Keep playing until I stop it' };

/** A timer's length as the Plan tab shows and edits it: "m:ss" ("5:00"), read back by parseTimer. */
export function fmtTimer(sec) {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function parseTimer(text) {
  const t = String(text).trim();
  const m = t.match(/^(\d+)(?::(\d{1,2}))?$/);
  if (!m) return null;
  return m[2] === undefined ? parseInt(m[1], 10) * 60 : parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}

// The fades a newly switched-on music item starts with: Settings → Music.
let musicDefaults = { fade_in_s: 2, fade_out_s: 2 };
export function loadMusicDefaults() {
  api('/api/settings/defaults').then((d) => { musicDefaults = d.music; }).catch(() => { /* keep the last known */ });
}

// ---------------------------------------------------------------- rows and controls

export function field(label, control, extraClass = '') {
  const row = document.createElement('div');
  row.className = 'ed-row ' + extraClass;
  const l = document.createElement('div');
  l.className = 'ed-label';
  l.textContent = label;
  const c = document.createElement('div');
  c.className = 'ed-control';
  if (typeof control === 'string') c.innerHTML = control; else c.appendChild(control);
  row.append(l, c);
  return row;
}

export function segmentedChoice(options, value, onPick, label) {
  const group = document.createElement('div');
  group.className = 'segmented ed-tabs';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', label);
  options.forEach(([v, text, trailing]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'segmented-item';
    b.textContent = text;
    if (trailing) b.insertAdjacentHTML('beforeend', icon(trailing));
    b.setAttribute('aria-pressed', String(v === value));
    b.addEventListener('click', () => onPick(v));
    group.appendChild(b);
  });
  return group;
}

export function input(value, onChange, attrs = {}) {
  const el = document.createElement(attrs.textarea ? 'textarea' : 'input');
  el.className = 'input';
  if (!attrs.textarea) el.type = attrs.type || 'text';
  el.value = value == null ? '' : value;
  Object.entries(attrs).forEach(([k, v]) => { if (k !== 'textarea' && k !== 'type') el.setAttribute(k, v); });
  el.addEventListener('input', () => onChange(el.value, el));
  return el;
}

/** A native <select>: `options` as `[value, label]` pairs (an array, or `Object.entries(map)`),
 *  the option matching `value` marked selected (compared as strings, so a numeric choice still
 *  matches); `onChange` gets the matching option's own `value` back, not the raw string, so a
 *  numeric choice keeps its type. */
export function selectField(label, options, value, onChange) {
  const sel = document.createElement('select');
  sel.className = 'select-native';
  sel.setAttribute('aria-label', label);
  options.forEach(([v, l]) => {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = l;
    o.selected = String(v) === String(value);
    sel.appendChild(o);
  });
  sel.addEventListener('change', () => {
    const picked = options.find(([v]) => String(v) === sel.value);
    onChange(picked ? picked[0] : sel.value);
  });
  return sel;
}

/** A new item font block. An activity title (72) seeds no size, so it keeps following the session's;
 *  a break's title (120) and the like seed theirs, as they always did. */
export const fontSeed = (defaultSize) => (defaultSize === 72 ? { family: 'theme' } : { family: 'theme', size_px: defaultSize || 72 });

// ---------------------------------------------------------------- per-item fields

/**
 * The per-item fields, bound to the Plan tab: `st` (the session being edited),
 * `markDirty` (an edit happened), the redraws `updatePreview`, `renderList` and
 * `renderEditor`, and `slideOf` (an item's imported slide).
 */
export function planFields({ st, markDirty, updatePreview, renderList, renderEditor, slideOf }) {
  /** The session's own title size (Sessions → Stage look): what an activity's title is until the item sets one. */
  const sessionTitleSize = () => (st.session.font && st.session.font.title_size) || 72;
  /** The size a font row shows for an item with none of its own: an activity title follows the session's. */
  const shownSize = (defaultSize) => (defaultSize === 72 ? sessionTitleSize() : defaultSize);

  /**
   * An item's own lettering — font, size (stage px on the 1920-wide canvas; none
   * for a slide's text, which keeps its PowerPoint sizes) and capitals. Each
   * starts as the session's (Sessions → Stage font): setting one here is the
   * exception for this item only.
   */
  function fontField(it, label, defaultSize) {
    const font = it.font || { family: 'theme' };
    const set = (change) => {
      it.font = Object.assign(fontSeed(defaultSize), it.font, change);
      markDirty();
      updatePreview(it);
    };
    const row = document.createElement('div');
    row.className = 'inline-controls font-controls';
    const famValue = font.family === 'Patrick Hand' ? 'theme' : font.family;
    const fam = selectField(label, FONTS, famValue, (v) => set({ family: v }));
    row.appendChild(fam);
    if (defaultSize) {
      const size = input(font.size_px || shownSize(defaultSize), (v) => { const n = parseInt(v, 10); if (n >= 12 && n <= 240) set({ size_px: n }); }, { type: 'number', min: '12', max: '240', 'aria-label': 'Font size in stage px (1920 wide)' });
      size.classList.add('size-input');
      row.append(size, Object.assign(document.createElement('span'), { className: 'muted small', textContent: 'px' }));
    }
    const sessionCaps = !(st.session.font && st.session.font.caps === false);
    const capsValue = font.caps === true ? 'caps' : font.caps === false ? 'typed' : '';
    const caps = selectField('Capitals',
      [['', `Capitals as the session (${sessionCaps ? 'ALL CAPS' : 'as typed'})`], ['caps', 'ALL CAPS'], ['typed', 'As typed']],
      capsValue,
      (v) => set({ caps: v === 'caps' ? true : v === 'typed' ? false : null }));
    row.appendChild(caps);
    return field(label, row);
  }

  /**
   * An item's other text — the chat hint, the answers, a subtitle, a slide's
   * text — in the session's lettering, or its own font and capitals (it.font.roles).
   */
  function rolesField(it, keys, defaultSize) {
    const wrap = document.createElement('div');
    wrap.className = 'role-rows';
    const own = (it.font || {}).roles || {};
    const setRole = (key, change) => {
      const f = Object.assign(fontSeed(defaultSize), it.font);
      const roles = Object.assign({}, f.roles);
      const mine = Object.assign({}, roles[key], change);
      if (!mine.font) delete mine.font;
      if (mine.caps !== true && mine.caps !== false) delete mine.caps;
      if (Object.keys(mine).length) roles[key] = mine; else delete roles[key];
      if (Object.keys(roles).length) f.roles = roles; else delete f.roles;
      it.font = f;
      markDirty();
      updatePreview(it);
    };
    keys.forEach((key) => {
      const role = ROLES.find((r) => r.key === key);
      const session = sessionRole(st.session.font, key);
      const mine = own[key] || {};
      const row = document.createElement('div');
      row.className = 'role-row';
      row.dataset.role = key;
      row.appendChild(Object.assign(document.createElement('span'), { className: 'small', textContent: role.label }));
      const fonts = [['', `As the session (${fontName(session.font)})`], ['title', 'Title font'], ['text', 'Text font'],
        ...TEXT_FONTS.filter(([v]) => v)];
      if (mine.font && !fonts.some(([v]) => v === mine.font)) fonts.push([mine.font, mine.font]);
      row.appendChild(selectField(`${role.label}: font`, fonts, mine.font || '', (v) => setRole(key, { font: v })));
      row.appendChild(selectField(`${role.label}: capitals`,
        [['', `As the session (${session.caps ? 'ALL CAPS' : 'as typed'})`], ['caps', 'ALL CAPS'], ['typed', 'As typed']],
        mine.caps === true ? 'caps' : mine.caps === false ? 'typed' : '',
        (v) => setRole(key, { caps: v === 'caps' ? true : v === 'typed' ? false : null })));
      wrap.appendChild(row);
    });
    return field('Other text', wrap);
  }

  /** The presenter's notes. A slide starts from its PowerPoint notes; editing them keeps the deck as it is. */
  function notesField(it) {
    const deck = it.kind === 'slide' ? ((slideOf(it) || {}).notes || '') : '';
    const box = input(it.notes || deck, (v) => { it.notes = v === deck ? '' : v; markDirty(); }, { textarea: true, rows: '4', 'aria-label': 'Notes',
      placeholder: it.kind === 'slide' ? 'No speaker notes in the deck — write yours here' : 'What to say or do on this item' });
    box.classList.add('notes-input');
    const row = field('Notes', box, 'with-hint');
    row.querySelector('.ed-control').insertAdjacentHTML('beforeend', `<p class="ed-hint">${it.kind === 'slide'
      ? (it.notes ? 'Edited here — the PowerPoint notes stay in the deck. Clear the box to get them back.' : 'From your PowerPoint. Editing them here leaves the deck as it is.')
      : 'Shown on the presenter while this item is on stage.'}</p>`);
    return row;
  }

  function timerField(it) {
    const wrap = document.createElement('div');
    wrap.className = 'timer-box';
    const on = !!(it.timer && it.timer.enabled);
    const isAct = it.kind === 'activity';
    wrap.appendChild(switchRow(on, on ? '' : 'No timer on this item', (next) => {
      if (next) {
        const long = it.kind === 'break' || it.kind === 'breakout';
        it.timer = Object.assign({ seconds: isAct ? 180 : long ? 600 : 120, start: isAct ? 'with_capture' : 'manual', show_on: isAct || long ? 'stage' : 'presenter', end: isAct ? 'stop_capture' : 'keep' }, it.timer || {}, { enabled: true });
      } else if (it.timer) {
        it.timer.enabled = false;
      }
      markDirty();
      renderList();
      renderEditor();
    }, 'Timer on this item'));
    if (on) {
      const t = it.timer;
      const dur = input(fmtTimer(t.seconds), (v, el) => {
        const s = parseTimer(v);
        el.classList.toggle('invalid', s == null || s < 5);
        if (s != null && s >= 5) { t.seconds = s; markDirty(); }
      }, { 'aria-label': 'Timer duration (m:ss)', placeholder: '3:00' });
      dur.classList.add('dur-input');
      wrap.querySelector('.switch-line').appendChild(dur);
      const grid = document.createElement('div');
      grid.className = 'timer-grid';
      const sel = (label, map, value, onPick, skip = []) => {
        const options = Object.entries(map).filter(([k]) => !skip.includes(k));
        const s = selectField(label, options, value, (v) => { onPick(v); markDirty(); });
        const line = document.createElement('label');
        line.className = 'opt-line';
        line.append(Object.assign(document.createElement('span'), { className: 'small', textContent: label }), s);
        return line;
      };
      grid.appendChild(sel('Starts', TIMER_START, t.start, (v) => { t.start = v; }, isAct ? [] : ['with_capture']));
      grid.appendChild(sel('Shows on', { stage: 'Stage', presenter: 'Presenter only', both: 'Stage and presenter' }, t.show_on, (v) => { t.show_on = v; }));
      grid.appendChild(sel('At the end', TIMER_END, t.end, (v) => { t.end = v; }, isAct ? [] : ['stop_capture']));
      wrap.appendChild(grid);
    }
    return field('Timer', wrap);
  }

  /** The item's music: a file copied into the session's audio/, synced to the item's timer. */
  function musicField(it) {
    const wrap = document.createElement('div');
    wrap.className = 'music-box';
    const on = !!(it.music && it.music.enabled !== false);
    wrap.appendChild(switchRow(on, on ? '' : 'No music on this item', (next) => {
      if (next) {
        it.music = Object.assign({ source: 'file', path: '', volume: 80, fade_in_s: musicDefaults.fade_in_s, fade_out_s: musicDefaults.fade_out_s,
          loop: false, start: 'with_timer', on_leave: 'fade_out', on_timer_end: 'fade_out' },
          it.music || {}, { enabled: true });
      } else if (it.music) {
        it.music.enabled = false;
      }
      markDirty();
      renderList();
      renderEditor();
    }, 'Music on this item'));
    if (!on) return field('Music', wrap);
    const m = it.music;
    wrap.appendChild(segmentedChoice([['file', 'File'], ['spotify', 'Spotify']], m.source || 'file', (v) => {
      m.source = v;
      markDirty();
      renderList();
      renderEditor();
    }, 'Music source'));
    if (m.source === 'spotify') {
      const link = input(m.uri, (v, el) => {
        const ok = SPOTIFY_LINK.test(v.trim());
        el.classList.toggle('invalid', v.trim() !== '' && !ok);
        m.uri = v.trim();
        markDirty();
      }, { placeholder: 'https://open.spotify.com/playlist/…', 'aria-label': 'Spotify link' });
      wrap.appendChild(link);
      wrap.insertAdjacentHTML('beforeend', '<p class="ed-hint">A playlist, album, artist or track link (Share → Copy link) — it plays on the Spotify desktop app of this PC (Premium; README → Music → Spotify setup).</p>');
    }
    const src = document.createElement('div');
    src.className = 'music-src';
    src.hidden = m.source === 'spotify';
    src.innerHTML = `<span class="music-file${m.path ? '' : ' muted'}">${icon('music')}<span>${esc(m.path || 'No file yet')}</span></span>` +
      `<button type="button" class="button-surface" data-pick>${icon('folder-open')} Choose file…</button>`;
    src.querySelector('[data-pick]').addEventListener('click', async () => {
      try {
        const picked = await api('/api/pick', { method: 'POST', body: { kind: 'audio' } });
        if (!picked.path) return;
        const res = await api(`/api/sessions/${st.sid}/audio`, { method: 'POST', body: { path: picked.path } });
        Object.assign(m, { source: 'file', path: res.path });
        markDirty();
        renderList();
        renderEditor();
        toast(`${res.name} is in the session's audio folder`);
      } catch (e) {
        toast(e.message, 'error');
      }
    });
    wrap.appendChild(src);
    if (m.source !== 'spotify') {
      const hint = document.createElement('p');
      hint.className = 'ed-hint';
      hint.textContent = 'The file is copied into the session folder (audio/): mp3, wav, ogg or flac.';
      wrap.appendChild(hint);
    }
    if (m.start === 'with_timer' && !(it.timer && it.timer.enabled)) {
      wrap.insertAdjacentHTML('beforeend', '<p class="ed-hint warn">This item has no timer — the music starts with it. Add a timer, or start the music when the item opens.</p>');
    }

    const grid = document.createElement('div');
    grid.className = 'timer-grid';
    const line = (label, control) => {
      const l = document.createElement('label');
      l.className = 'opt-line';
      l.append(Object.assign(document.createElement('span'), { className: 'small', textContent: label }), control);
      return l;
    };
    const vol = document.createElement('span');
    vol.className = 'music-volume';
    vol.innerHTML = `<input type="range" min="0" max="100" step="5" aria-label="Music volume"><output class="small"></output>`;
    const range = vol.querySelector('input');
    range.value = String(m.volume);
    vol.querySelector('output').textContent = `${m.volume}%`;
    range.addEventListener('input', () => { m.volume = Number(range.value); vol.querySelector('output').textContent = `${m.volume}%`; markDirty(); });
    grid.appendChild(line('Volume', vol));
    const secs = (key, label) => {
      const el = input(m[key], (v, e) => {
        const n = Number(v);
        const ok = v !== '' && Number.isFinite(n) && n >= 0 && n <= 60;
        e.classList.toggle('invalid', !ok);
        if (ok) { m[key] = n; markDirty(); }
      }, { type: 'number', min: '0', max: '60', step: '0.5', 'aria-label': label });
      return line(label, el);
    };
    grid.appendChild(secs('fade_in_s', 'Fade in (s)'));
    grid.appendChild(secs('fade_out_s', 'Fade out (s)'));
    const sel = (label, map, key, rerender) => {
      const s = selectField(label, Object.entries(map), m[key], (v) => { m[key] = v; markDirty(); if (rerender) renderEditor(); });
      return line(label, s);
    };
    grid.appendChild(sel('Starts', MUSIC_START, 'start', true));
    const timed = !!(it.timer && it.timer.enabled);
    if (timed) grid.appendChild(sel('When the timer ends', MUSIC_TIMER_END, 'on_timer_end', true));
    grid.appendChild(sel('When leaving the item', MUSIC_LEAVE, 'on_leave', false));
    wrap.appendChild(grid);
    if (m.source !== 'spotify') wrap.appendChild(switchRow(!!m.loop, 'Start over at the end (loop)', (next) => { m.loop = next; markDirty(); }));
    const how = document.createElement('p');
    how.className = 'ed-hint';
    how.textContent = !timed ? 'With no timer, the music stops from the presenter, or when you leave the item (as set above).'
      : 'Pausing the timer fades the music out and pauses it; a reset fades it out and stops it. '
        + (m.on_timer_end === 'keep_playing' ? 'At 00:00 the music keeps playing until you stop it (Stop, in the presenter).' : 'At 00:00 it fades out and stops.');
    wrap.appendChild(how);
    return field('Music', wrap);
  }

  return { fontField, rolesField, notesField, timerField, musicField };
}
