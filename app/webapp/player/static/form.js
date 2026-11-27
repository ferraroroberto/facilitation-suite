// /play/form?t=<token> — a feedback form on a phone: every question on one scroll, an optional
// name, Send. After sending, a thank-you and "Send another response" (each send is its own row).
// The same page opens from the stage's QR code, from the link pasted in the meeting chat and from
// the room view (play.js → drawFormLink). Answers go to the server only; nothing comes back but
// the acknowledgement (src/forms/service.py).

const THEME = 'facilitation-suite.play.theme';
const PHONE = 'facilitation-suite.form.phone'; // this phone's own id: its rate limit, never shown
const SEND_TRIES = 4;
const SEND_BASE_MS = 600;

// Every word the page says, in the session's language. form.html's own words carry data-t="<key>"
// (and data-t-placeholder / data-t-label); tests/quiz/test_quiz_language.py checks both languages.
const TEXT = {
  en: {
    title: 'Feedback',
    loading: 'Loading…',
    theme: 'Switch light or dark',
    name_label: 'Your name (optional)',
    name_placeholder: 'Leave empty to stay anonymous',
    send: 'Send',
    sending: 'Sending…',
    private: 'Your answers go only to the host. They are never shown on the screen.',
    thanks: 'Thank you!',
    thanks_detail: 'Your answers were sent.',
    again: 'Send another response',
    closed: 'This form is closed',
    closed_detail: 'The host is not taking responses right now. Thank you for your time.',
    required: 'Required',
    your_answer: 'Your answer',
    your_number: 'A number',
    stars: (n) => `${n} star${n === 1 ? '' : 's'}`,
    check: 'Some answers need a look.',
    unreachable: "Can't reach the form — check your connection and try again.",
    too_many: 'Too many tries — wait a moment and try again.',
    failed: 'That did not work — try again.',
  },
  es: {
    title: 'Tu opinión',
    loading: 'Cargando…',
    theme: 'Cambiar a modo claro u oscuro',
    name_label: 'Tu nombre (opcional)',
    name_placeholder: 'Déjalo vacío para responder de forma anónima',
    send: 'Enviar',
    sending: 'Enviando…',
    private: 'Tus respuestas solo las ve quien presenta. Nunca salen en la pantalla.',
    thanks: '¡Gracias!',
    thanks_detail: 'Tus respuestas se han enviado.',
    again: 'Enviar otra respuesta',
    closed: 'Este formulario está cerrado',
    closed_detail: 'Ahora no se aceptan respuestas. Gracias por tu tiempo.',
    required: 'Obligatoria',
    your_answer: 'Tu respuesta',
    your_number: 'Un número',
    stars: (n) => `${n} estrella${n === 1 ? '' : 's'}`,
    check: 'Revisa algunas respuestas.',
    unreachable: 'No se puede llegar al formulario: revisa tu conexión y vuelve a intentarlo.',
    too_many: 'Demasiados intentos: espera un momento y vuelve a intentarlo.',
    failed: 'No ha funcionado: vuelve a intentarlo.',
  },
};

let T = TEXT.en;
const $ = (sel) => document.querySelector(sel);
const main = $('.play');
const token = new URLSearchParams(location.search).get('t') || '';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let form = null; // {title, intro, questions}
let picked = {}; // question id → the value a button control holds
let key = newKey(); // this submission's retry key: a resend is stored once

function phoneId() {
  try {
    let id = localStorage.getItem(PHONE);
    if (!id) { id = newKey(); localStorage.setItem(PHONE, id); }
    return id;
  } catch { return null; } // private mode: the address's limit still applies
}

function setLang(l) {
  T = TEXT[l] || TEXT.en;
  document.documentElement.lang = TEXT[l] ? l : 'en';
  document.querySelectorAll('[data-t]').forEach((el) => { el.textContent = T[el.dataset.t]; });
  document.querySelectorAll('[data-t-placeholder]').forEach((el) => { el.placeholder = T[el.dataset.tPlaceholder]; });
  document.querySelectorAll('[data-t-label]').forEach((el) => { el.setAttribute('aria-label', T[el.dataset.tLabel]); });
}

// ------------------------------------------------------------------ drawing

function controlHtml(q) {
  const id = `fm-${esc(q.id)}`;
  if (q.type === 'text') return `<textarea class="play-input fm-text" id="${id}" maxlength="1000" aria-label="${esc(T.your_answer)}"></textarea>`;
  if (q.type === 'number') {
    return `<span class="play-number"><input class="play-input" id="${id}" inputmode="decimal" autocomplete="off" aria-label="${esc(T.your_number)}">`
      + `<span class="play-unit">${esc(q.unit || '')}</span></span>`;
  }
  if (q.type === 'choice') {
    return `<div class="play-choices" role="group">${(q.choices || []).map((c, i) => {
      const k = String.fromCharCode(65 + i);
      return `<button type="button" class="play-choice-btn" data-pick="${k}" aria-pressed="false"><b>${k}</b><span>${esc(c)}</span></button>`;
    }).join('')}</div>`;
  }
  const nums = [];
  for (let n = q.min; n <= q.max; n++) nums.push(n);
  const stars = q.type === 'stars';
  const ends = (q.low || q.high) ? `<div class="fm-ends"><span>${esc(q.low || '')}</span><span>${esc(q.high || '')}</span></div>` : '';
  return `<div class="play-scale" role="group"${stars ? ' data-stars' : ''}>${nums.map((n) => `<button type="button" class="play-scale-btn" data-pick="${n}" aria-pressed="false"`
    + `${stars ? ` aria-label="${esc(T.stars(n))}"><svg class="icon" aria-hidden="true"><use href="#i-star"/></svg>` : `>${n}`}</button>`).join('')}</div>${ends}`;
}

function drawForm() {
  $('[data-form-heading]').textContent = form.title || T.title;
  document.title = form.title || T.title;
  $('[data-form-intro]').textContent = form.intro || '';
  $('[data-questions]').innerHTML = form.questions.map((q, i) => `<div class="card fm-q" data-q="${esc(q.id)}" data-type="${esc(q.type)}">`
    + `<p class="fm-qtext"><span>${i + 1}. ${esc(q.text)}</span>${q.required ? ` <span class="fm-req" title="${esc(T.required)}">*</span>` : ''}</p>`
    + `${controlHtml(q)}<p class="play-error" data-q-error hidden></p></div>`).join('');
  picked = {};
  main.dataset.screen = 'form';
}

function paintPicks(box) {
  const q = form.questions.find((x) => x.id === box.dataset.q);
  const value = picked[q.id];
  box.querySelectorAll('[data-pick]').forEach((b) => {
    const v = b.dataset.pick;
    const on = q.type === 'choice' ? (value || []).includes(v) : String(value) === v;
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    if (q.type === 'stars') b.toggleAttribute('data-on', value != null && Number(v) <= Number(value));
  });
}

function onPick(e) {
  const b = e.target.closest('[data-pick]');
  if (!b) return;
  const box = b.closest('[data-q]');
  const q = form.questions.find((x) => x.id === box.dataset.q);
  const v = b.dataset.pick;
  if (q.type === 'choice') {
    const now = picked[q.id] || [];
    picked[q.id] = now.includes(v) ? now.filter((x) => x !== v) : (q.multiple ? [...now, v] : [v]);
  } else {
    picked[q.id] = String(picked[q.id]) === v ? null : Number(v); // a second tap takes the answer back
  }
  paintPicks(box);
}

function answers() {
  const out = {};
  form.questions.forEach((q) => {
    if (q.type === 'text' || q.type === 'number') {
      const v = $(`#fm-${CSS.escape(q.id)}`).value.trim();
      if (v) out[q.id] = v;
    } else if (q.type === 'choice') {
      if ((picked[q.id] || []).length) out[q.id] = picked[q.id];
    } else if (picked[q.id] != null) {
      out[q.id] = picked[q.id];
    }
  });
  return out;
}

function showErrors(errors, message) {
  document.querySelectorAll('[data-q]').forEach((box) => {
    const err = errors[box.dataset.q];
    box.toggleAttribute('data-bad', !!err);
    const p = box.querySelector('[data-q-error]');
    p.hidden = !err;
    p.textContent = err || '';
  });
  const top = $('#formError');
  top.hidden = !message;
  top.textContent = message || '';
  const first = document.querySelector('[data-q][data-bad]');
  if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ------------------------------------------------------------------ sending

async function post(body) {
  const r = await fetch('/play/api/form', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store' });
  let data = null;
  try { data = await r.json(); } catch { /* an empty or HTML body: a proxy error */ }
  return { status: r.status, data };
}

async function send(ev) {
  ev.preventDefault();
  const btn = $('#formSend');
  btn.disabled = true;
  btn.textContent = T.sending;
  const body = { t: token, name: $('#fmName').value.trim(), answers: answers(), key, phone: phoneId() };
  let res = null;
  for (let attempt = 0; attempt < SEND_TRIES; attempt++) {
    try {
      res = await post(body);
      if (res.status < 500) break;
    } catch { res = null; } // offline or the tunnel dropped: the same key makes the resend safe
    await sleep(SEND_BASE_MS * 2 ** attempt);
  }
  btn.disabled = false;
  btn.textContent = T.send;
  const err = res && res.data && res.data.error;
  if (res && res.status === 200 && res.data.state === 'accepted') {
    main.dataset.screen = 'thanks';
    window.scrollTo(0, 0);
  } else if (res && res.status === 410) {
    main.dataset.screen = 'closed';
  } else if (res && res.status === 422 && err && err.detail) {
    showErrors(err.detail.errors || {}, err.message || T.check);
  } else {
    showErrors({}, !res ? T.unreachable : res.status === 429 ? T.too_many : T.failed);
  }
}

function again() {
  key = newKey();
  drawForm();
  showErrors({}, '');
  window.scrollTo(0, 0);
}

// ------------------------------------------------------------------ start

$('[data-theme-toggle]').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(THEME, next); } catch { /* this tab only */ }
});
$('[data-questions]').addEventListener('click', onPick);
$('#feedbackForm').addEventListener('submit', send);
$('[data-again]').addEventListener('click', again);

fetch(`/play/api/form?t=${encodeURIComponent(token)}`, { cache: 'no-store' })
  .then((r) => r.json())
  .then((d) => {
    setLang(d.lang);
    if (d.state !== 'open') { main.dataset.screen = 'closed'; return; }
    form = d.form;
    drawForm();
  })
  .catch(() => { main.dataset.screen = 'loading'; $('[data-for="loading"] p').textContent = T.unreachable; });
