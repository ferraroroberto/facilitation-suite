// Plan tab: a feedback form's builder, in the editor of a "Feedback form" item. The form's title is
// the item's title; its intro, the stage's "show the count" switch and its questions live in the
// item's options and are saved with the plan (src/forms/model.py reads them). "Copy from another
// session" brings in a form built before, so one form serves many sessions.

import { icon } from '/static/_vendored/icons/icons.js';
import { api, esc, toast, switchRow } from '/static/js/ui.js';
import { sheetDialog, confirmDialog } from '/static/js/dialogs.js';
import { field, input, selectField } from '/static/js/views/plan-fields.js';

const TYPES = [['text', 'Free text'], ['number', 'Number'], ['scale', 'Scale 0–10'], ['stars', 'Stars 1–5'], ['nps', 'NPS 0–10'], ['choice', 'Choice']];

function newQuestionId(qs) {
  let n = qs.length + 1;
  while (qs.some((q) => q.id === `q${n}`)) n += 1;
  return `q${n}`;
}

/** The fields one question's type adds under its text: choices, a number's range, a scale's end words. */
function typeFields(q, markDirty) {
  const box = document.createElement('div');
  box.className = 'opt-list';
  const line = (label, control) => {
    const l = document.createElement('label');
    l.className = 'opt-line';
    l.append(Object.assign(document.createElement('span'), { className: 'small', textContent: label }), control);
    box.appendChild(l);
  };
  if (q.type === 'choice') {
    const text = Array.isArray(q.choices) ? q.choices.join(', ') : (q.choices || '');
    line('Choices, separated by commas', input(text, (v) => { q.choices = v.split(',').map((c) => c.trim()).filter(Boolean); markDirty(); }, { placeholder: 'Office, Remote, Hybrid', 'aria-label': 'Choices' }));
    box.appendChild(switchRow(!!q.multiple, 'Allow more than one choice', (next) => { q.multiple = next; markDirty(); }));
  } else if (q.type === 'number') {
    const num = (key, label) => line(label, input(q[key] ?? '', (v) => { q[key] = v === '' ? null : Number(v); markDirty(); }, { type: 'number', 'aria-label': label }));
    num('min', 'Lowest (optional)');
    num('max', 'Highest (optional)');
    line('Unit (optional)', input(q.unit || '', (v) => { q.unit = v; markDirty(); }, { placeholder: 'hours', 'aria-label': 'Unit' }));
  } else if (q.type === 'scale' || q.type === 'nps') {
    line('Words under 0', input(q.low || '', (v) => { q.low = v; markDirty(); }, { placeholder: q.type === 'nps' ? 'Not at all likely' : 'Not at all', 'aria-label': 'Words under the lowest' }));
    line('Words under 10', input(q.high || '', (v) => { q.high = v; markDirty(); }, { placeholder: q.type === 'nps' ? 'Extremely likely' : 'Completely', 'aria-label': 'Words under the highest' }));
  }
  return box.childElementCount ? box : null;
}

function questionCard(qs, i, ctx) {
  const q = qs[i];
  const card = document.createElement('div');
  card.className = 'fb-q';
  card.dataset.q = q.id;
  card.innerHTML = `<div class="fb-q-head"><span class="fb-q-n">${i + 1}</span><span class="fb-q-type"></span>` +
    `<span class="fb-q-tools">` +
    `<button type="button" class="icon-button" data-up data-track="plan.form-question-up" aria-label="Move question ${i + 1} up"${i === 0 ? ' disabled' : ''}>${icon('chevron-up')}</button>` +
    `<button type="button" class="icon-button" data-down data-track="plan.form-question-down" aria-label="Move question ${i + 1} down"${i === qs.length - 1 ? ' disabled' : ''}>${icon('chevron-down')}</button>` +
    `<button type="button" class="icon-button" data-delete data-track="plan.form-question-delete" aria-label="Delete question ${i + 1}">${icon('trash-2')}</button></span></div>`;
  card.querySelector('.fb-q-type').appendChild(selectField('Question type', TYPES, q.type, (v) => {
    q.type = v;
    ctx.markDirty();
    ctx.redraw();
  }, { track: 'plan.form-question-type' }));
  card.appendChild(input(q.text || '', (v) => { q.text = v; ctx.markDirty(); }, { placeholder: 'What would you like to ask?', 'aria-label': `Question ${i + 1}` }));
  const extra = typeFields(q, ctx.markDirty);
  if (extra) card.appendChild(extra);
  card.appendChild(switchRow(!!q.required, 'Required', (next) => { q.required = next; ctx.markDirty(); }, `Question ${i + 1} required`));
  const move = (to) => { qs.splice(to, 0, qs.splice(i, 1)[0]); ctx.markDirty(); ctx.redraw(); };
  card.querySelector('[data-up]').addEventListener('click', () => move(i - 1));
  card.querySelector('[data-down]').addEventListener('click', () => move(i + 1));
  card.querySelector('[data-delete]').addEventListener('click', () => { qs.splice(i, 1); ctx.markDirty(); ctx.redraw(); });
  return card;
}

/** "Copy from another session": every form in the ledger's sessions; picking one replaces this form's intro and questions. */
async function copyFrom(it, ctx) {
  let forms;
  try {
    forms = (await api('/api/forms/library')).forms.filter((f) => f.questions.length);
  } catch (e) {
    toast(e.message, 'error');
    return;
  }
  const sheet = sheetDialog({ title: 'Copy a form from another session', track: 'plan.form-library' });
  sheet.dlg.dataset.surface = 'plan.form-library';
  sheet.body.innerHTML = forms.length
    ? `<ul class="fb-library">${forms.map((f, n) => `<li><button type="button" class="button-surface fb-pick" data-pick="${n}" data-track="plan.form-library-pick">` +
      `<b>${esc(f.title || 'Untitled form')}</b><span class="small muted">${esc(f.session_title)} · ${f.questions.length} question${f.questions.length === 1 ? '' : 's'}</span></button></li>`).join('')}</ul>`
    : '<p class="muted">No session has a feedback form yet.</p>';
  sheet.body.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-pick]');
    if (!b) return;
    const f = forms[Number(b.dataset.pick)];
    const opts = it.options || {};
    if ((opts.questions || []).length) {
      const ok = await confirmDialog({ title: 'Replace this form?', message: `Its ${opts.questions.length} questions and intro are replaced by the ones of “${f.title || 'Untitled form'}”.`, actionLabel: 'Replace' });
      if (!ok) return;
    }
    it.options = Object.assign({}, opts, { intro: f.intro, questions: JSON.parse(JSON.stringify(f.questions)) });
    if (!it.title && f.title) it.title = f.title;
    sheet.dlg.close();
    ctx.markDirty();
    ctx.renderEditor();
  });
}

/** The builder's rows for the form item `it`: intro, the stage count switch, the questions and their tools. */
export function formBuilder(it, { markDirty, renderEditor }) {
  it.options = it.options || {};
  if (!Array.isArray(it.options.questions)) it.options.questions = [];
  const qs = it.options.questions;
  const wrap = document.createElement('div');
  wrap.className = 'fb';
  wrap.dataset.surface = 'plan.form-builder';
  const ctx = { markDirty, renderEditor, redraw: () => draw() };

  function draw() {
    wrap.innerHTML = '';
    wrap.appendChild(field('Intro', input(it.options.intro || '', (v) => { it.options.intro = v; markDirty(); }, { textarea: true, rows: 2, placeholder: 'Thank you for joining! Two minutes, and it helps a lot.', 'aria-label': 'Form intro' })));
    wrap.appendChild(field('On the stage', switchRow(!!it.options.show_count, 'Show how many responses came in (never the answers)', (next) => { it.options.show_count = next; markDirty(); }, 'Show the response count on the stage')));
    const list = document.createElement('div');
    list.className = 'fb-list';
    qs.forEach((_, i) => list.appendChild(questionCard(qs, i, ctx)));
    if (!qs.length) list.innerHTML = '<p class="ed-hint">No questions yet — add the first one.</p>';
    const tools = document.createElement('div');
    tools.className = 'fb-tools';
    tools.innerHTML = `<button type="button" class="button-tint" data-add data-track="plan.form-question-add">${icon('plus')} Add question</button>` +
      `<button type="button" class="button-surface" data-copy-from data-track="plan.form-copy-from" aria-haspopup="dialog">${icon('copy')} Copy from another session</button>`;
    const add = document.createElement('div');
    add.append(list, tools);
    wrap.appendChild(field(`Questions · ${qs.length}`, add));
    tools.querySelector('[data-add]').addEventListener('click', () => {
      qs.push({ id: newQuestionId(qs), type: 'text', text: '', required: false });
      markDirty();
      draw();
      const last = wrap.querySelector('.fb-q:last-child input');
      if (last) last.focus();
    });
    tools.querySelector('[data-copy-from]').addEventListener('click', () => copyFrom(it, ctx));
  }

  draw();
  return wrap;
}

