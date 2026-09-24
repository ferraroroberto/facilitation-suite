// Editor dialogs (vendored modal contract), a confirm dialog and a row menu.
// Never window.confirm/prompt: native browser dialogs block the page.

import { icon } from '/static/_vendored/icons/icons.js';
import { esc } from '/static/js/ui.js';

/**
 * An editor <dialog>: fields = [{name, label, type, value, placeholder, hint, options}].
 * Resolves with {name: value} on Save, or null when dismissed (Escape, ×, backdrop).
 */
export function formDialog({ title, fields, saveLabel = 'Save', wide = false }) {
  return new Promise((resolve) => {
    const dlg = document.createElement('dialog');
    dlg.className = 'detail-dialog' + (wide ? ' dialog-wide' : '');
    const rows = fields.map((f) => {
      const id = 'f-' + f.name;
      let control;
      if (f.type === 'select') {
        control = `<select class="select-native" id="${id}" name="${f.name}">` +
          f.options.map((o) => `<option value="${esc(o.value)}"${String(o.value) === String(f.value) ? ' selected' : ''}>${esc(o.label)}</option>`).join('') +
          '</select>';
      } else if (f.type === 'textarea') {
        control = `<textarea class="input" id="${id}" name="${f.name}" rows="3" placeholder="${esc(f.placeholder || '')}">${esc(f.value || '')}</textarea>`;
      } else {
        control = `<input class="input-native" id="${id}" name="${f.name}" type="${f.type || 'text'}" value="${esc(f.value == null ? '' : f.value)}" placeholder="${esc(f.placeholder || '')}"${f.required ? ' required' : ''}>`;
      }
      return `<label class="row"><span>${esc(f.label)}</span>${control}</label>` +
        (f.hint ? `<p class="dialog-hint">${esc(f.hint)}</p>` : '');
    }).join('');
    dlg.innerHTML =
      `<form class="detail-card" method="dialog">` +
      `<div class="detail-header"><h2>${esc(title)}</h2>` +
      `<button type="button" class="detail-close" aria-label="Close" data-close>${icon('x')}</button></div>` +
      rows +
      `<div class="detail-actions"><button type="submit" class="detail-save-btn">${esc(saveLabel)}</button></div>` +
      `</form>`;
    document.body.appendChild(dlg);
    const form = dlg.querySelector('form');
    const save = dlg.querySelector('.detail-save-btn');
    const validate = () => { save.disabled = !form.checkValidity(); };
    form.addEventListener('input', validate);
    validate();
    let result = null;
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      result = {};
      fields.forEach((f) => { result[f.name] = form.elements[f.name].value; });
      dlg.close();
    });
    dlg.querySelector('[data-close]').addEventListener('click', () => dlg.close());
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener('close', () => { dlg.remove(); resolve(result); });
    dlg.showModal();
    const first = form.querySelector('input, select, textarea');
    if (first) first.focus();
  });
}

/** Confirm dialog; resolves true/false. `danger` styles the action destructive. */
export function confirmDialog({ title, message, actionLabel = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const dlg = document.createElement('dialog');
    dlg.className = 'detail-dialog';
    dlg.innerHTML =
      `<div class="detail-card">` +
      `<div class="detail-header"><h2>${esc(title)}</h2>` +
      `<button type="button" class="detail-close" aria-label="Close" data-close>${icon('x')}</button></div>` +
      `<p class="dialog-message">${esc(message)}</p>` +
      `<div class="detail-actions"><button type="button" class="${danger ? 'button-tint danger' : 'detail-save-btn'} dialog-confirm" data-ok>${esc(actionLabel)}</button></div>` +
      `</div>`;
    document.body.appendChild(dlg);
    let ok = false;
    dlg.querySelector('[data-ok]').addEventListener('click', () => { ok = true; dlg.close(); });
    dlg.querySelector('[data-close]').addEventListener('click', () => dlg.close());
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener('close', () => { dlg.remove(); resolve(ok); });
    dlg.showModal();
  });
}

let openMenu = null;
function closeMenu() {
  if (openMenu) { openMenu.remove(); openMenu = null; }
}
document.addEventListener('click', (e) => { if (openMenu && !openMenu.contains(e.target)) closeMenu(); }, true);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

/**
 * A floating row menu anchored to `anchor`: items = [{label, icon, onClick, danger}].
 * A destructive item goes last, after a divider (design.md action-row).
 */
export function rowMenu(anchor, items) {
  closeMenu();
  const menu = document.createElement('div');
  menu.className = 'row-menu';
  menu.setAttribute('role', 'menu');
  items.forEach((it) => {
    if (it.danger) {
      const hr = document.createElement('div');
      hr.className = 'row-menu-divider';
      menu.appendChild(hr);
    }
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('role', 'menuitem');
    b.className = 'row-menu-item' + (it.danger ? ' danger' : '');
    b.innerHTML = (it.icon ? icon(it.icon) : '') + `<span>${esc(it.label)}</span>`;
    b.addEventListener('click', (e) => { e.stopPropagation(); closeMenu(); it.onClick(); });
    menu.appendChild(b);
  });
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  const w = 220;
  menu.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + 'px';
  menu.style.top = (r.bottom + 4 + window.scrollY) + 'px';
  openMenu = menu;
  const first = menu.querySelector('button');
  if (first) first.focus();
}
